"""The circuit breaker, retries, latency metrics and the key resolver's stampede and outage handling."""

import asyncio

import httpx
import pytest

from app.config import GatewaySettings
from app.keys import KeyRejected, KeyResolver
from app.proxy.metrics import Latency
from app.proxy.upstream import Breaker, CircuitOpen, Upstreams
from pawabase_core.clients import ServiceError


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def settings(**kw):
    return GatewaySettings(_env_file=None, app_env="testing", **kw)


def pool(handler, **kw):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://svc")
    clock = Clock()
    return Upstreams({"api": client}, settings(**kw), clock=clock), clock


def build(upstreams, method="GET"):
    return upstreams.client("api").build_request(method, "/x")


def test_a_breaker_opens_after_consecutive_failures_and_lets_one_probe_through():
    clock = Clock()
    breaker = Breaker(3, 10, clock)
    for _ in range(2):
        breaker.failure()
    assert breaker.state == "closed" and breaker.before() is None
    breaker.failure()
    assert breaker.state == "open" and breaker.before() == pytest.approx(10)
    clock.now = 10
    assert breaker.state == "half-open"
    assert breaker.before() is None  # the probe
    assert breaker.before() is not None  # everyone else keeps waiting while it is out
    breaker.failure()  # the probe failed: closed again for another cooldown
    assert breaker.state == "open"
    clock.now = 25
    assert breaker.before() is None
    breaker.success()
    assert breaker.state == "closed" and breaker.consecutive == 0


def test_a_success_in_between_resets_the_count():
    breaker = Breaker(3, 10, Clock())
    breaker.failure(), breaker.failure(), breaker.success(), breaker.failure(), breaker.failure()
    assert breaker.state == "closed"


async def test_an_unreachable_service_is_retried_once_for_a_get_and_then_fails_fast():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("refused")

    upstreams, _ = pool(handler, breaker_failures=2, upstream_retries=1)
    with pytest.raises(httpx.ConnectError):
        await upstreams.send("api", build(upstreams), repeatable=True)
    assert len(calls) == 2  # the retry
    with pytest.raises(httpx.ConnectError):
        await upstreams.send("api", build(upstreams), repeatable=True)
    with pytest.raises(CircuitOpen):  # open now: no more attempts at all
        await upstreams.send("api", build(upstreams), repeatable=True)
    assert len(calls) == 4


async def test_a_request_with_a_body_or_a_non_idempotent_method_is_never_repeated():
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx.ConnectError("refused")

    upstreams, _ = pool(handler, breaker_failures=0)
    with pytest.raises(httpx.ConnectError):
        await upstreams.send("api", build(upstreams, "POST"), repeatable=True)
    with pytest.raises(httpx.ConnectError):
        await upstreams.send("api", build(upstreams, "GET"), repeatable=False)
    assert calls == ["POST", "GET"]


async def test_a_gateway_error_from_the_service_counts_against_it_but_is_passed_on():
    upstreams, _ = pool(lambda request: httpx.Response(503), breaker_failures=2)
    for _ in range(2):
        response = await upstreams.send("api", build(upstreams), repeatable=True)
        assert response.status_code == 503
        await response.aclose()
    with pytest.raises(CircuitOpen):
        await upstreams.send("api", build(upstreams), repeatable=True)
    assert upstreams.snapshot()["api"]["state"] == "open"


async def test_an_application_error_does_not_trip_the_breaker():
    upstreams, _ = pool(lambda request: httpx.Response(500), breaker_failures=2)
    for _ in range(5):
        response = await upstreams.send("api", build(upstreams), repeatable=True)
        await response.aclose()
    assert upstreams.snapshot()["api"]["state"] == "closed"


async def test_the_breaker_closes_again_when_the_service_comes_back():
    state = {"up": False}

    def handler(request):
        if not state["up"]:
            raise httpx.ConnectError("refused")
        return httpx.Response(200)

    upstreams, clock = pool(handler, breaker_failures=1, breaker_cooldown=10, upstream_retries=0)
    with pytest.raises(httpx.ConnectError):
        await upstreams.send("api", build(upstreams), repeatable=True)
    clock.now = 11
    state["up"] = True
    response = await upstreams.send("api", build(upstreams), repeatable=True)
    await response.aclose()
    assert upstreams.snapshot()["api"] == {"state": "closed", "consecutive_failures": 0}


def test_latency_is_bucketed_per_service():
    latency = Latency()
    for ms, status in ((10, 200), (40, 200), (700, 500), (9000, 200)):
        latency.record("api", ms, status)
    snap = latency.snapshot()["api"]
    assert snap["requests"] == 4 and snap["max_ms"] == 9000
    assert (
        snap["histogram"]["<=25"] == 1
        and snap["histogram"]["<=50"] == 1
        and snap["histogram"]["<=1000"] == 1
        and snap["histogram"][">5000"] == 1
    )
    assert snap["statuses"] == {"2xx": 3, "5xx": 1}


class FakeKeysApi:
    def __init__(self) -> None:
        self.calls = 0
        self.mode = "ok"

    async def post(self, path, json):
        self.calls += 1
        await asyncio.sleep(0.01)
        if self.mode == "down":
            raise ServiceError(503, {"detail": "down"})
        if self.mode == "revoked":
            raise ServiceError(401, {"detail": "invalid API key"})
        return {"env": "main", "role": "anon", "key_id": "k", "scopes": []}


async def test_a_stampede_for_one_key_costs_one_lookup():
    api = FakeKeysApi()
    resolver = KeyResolver(api, ttl=30)
    results = await asyncio.gather(*(resolver.resolve("pk_abc") for _ in range(40)))
    assert api.calls == 1 and {r[0].key_id for r in results} == {"k"}


async def test_when_the_api_is_down_the_last_answer_serves_for_the_stale_window_only():
    clock = Clock()
    api = FakeKeysApi()
    from sillo.cache import MemoryCache

    resolver = KeyResolver(api, cache=MemoryCache(namespace="t"), ttl=0, stale_ttl=60, clock=clock)
    await resolver.resolve("pk_abc")
    api.mode = "down"
    clock.now = 30
    context, _ = await resolver.resolve("pk_abc")
    assert context.env == "main" and resolver.stale_served == 1
    clock.now = 500
    with pytest.raises(ServiceError):
        await resolver.resolve("pk_abc")


async def test_an_outage_is_never_remembered_as_a_bad_key_and_a_revocation_ends_staleness():
    api = FakeKeysApi()
    resolver = KeyResolver(api, ttl=30, stale_ttl=60)
    api.mode = "down"
    with pytest.raises(ServiceError):
        await resolver.resolve("pk_new")
    api.mode = "ok"
    assert (await resolver.resolve("pk_new"))[0].env == "main"  # not cached as rejected
    api.mode = "revoked"
    with pytest.raises(KeyRejected):
        await resolver.resolve("pk_other")
