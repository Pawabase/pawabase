"""Flows, functions, custom routes, events, webhooks, schedules and jobs."""

import hashlib
import hmac
import json
import textwrap

import httpx
import pytest

ENV = "/platform/v1/projects/acme/envs/development"

ORDERS = {
    "name": "orders",
    "fields": [
        {"name": "total", "type": "number", "required": True},
        {"name": "status", "type": "string", "default": "new"},
    ],
    "operations": {
        "create": {"enabled": True, "policy": "authenticated"},
        "get": {"enabled": True, "policy": "public"},
    },
}


def node(node_id, block, **config):
    return {"id": node_id, "data": {"block": block, "config": config}, "position": {"x": 0, "y": 0}}


def edge(source, target, handle=None):
    return {
        "id": f"{source}-{target}",
        "source": source,
        "target": target,
        **({"sourceHandle": handle} if handle else {}),
    }


PAY_FLOW = {
    "name": "pay-order",
    "definition": {
        "nodes": [
            node("start", "trigger.http", method="POST", path="/orders/{id}/pay"),
            node("load", "resource.get", resource="orders", id="{{ input.params.id }}"),
            node(
                "paid",
                "resource.update",
                resource="orders",
                id="{{ input.params.id }}",
                data={"status": "paid"},
            ),
            node(
                "event",
                "event.emit",
                event="order.paid",
                payload={
                    "id": "{{ steps.load.output.id }}",
                    "total": "{{ steps.load.output.total }}",
                },
            ),
            node(
                "reply",
                "response.return",
                status=200,
                body={"paid": True, "order": "{{ steps.paid.output }}", "by": "{{ auth.user_id }}"},
            ),
            node(
                "missing",
                "error.raise",
                status=404,
                message="no such order",
                code="order_not_found",
            ),
        ],
        "edges": [
            edge("start", "load"),
            edge("load", "paid"),
            edge("load", "missing", "missing"),
            edge("paid", "event"),
            edge("event", "reply"),
        ],
    },
}

NOTIFY_FLOW = {
    "name": "on-order-paid",
    "definition": {
        "nodes": [
            node("start", "trigger.event", event="order.paid"),
            node(
                "store", "cache.set", key="last-paid", value="{{ input.event.payload.id }}", ttl=60
            ),
            node("metric", "metric.increment", name="orders.paid"),
        ],
        "edges": [edge("start", "store"), edge("store", "metric")],
    },
}

FUNCTION_CODE = textwrap.dedent(
    '''
    from pawabase_kit.functions import function

    @function("quote", input_fields=[{"name": "items", "type": "integer", "required": True, "minimum": 1}])
    async def quote(ctx):
        """Price a quote."""
        return {"total": ctx.input["items"] * 5, "user": ctx.auth.get("user_id")}

    @function("tally", policy="public")
    async def tally(ctx):
        orders = await ctx.runtime.resource_list("orders")
        return {"orders": orders["total"]}
    '''
)

ROUTES_CODE = textwrap.dedent(
    """
    from sillo import HttpContext, Router

    router = Router(prefix="/x/acme")

    @router.get("/hello")
    async def hello(ctx: HttpContext):
        return {"hello": "from normal Sillo code"}
    """
)


@pytest.fixture
async def acme(api, settings, tmp_path):
    code = tmp_path / "code" / "acme" / "functions"
    code.mkdir(parents=True)
    (code / "billing.py").write_text(FUNCTION_CODE)
    (tmp_path / "code" / "acme" / "routes.py").write_text(ROUTES_CODE)
    await api.studio.post("/platform/v1/projects", json={"ref": "acme", "name": "Acme"})
    await api.studio.post(f"{ENV}/resources", json=ORDERS)
    await api.studio.post(f"{ENV}/resources/orders/migrate")
    for flow in (PAY_FLOW, NOTIFY_FLOW):
        await api.studio.post(f"{ENV}/flows", json=flow)
    await api.studio.post(
        f"{ENV}/routes",
        json={
            "method": "POST",
            "path": "/orders/{id}/pay",
            "handler": "pay-order",
            "policy": "authenticated",
            "name": "Pay order",
        },
    )
    await api.studio.post(
        f"{ENV}/routes",
        json={
            "method": "POST",
            "path": "/quotes",
            "handler_type": "function",
            "handler": "quote",
            "policy": "public",
            "input_fields": [{"name": "items", "type": "integer", "required": True}],
        },
    )
    return api


async def test_custom_route_flow_and_event_consumer(acme):
    api = acme
    ada = api.user_headers("acme", "development", user_id="7")
    order = (await api.http.post("/rest/v1/orders", json={"total": 42.5}, headers=ada)).json()
    paid = await api.http.post(f"/rest/v1/orders/{order['id']}/pay", headers=ada)
    assert paid.status_code == 200, paid.text
    assert (
        paid.json()["paid"] is True
        and paid.json()["order"]["status"] == "paid"
        and paid.json()["by"] == "7"
    )

    missing = await api.http.post("/rest/v1/orders/999/pay", headers=ada)
    assert missing.status_code == 404 and missing.json()["error"] == "order_not_found"
    assert (
        await api.http.post(
            f"/rest/v1/orders/{order['id']}/pay", headers=api.context_headers("acme", "development")
        )
    ).status_code == 401

    await api.drain()
    state = await api.platform.state("acme", "development")
    assert await api.platform.cache_get(state, "user:last-paid") == order["id"]
    events = (await api.studio.get(f"{ENV}/events?name=order.paid"))["data"]
    assert events and events[0]["consumers"][0] == {
        "type": "flow",
        "target": "on-order-paid",
        "status": "queued",
        "ref": events[0]["consumers"][0]["ref"],
    }
    runs = (await api.studio.get(f"{ENV}/flow-runs"))["data"]
    assert {run["flow"] for run in runs} == {"pay-order", "on-order-paid"}
    graph = (await api.studio.get(f"{ENV}/events-graph"))["data"]
    paid_edge = next(item for item in graph if item["event"] == "order.paid")
    assert (
        "flow:pay-order" in paid_edge["producers"]
        and paid_edge["consumers"][0]["target"] == "on-order-paid"
    )
    metrics = (await api.studio.get(f"{ENV}/metrics?name=orders.paid"))["data"]
    assert metrics and metrics[0]["value"] == 1


async def test_functions_and_python_routes(acme):
    api = acme
    anon = api.context_headers("acme", "development")
    quote = await api.http.post("/rest/v1/quotes", json={"items": 3}, headers=anon)
    assert quote.status_code == 200 and quote.json()["total"] == 15
    assert (
        await api.http.post("/rest/v1/quotes", json={"items": "x"}, headers=anon)
    ).status_code == 422

    # /functions/v1 applies the function's own policy (quote requires sign-in).
    assert (
        await api.http.post("/functions/v1/quote", json={"items": 2}, headers=anon)
    ).status_code == 401
    signed_in = await api.http.post(
        "/functions/v1/quote",
        json={"items": 2},
        headers=api.user_headers("acme", "development", user_id="9"),
    )
    assert signed_in.json() == {"data": {"total": 10, "user": "9"}}
    tally = await api.http.post("/functions/v1/tally", headers=anon)
    assert tally.json() == {"data": {"orders": 0}}

    hello = await api.http.get("/rest/v1/x/acme/hello", headers=anon)
    assert hello.json() == {"hello": "from normal Sillo code"}
    listing = await api.studio.get(f"{ENV}/functions")
    assert {f["name"] for f in listing["data"]} == {"quote", "tally"} and listing["router"] is True


async def test_manual_run_and_validation(acme):
    api = acme
    order = await api.studio.post(f"{ENV}/resources/orders/records", json={"total": 10})
    run = await api.studio.post(
        f"{ENV}/flows/pay-order/run", json={"input": {"params": {"id": str(order["id"])}}}
    )
    assert run["status"] == "succeeded" and run["response"]["status"] == 200
    assert [step["node"] for step in run["trace"]] == ["start", "load", "paid", "event", "reply"]
    broken = {"name": "broken", "definition": {"nodes": [node("a", "nope")], "edges": []}}
    from pawabase_kit.clients import ServiceError

    with pytest.raises(ServiceError) as refused:
        await api.studio.post(f"{ENV}/flows", json=broken)
    assert refused.value.status == 422 and refused.value.body["problems"]
    blocks = await api.studio.get("/platform/v1/blocks")
    assert blocks["count"] >= 50


async def test_outbound_webhooks_are_signed_and_retried(acme):
    api = acme
    received = []
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        received.append(request)
        return httpx.Response(200, json={"ok": True})

    api.platform.outbound = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    hook = await api.studio.post(
        f"{ENV}/webhooks",
        json={"name": "erp", "url": "https://erp.example.com/hooks", "events": ["orders.*"]},
    )
    secret = hook["secret"]
    await api.studio.post(f"{ENV}/resources/orders/records", json={"total": 5})
    await api.drain(timeout=15)
    assert received, "the endpoint never got a delivery"
    request = received[0]
    body = request.content
    signature = dict(
        part.split("=", 1) for part in request.headers["pawabase-signature"].split(",")
    )
    expected = hmac.new(
        secret.encode(), f"{signature['t']}.".encode() + body, hashlib.sha256
    ).hexdigest()
    assert signature["v1"] == expected
    assert json.loads(body)["event"] == "orders.created"
    deliveries = (await api.studio.get(f"{ENV}/webhook-deliveries"))["data"]
    assert deliveries[0]["status"] == "delivered" and deliveries[0]["attempts"] == 2


async def test_inbound_hooks_verify_signatures(acme):
    api = acme
    hook = await api.studio.post(
        f"{ENV}/inbound-hooks", json={"slug": "stripe", "target": "payment.completed"}
    )
    secret = hook["secret"]
    body = json.dumps({"amount": 100}).encode()
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert (
        await api.http.post(
            "/hooks/v1/acme/development/stripe", content=body, headers={"x-signature": "bad"}
        )
    ).status_code == 401
    ok = await api.http.post(
        "/hooks/v1/acme/development/stripe",
        content=body,
        headers={"x-signature": f"sha256={good}", "content-type": "application/json"},
    )
    assert ok.status_code == 202
    await api.drain()
    events = (await api.studio.get(f"{ENV}/events?name=payment.completed"))["data"]
    assert events[0]["payload"]["body"] == {"amount": 100}


async def test_storage_with_policies_and_signed_urls(acme):
    api = acme
    await api.studio.post(
        f"{ENV}/buckets",
        json={
            "name": "avatars",
            "read_policy": "public",
            "write_policy": {"eq": ["$object.segments.0", "$auth.user_id"]},
            "accepts": ["image/png"],
        },
    )
    ada = api.user_headers("acme", "development", user_id="7")
    anon = api.context_headers("acme", "development")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    assert (
        await api.http.put(
            "/storage/v1/object/avatars/8/me.png",
            content=png,
            headers={**ada, "content-type": "image/png"},
        )
    ).status_code == 403
    uploaded = await api.http.put(
        "/storage/v1/object/avatars/7/me.png",
        content=png,
        headers={**ada, "content-type": "image/png"},
    )
    assert uploaded.status_code == 201 and uploaded.json()["content_type"] == "image/png"
    html = await api.http.put(
        "/storage/v1/object/avatars/7/evil.png",
        content=b"<html><script>alert(1)</script>",
        headers={**ada, "content-type": "image/png"},
    )
    assert html.status_code == 400  # sniffed as HTML, refused by the bucket's accepts
    download = await api.http.get("/storage/v1/object/avatars/7/me.png", headers=anon)
    assert (
        download.status_code == 200
        and download.content == png
        and download.headers["x-content-type-options"] == "nosniff"
    )
    signed = (
        await api.http.post(
            "/storage/v1/sign/avatars/7/me.png", json={"method": "GET"}, headers=ada
        )
    ).json()
    path = signed["url"].replace(api.settings.public_url, "")
    fetched = await api.http.get(path)
    assert fetched.status_code == 200 and fetched.content == png
    assert (await api.http.get(path.replace("token=", "token=x"))).status_code == 404
    listing = (await api.studio.get(f"{ENV}/buckets/avatars/objects?prefix=7/"))["files"]
    assert [f["key"] for f in listing] == ["7/me.png"]
    await api.drain()
    uploads = (await api.studio.get(f"{ENV}/events?name=file.uploaded"))["data"]
    assert uploads[0]["payload"]["key"] == "7/me.png"


async def test_schedules_and_jobs(acme):
    api = acme
    await api.studio.post(
        f"{ENV}/schedules",
        json={
            "name": "nightly",
            "cron": "0 3 * * *",
            "target_type": "event",
            "target": "reports.due",
            "payload": {"kind": "daily"},
        },
    )
    from pawabase_kit.clients import ServiceError

    with pytest.raises(ServiceError):
        await api.studio.post(
            f"{ENV}/schedules",
            json={"name": "bad", "cron": "nope", "target_type": "event", "target": "x"},
        )
    fired = await api.studio.post(f"{ENV}/schedules/nightly/run")
    assert fired["last_status"] == "emitted" and fired["run_count"] == 1
    await api.studio.post(
        f"{ENV}/schedules",
        json={
            "name": "tally",
            "interval_seconds": 60,
            "target_type": "function",
            "target": "tally",
        },
    )
    await api.studio.post(f"{ENV}/schedules/tally/run")
    await api.drain()
    jobs = (await api.studio.get(f"{ENV}/jobs?job=RunFunctionJob"))["data"]
    assert (
        jobs[0]["status"] == "completed"
        and jobs[0]["result"] == {"orders": 0}
        and jobs[0]["source"] == "schedule"
    )
    queues = (await api.studio.get(f"{ENV}/queues"))["data"]
    assert next(q for q in queues if q["name"] == "functions")["jobs"]["completed"] == 1
    workers = (await api.studio.get("/platform/v1/workers"))["data"]
    assert workers[0]["name"].startswith("inline")


async def test_internal_key_resolution_and_config(acme, settings):
    from pawabase_kit.clients import ServiceClient, ServiceError

    api = acme
    created = await api.studio.post(
        f"{ENV}/keys", json={"name": "ci", "role": "secret", "scopes": ["resource:read"]}
    )
    gateway = ServiceClient(
        "http://x", secret=settings.internal_secret, issuer="gateway", audience="api", app=api.app
    )
    resolved = await gateway.post("/internal/v1/keys/resolve", json={"key": created["key"]})
    assert resolved == {
        **resolved,
        "project": "acme",
        "env": "development",
        "role": "service",
        "scopes": ["resource:read"],
    }
    await api.studio.post(f"{ENV}/keys/{created['id']}/revoke")
    with pytest.raises(ServiceError) as refused:
        await gateway.post("/internal/v1/keys/resolve", json={"key": created["key"]})
    assert refused.value.status == 401
    await api.studio.put(f"{ENV}/secrets/GOOGLE_SECRET", json={"value": "s3cr3t-value-123"})
    await api.studio.patch(
        ENV,
        json={
            "auth": {
                "providers": {
                    "google": {"client_id": "abc", "client_secret": "secret://GOOGLE_SECRET"}
                }
            }
        },
    )
    config = await gateway.get("/internal/v1/environments/acme/development/auth")
    assert config["auth"]["providers"]["google"]["client_secret"] == "s3cr3t-value-123"
    listing = (await api.studio.get(f"{ENV}/secrets"))["data"]
    assert listing[0]["name"] == "GOOGLE_SECRET" and "s3cr3t" not in json.dumps(listing)
    await gateway.close()
