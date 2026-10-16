"""Flows, functions, custom routes, events, webhooks, schedules and jobs."""

import hashlib
import hmac
import json
import textwrap

import httpx
import pytest

from pawabase_server.infra.storage.manager import MimePatterns

ENV = "/platform/v1/envs/development"

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
                "log",
                "log.write",
                level="info",
                category="billing",
                code="order_paid",
                message="Order payment completed",
                tags={"operation": "pay"},
                data={
                    "order_id": "{{ steps.load.output.id }}",
                    "password": "must-not-appear",
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
            edge("paid", "log"),
            edge("log", "event"),
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
    from pawabase_server.functions.registry import function

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
    code = tmp_path / "code" / "functions"
    code.mkdir(parents=True)
    (code / "billing.py").write_text(FUNCTION_CODE)
    (tmp_path / "code" / "routes.py").write_text(ROUTES_CODE)
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
    ada = api.user_headers("development", user_id="7")
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
            f"/rest/v1/orders/{order['id']}/pay", headers=api.context_headers("development")
        )
    ).status_code == 401

    await api.drain()
    await api.app.state["request_rollup"].flush()
    state = await api.platform.state("development")
    assert await api.platform.cache_get(state, "user:last-paid") == order["id"]
    events = (await api.studio.get(f"{ENV}/events?name=order.paid"))["data"]
    consumer = events[0]["consumers"][0] if events else {}
    assert {k: consumer.get(k) for k in ("type", "target", "status")} == {
        "type": "flow",
        "target": "on-order-paid",
        "status": "queued",
    }
    assert consumer["ref"] and consumer["node"]  # which trigger node of the flow the event entered
    assert events[0]["routed_at"]
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

    request_id = paid.headers["x-request-id"]
    request_rows = (await api.studio.get(f"{ENV}/requests", params={"request_id": request_id}))[
        "data"
    ]
    assert request_rows and request_rows[0]["user"] == "7"
    detail = await api.studio.get(f"{ENV}/requests/{request_id}")
    assert {run["flow"] for run in detail["flow_runs"]} == {"pay-order", "on-order-paid"}
    assert "order.paid" in {event["name"] for event in detail["events"]}
    assert detail["jobs"] and detail["summary"]["logs"] == 1
    assert detail["logs"][0]["code"] == "order_paid"
    assert detail["logs"][0]["data"]["password"] == "[redacted]"


async def test_functions_and_python_routes(acme):
    api = acme
    anon = api.context_headers("development")
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
        headers=api.user_headers("development", user_id="9"),
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
    assert [step["node"] for step in run["trace"]] == [
        "start",
        "load",
        "paid",
        "log",
        "event",
        "reply",
    ]
    broken = {"name": "broken", "definition": {"nodes": [node("a", "nope")], "edges": []}}
    from pawabase_server.core.clients import ServiceError

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
            "/hooks/v1/development/stripe", content=body, headers={"x-signature": "bad"}
        )
    ).status_code == 401
    ok = await api.http.post(
        "/hooks/v1/development/stripe",
        content=body,
        headers={"x-signature": f"sha256={good}", "content-type": "application/json"},
    )
    assert ok.status_code == 202
    await api.drain()
    events = (await api.studio.get(f"{ENV}/events?name=payment.completed"))["data"]
    assert events[0]["payload"]["body"] == {"amount": 100}


async def test_inbound_hooks_verify_paystack_sha512_signatures(acme):
    api = acme
    hook = await api.studio.post(
        f"{ENV}/inbound-hooks",
        json={
            "slug": "paystack",
            "target": "payment.completed",
            "verification": "hmac-sha512",
            "signature_header": "x-paystack-signature",
        },
    )
    secret = hook["secret"]
    body = json.dumps({"event": "charge.success", "data": {"reference": "PAY-1"}}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    response = await api.http.post(
        "/hooks/v1/development/paystack",
        content=body,
        headers={"x-paystack-signature": signature, "content-type": "application/json"},
    )
    assert response.status_code == 202
    await api.drain()
    events = (await api.studio.get(f"{ENV}/events?name=payment.completed"))["data"]
    assert events[0]["payload"]["body"]["event"] == "charge.success"


async def test_inbound_hooks_verify_timestamped_signatures(acme):
    import time

    api = acme
    hook = await api.studio.post(
        f"{ENV}/inbound-hooks",
        json={
            "slug": "bachs",
            "target": "payment.completed",
            "verification": "hmac-sha256-ts",
            "signature_header": "x-bachs-signature-v2",
        },
    )
    secret = hook["secret"]
    body = json.dumps({"type": "collection.succeeded", "data": {"checkout_id": "chk_1"}}).encode()

    def sign(stamp, key=secret):
        return hmac.new(key.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()

    async def post(headers):
        return await api.http.post(
            "/hooks/v1/development/bachs",
            content=body,
            headers={"content-type": "application/json", **headers},
        )

    now = int(time.time())
    # The t=…,v1=… form, with a second v1 as during a secret rotation.
    header = f"t={now},v1={sign(now, 'the-old-secret')},v1={sign(now)}"
    assert (await post({"x-bachs-signature-v2": header})).status_code == 202
    # A stale delivery, a wrong secret, and a body signed without the timestamp are all refused.
    old = now - 3600
    assert (await post({"x-bachs-signature-v2": f"t={old},v1={sign(old)}"})).status_code == 401
    assert (
        await post({"x-bachs-signature-v2": f"t={now},v1={sign(now, 'nope')}"})
    ).status_code == 401
    bare = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert (await post({"x-bachs-signature-v2": f"t={now},v1={bare}"})).status_code == 401


async def test_storage_with_policies_and_signed_urls(acme):
    api = acme
    await api.studio.post(
        f"{ENV}/buckets",
        json={
            "name": "avatars",
            "read_policy": "public",
            "write_policy": {"eq": ["$object.segments.0", "$auth.user_id"]},
            "accepts": ["image/png"],
            "signed_downloads": True,
        },
    )
    ada = api.user_headers("development", user_id="7")
    anon = api.context_headers("development")
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


async def test_signed_storage_stays_behind_pawabase_and_enforces_bucket_rules(acme):
    api = acme
    body = {
        "name": "audio",
        "public": False,
        "read_policy": "authenticated",
        "write_policy": "authenticated",
        "accepts": ["audio/*"],
        "max_bytes": 1024,
        "signed_uploads": False,
        "signed_downloads": False,
    }
    await api.studio.post(f"{ENV}/buckets", json=body)
    ada = api.user_headers("development", user_id="7")

    # Disabled means disabled for both application callers and Studio, with a useful status instead of a 500.
    assert (
        await api.http.post(
            "/storage/v1/sign/audio/song.mp3", json={"method": "PUT"}, headers=ada
        )
    ).status_code == 403
    from pawabase_server.core.clients import ServiceError

    with pytest.raises(ServiceError) as refused:
        await api.studio.post(
            f"{ENV}/buckets/audio/sign",
            json={"key": "song.mp3", "method": "PUT"},
        )
    assert refused.value.status == 403

    body.update({"signed_uploads": True, "signed_downloads": True})
    await api.studio.put(f"{ENV}/buckets/audio", json=body)

    grant = await api.studio.post(
        f"{ENV}/buckets/audio/sign",
        json={
            "key": "song.mp3",
            "method": "PUT",
            "content_type": "audio/mpeg",
            "max_bytes": 512,
        },
    )
    assert grant["url"].startswith(f"{api.settings.public_url}/storage/v1/signed/")
    path = grant["url"].replace(api.settings.public_url, "")

    # A DOCX/ZIP body cannot become audio merely by claiming audio/mpeg.
    disguised = await api.http.put(
        path,
        content=b"PK\x03\x04" + b"\x00" * 64,
        headers={"content-type": "audio/mpeg"},
    )
    assert disguised.status_code == 400

    uploaded = await api.http.put(
        path,
        content=b"ID3" + b"\x00" * 64,
        headers={"content-type": "audio/mpeg"},
    )
    assert uploaded.status_code == 201

    read = await api.studio.post(
        f"{ENV}/buckets/audio/sign",
        json={"key": "song.mp3", "method": "GET", "expires_in": 3600},
    )
    assert read["url"].startswith(f"{api.settings.public_url}/storage/v1/signed/")
    read_path = read["url"].replace(api.settings.public_url, "")
    assert (await api.http.get(read_path)).status_code == 200

    # Turning signed downloads off revokes even a previously issued bearer link immediately.
    body["signed_downloads"] = False
    await api.studio.put(f"{ENV}/buckets/audio", json=body)
    assert (await api.http.get(read_path)).status_code == 404


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
    from pawabase_server.core.clients import ServiceError

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
    from pawabase_server.edge.key_resolver import KeyRejected, lookup_key

    api = acme
    created = await api.studio.post(
        f"{ENV}/keys", json={"name": "ci", "role": "secret", "scopes": ["resource:read"]}
    )
    resolved = await lookup_key(created["key"])
    assert resolved == {
        **resolved,
        "env": "development",
        "role": "service",
        "scopes": ["resource:read"],
    }
    with pytest.raises(KeyRejected, match="environment"):
        await lookup_key(created["key"], "production")
    assert (await lookup_key(created["key"], "development"))["env"] == "development"
    await api.studio.post(f"{ENV}/keys/{created['id']}/revoke")
    with pytest.raises(KeyRejected):
        await lookup_key(created["key"])
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
    config = await api.platform.auth_settings("development")
    assert config["auth"]["providers"]["google"]["client_secret"] == "s3cr3t-value-123"
    listing = (await api.studio.get(f"{ENV}/secrets"))["data"]
    assert listing[0]["name"] == "GOOGLE_SECRET" and "s3cr3t" not in json.dumps(listing)


async def test_dispatch_tolerates_a_worker_recording_the_job_first(api):
    """With a separate worker, a job can be picked up and recorded between
    the push and the dispatcher's own record. Neither side may fail, and the
    worker's status must survive."""
    from pawabase_server.automation.jobs.flows import RunFlowJob
    from pawabase_server.db.models import JobRun

    platform = api.platform
    push = platform.queue.push

    async def racing_push(queue, payload, delay=0):
        job_id = await push(queue, payload, delay=delay)
        await JobRun.create(
            id=job_id, project="p", env="e", queue=queue, job="RunFlowJob", status="active"
        )
        return job_id

    platform.queue.push = racing_push
    try:
        job_id = await platform.dispatch(
            RunFlowJob, project="p", env="e", flow="f", target="f", source="event"
        )
    finally:
        platform.queue.push = push
    row = await JobRun.get(id=job_id)
    assert (row.status, row.target, row.source) == ("active", "f", "event")


async def test_bucket_accepts_wildcard_mime_patterns(acme):
    # Regression: "image/*" was compared to "image/png" by equality, so a
    # wildcard bucket refused every upload with a 400.
    api = acme
    await api.studio.post(
        f"{ENV}/buckets",
        json={
            "name": "photos",
            "write_policy": "authenticated",
            "accepts": ["image/*", "application/pdf"],
        },
    )
    ada = api.user_headers("development", user_id="7")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    raw = await api.http.put(
        "/storage/v1/object/photos/a.png", content=png, headers={**ada, "content-type": "image/png"}
    )
    assert raw.status_code == 201, raw.text
    multipart = await api.http.post(
        "/storage/v1/object/photos/b.png", files={"file": ("b.png", png, "image/png")}, headers=ada
    )
    assert multipart.status_code == 201, multipart.text
    disguised = await api.http.put(
        "/storage/v1/object/photos/evil.png",
        content=b"<html><script>alert(1)</script>",
        headers={**ada, "content-type": "image/png"},
    )
    assert disguised.status_code == 400  # still sniffed, still refused


def test_mime_patterns():
    patterns = MimePatterns(["image/*", "Application/PDF"])
    assert "image/png" in patterns and "image/webp" in patterns
    assert "application/pdf; charset=binary" in patterns
    assert "text/html" not in patterns and "imagex/png" not in patterns
    assert "text/csv" in MimePatterns(["*/*"])
    assert ", ".join(patterns) == "image/*, application/pdf"


async def test_input_policies_see_the_body_on_resources_and_routes(acme):
    """Gates decide before the body is read; `$input` conditions wait for it."""
    api = acme
    await api.studio.post(
        f"{ENV}/policies",
        json={
            "name": "small",
            "condition": {"all": [{"authenticated": True}, {"lte": ["$input.total", 100]}]},
        },
    )
    await api.studio.post(
        f"{ENV}/policies",
        json={
            "name": "few_items",
            "condition": {"all": [{"authenticated": True}, {"lte": ["$input.items", 5]}]},
        },
    )
    await api.studio.put(
        f"{ENV}/resources/orders",
        json={
            **ORDERS,
            "operations": {**ORDERS["operations"], "create": {"enabled": True, "policy": "small"}},
        },
    )
    await api.studio.post(
        f"{ENV}/routes",
        json={
            "method": "POST",
            "path": "/small-quotes",
            "handler_type": "function",
            "handler": "quote",
            "policy": "few_items",
            "input_fields": [{"name": "items", "type": "integer", "required": True}],
        },
    )
    ada = api.user_headers("development", user_id="7")
    assert (
        await api.http.post("/rest/v1/orders", json={"total": 40}, headers=ada)
    ).status_code == 201
    assert (
        await api.http.post("/rest/v1/orders", json={"total": 400}, headers=ada)
    ).status_code == 403
    assert (
        await api.http.post("/rest/v1/small-quotes", json={"items": 3}, headers=ada)
    ).status_code == 200
    assert (
        await api.http.post("/rest/v1/small-quotes", json={"items": 9}, headers=ada)
    ).status_code == 403
    anonymous = api.context_headers("development")
    assert (
        await api.http.post("/rest/v1/small-quotes", json={"items": 3}, headers=anonymous)
    ).status_code == 401
