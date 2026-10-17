"""HTTP functions end to end: deployed as ``functions/<name>/index.ts``, served at ``/functions/v1/<name>/…`` by Deno workers.

These run the real thing (Deno, the worker protocol, confinement where the kernel has it), so they need Deno; the runtime service is
exercised in-process through ASGI transports at the end.
"""

import asyncio
import base64
import io
import json
import shutil
import tarfile
import textwrap

import httpx
import pytest

from pawabase_server.db.models import FunctionRun

ENV = "/platform/v1/envs/development"
deno = pytest.mark.skipif(not shutil.which("deno"), reason="needs Deno")
pytestmark = deno

THINGS = {
    "name": "things",
    "fields": [{"name": "name", "type": "string"}],
    "operations": {"list": {"enabled": True, "policy": "public"}},
}

ECHO = """
import { serve } from "@pawabase/functions";

export const config = { description: "Echoes the request", auth: "key" };

serve(async (req, ctx) => {
  const body = req.method === "GET" ? null : await req.text();
  console.log("echo", req.method, ctx.path);
  return Response.json({
    method: req.method,
    path: ctx.path,
    query: Object.fromEntries(ctx.query),
    url: req.url,
    body,
    header: req.headers.get("x-test"),
    sawApiKeyHeader: req.headers.has("x-pawabase-context"),
    user: ctx.auth.user_id ?? null,
    env: ctx.env,
    fn: ctx.function,
  }, { status: req.method === "POST" ? 201 : 200, headers: { "x-echo": "1" } });
});
"""

NATIVE = """
import { getContext } from "pawabase";

Deno.serve(async (req) => {
  const ctx = getContext(req);
  return new Response(`hello ${ctx.env} ${new URL(req.url).pathname}`, { headers: { "content-type": "text/plain" } });
});
"""

MEMBERS = """
import { serve } from "@pawabase/functions";
export const config = { auth: "user", methods: ["GET"] };
serve((req, ctx) => Response.json({ user: ctx.auth.user_id, roles: ctx.auth.roles }));
"""

WEBHOOK = """
import { serve } from "@pawabase/functions";
export const config = { auth: "public" };
serve(async (req, ctx) => {
  const event = await req.json();
  ctx.waitUntil((async () => {
    await new Promise((r) => setTimeout(r, 200));
    await ctx.runtime.resourceCreate("things", { name: `hook:${event.id}` });
  })());
  return new Response(null, { status: 202 });
});
"""

SECRETS = """
import { serve } from "@pawabase/functions";
serve(() => Response.json({ greeting: Deno.env.get("GREETING") ?? null, env: Deno.env.get("PAWABASE_ENVIRONMENT"), fn: Deno.env.get("PAWABASE_FUNCTION") }));
"""

DATA = """
import { serve, json } from "@pawabase/functions";
serve(async (req, ctx) => {
  const { name } = await req.json();
  await ctx.runtime.resourceCreate("things", { name });
  const listed = await ctx.runtime.resourceList("things");
  return json({ names: listed.data.map((r) => r.name) });
});
"""

STREAM = """
import { serve, sse } from "@pawabase/functions";
serve(() => sse(async (send) => {
  for (let i = 1; i <= 3; i++) {
    send({ event: "tick", data: { i } });
    await new Promise((r) => setTimeout(r, 20));
  }
}));
"""

FAILS = """
import { serve, FunctionError } from "@pawabase/functions";
serve((req) => {
  const kind = new URL(req.url).searchParams.get("kind");
  if (kind === "chosen") throw new FunctionError("That is taken.", { status: 409, code: "conflict" });
  if (kind === "nothing") return undefined as any;
  return (undefined as any).boom;
});
"""

SLOW = """
import { serve } from "@pawabase/functions";
export const config = { timeout: 1 };
serve(async (req, ctx) => {
  await new Promise((resolve) => { const t = setTimeout(resolve, 5000); ctx.signal.addEventListener("abort", () => clearTimeout(t)); });
  return new Response("late");
});
"""

FETCHES = """
import { serve } from "@pawabase/functions";
serve(async () => {
  try {
    await fetch("http://127.0.0.1:9/");
    return Response.json({ reached: true });
  } catch (error) {
    return Response.json({ reached: false, message: String(error.message ?? error) });
  }
});
"""

SHARED = """
export const double = (n: number) => n * 2;
"""

USES_MAP = """
import { serve } from "@pawabase/functions";
import { double } from "@lib/math.ts";
serve(() => Response.json({ four: double(2) }));
"""

RPC = """
import { fn } from "@pawabase/functions";
export const add = fn("add", { policy: "public" }, async (ctx) => ({ sum: ctx.input.a + ctx.input.b }));
"""


def archive(files: dict[str, str]) -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as bundle:
        for name, text in files.items():
            data = textwrap.dedent(text).encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return base64.b64encode(raw.getvalue()).decode()


BUNDLE = {
    "functions/echo/index.ts": ECHO,
    "functions/native/index.ts": NATIVE,
    "functions/members/index.ts": MEMBERS,
    "functions/webhook/index.ts": WEBHOOK,
    "functions/secrets/index.ts": SECRETS,
    "functions/data/index.ts": DATA,
    "functions/stream/index.ts": STREAM,
    "functions/fails/index.ts": FAILS,
    "functions/slow/index.ts": SLOW,
    "functions/fetches/index.ts": FETCHES,
    "functions/maps/index.ts": USES_MAP,
    "functions/_shared/math.ts": SHARED,
    "functions/deno.json": json.dumps({"imports": {"@lib/": "./_shared/"}}),
    "functions/rpc.ts": RPC,
}


@pytest.fixture
async def http_api(api):
    await api.studio.post(f"{ENV}/resources", json=THINGS)
    await api.studio.post(f"{ENV}/resources/things/migrate")
    deployed = await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive(BUNDLE)})
    assert {f["name"] for f in deployed["functions"]} >= {"add"}
    yield api
    await api.platform.functions.close()


def key(api):
    return api.context_headers("development")


async def test_the_listing_shows_http_functions_with_their_config(http_api):
    listing = await http_api.studio.get(f"{ENV}/functions")
    kinds = {f["name"]: f.get("kind") for f in listing["data"]}
    assert kinds["add"] == "call" and kinds["echo"] == "http" and kinds["webhook"] == "http"
    echo = next(f for f in listing["data"] if f["name"] == "echo")
    assert echo["config"]["description"] == "Echoes the request" and echo["url"].endswith("/functions/v1/echo")
    webhook = next(f for f in listing["data"] if f["name"] == "webhook")
    assert webhook["public_url"].endswith("/functions/v1/public/development/webhook")


async def test_any_method_and_path_reach_the_handler_with_the_request_as_sent(http_api):
    got = await http_api.http.get("/functions/v1/echo/orders/7?expand=items", headers={**key(http_api), "x-test": "yes"})
    assert got.status_code == 200 and got.headers["x-echo"] == "1" and got.headers["x-pawabase-function"] == "echo"
    body = got.json()
    assert body["method"] == "GET" and body["path"] == "/orders/7" and body["query"] == {"expand": "items"}
    assert body["url"].endswith("/functions/v1/echo/orders/7?expand=items") and body["header"] == "yes"
    assert body["sawApiKeyHeader"] is False and body["env"] == "development" and body["fn"] == "echo"
    posted = await http_api.http.post("/functions/v1/echo", content=b"raw text", headers=key(http_api))
    assert posted.status_code == 201 and posted.json()["body"] == "raw text" and posted.json()["path"] == "/"
    # The fn() functions keep their own route.
    assert (await http_api.http.post("/functions/v1/add", json={"a": 2, "b": 3}, headers=key(http_api))).json() == {
        "data": {"sum": 5}
    }


async def test_runs_are_recorded_with_method_path_status_and_logs(http_api):
    await http_api.http.put("/functions/v1/echo/a/b", content=b"x", headers=key(http_api))
    run = await FunctionRun.filter(function="echo", method="PUT").first()
    assert run.kind == "http" and run.path == "/a/b" and run.status_code == 200 and run.status == "succeeded"
    assert run.request_bytes == 1 and run.response_bytes > 0 and run.cold_start in (True, False)
    assert any("echo PUT /a/b" in line["message"] for line in run.logs)


async def test_deno_serve_handlers_work_unchanged_and_get_their_context(http_api):
    got = await http_api.http.get("/functions/v1/native/x", headers=key(http_api))
    assert got.status_code == 200 and got.text == "hello development /functions/v1/native/x"


async def test_auth_user_needs_a_signed_in_caller_and_methods_are_enforced(http_api):
    anonymous = await http_api.http.get("/functions/v1/members", headers=key(http_api))
    assert anonymous.status_code == 401 and anonymous.json()["error"] == "unauthenticated"
    signed_in = await http_api.http.get(
        "/functions/v1/members", headers=http_api.user_headers("development", user_id="42", roles=["editor"])
    )
    assert signed_in.json() == {"user": "42", "roles": ["editor"]}
    wrong = await http_api.http.post("/functions/v1/members", headers=http_api.user_headers("development"))
    assert wrong.status_code == 405 and "GET" in wrong.headers["allow"]
    forged = await http_api.http.get("/functions/v1/members", headers={**key(http_api), "Authorization": "Bearer nope"})
    assert forged.status_code == 401 and forged.json()["error"] == "invalid_token"


async def test_a_public_function_is_reachable_without_a_key_and_wait_until_finishes_after_the_response(http_api):
    answered = await http_api.http.post("/functions/v1/public/development/webhook", json={"id": "e1"})
    assert answered.status_code == 202
    for _ in range(50):  # the response came first; the work handed to waitUntil lands afterwards
        rows = (await http_api.http.get("/rest/v1/things", headers=key(http_api))).json()["data"]
        if any(r["name"] == "hook:e1" for r in rows):
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("waitUntil work never ran")
    for _ in range(50):
        run = await FunctionRun.filter(function="webhook").first()
        if run is not None:
            break
        await asyncio.sleep(0.1)
    assert run.status_code == 202 and run.background_ms is not None and run.background_ms >= 150
    # Without a key, only public functions answer.
    refused = await http_api.http.get("/functions/v1/public/development/echo")
    assert refused.status_code == 401 and refused.json()["error"] == "missing_api_key"
    missing = await http_api.http.get("/functions/v1/public/development/nope")
    assert missing.status_code == 404


async def test_secrets_are_environment_variables_and_new_values_reach_new_workers(http_api):
    await http_api.studio.put(f"{ENV}/secrets/GREETING", json={"value": "hi"})
    first = (await http_api.http.get("/functions/v1/secrets", headers=key(http_api))).json()
    assert first == {"greeting": "hi", "env": "development", "fn": "secrets"}
    await http_api.studio.put(f"{ENV}/secrets/GREETING", json={"value": "hello again"})
    second = (await http_api.http.get("/functions/v1/secrets", headers=key(http_api))).json()
    assert second["greeting"] == "hello again"


async def test_the_runtime_acts_as_the_caller(http_api):
    got = await http_api.http.post("/functions/v1/data", json={"name": "from-http"}, headers=key(http_api))
    assert got.status_code == 200 and "from-http" in got.json()["names"]


async def test_a_response_streams(http_api):
    got = await http_api.http.get("/functions/v1/stream", headers=key(http_api))
    assert got.headers["content-type"].startswith("text/event-stream")
    events = [line for line in got.text.splitlines() if line.startswith("data: ")]
    assert events == ['data: {"i":1}', 'data: {"i":2}', 'data: {"i":3}']


async def test_failures_have_the_platform_shape_and_do_not_leak_the_stack(http_api):
    chosen = await http_api.http.get("/functions/v1/fails?kind=chosen", headers=key(http_api))
    assert chosen.status_code == 409 and chosen.json() == {"error": "conflict", "message": "That is taken.", "details": None}
    crashed = await http_api.http.get("/functions/v1/fails?kind=boom", headers=key(http_api))
    assert crashed.status_code == 500 and crashed.json()["error"] == "function_error"
    assert "boom" not in crashed.text
    nothing = await http_api.http.get("/functions/v1/fails?kind=nothing", headers=key(http_api))
    assert nothing.status_code == 500
    run = await FunctionRun.filter(function="fails", status="failed").first()
    assert run.error and "TypeError" in run.error


async def test_a_function_that_runs_out_of_time_gets_504_and_is_told_to_stop(http_api):
    slow = await http_api.http.get("/functions/v1/slow", headers=key(http_api))
    assert slow.status_code == 504 and slow.json()["error"] == "function_timeout"


async def test_fetch_cannot_reach_private_addresses(http_api):
    got = (await http_api.http.get("/functions/v1/fetches", headers=key(http_api))).json()
    assert got["reached"] is False and "private" in got["message"]


async def test_import_maps_resolve_local_paths(http_api):
    assert (await http_api.http.get("/functions/v1/maps", headers=key(http_api))).json() == {"four": 4}


async def test_runtime_status_restart_detail_and_try_it(http_api):
    await http_api.http.get("/functions/v1/echo", headers=key(http_api))
    status = await http_api.studio.get(f"{ENV}/functions-runtime")
    assert status["mode"] == "embedded"
    echo = next(f for f in status["http"]["functions"] if f["function"] == "echo")
    assert echo["workers"] and echo["stats"]["requests"] >= 1
    restarted = await http_api.studio.post(f"{ENV}/functions-runtime/restart", json={"function": "echo"})
    assert restarted["http"] >= 1
    tried = await http_api.studio.post(
        f"{ENV}/http-functions/echo/request",
        json={"method": "POST", "path": "/try?x=1", "body": "hello", "as_user": {"authenticated": True, "user_id": "7"}},
    )
    assert tried["status"] == 201
    body = json.loads(tried["body"])
    assert body["path"] == "/try" and body["query"] == {"x": "1"} and body["user"] == "7" and body["body"] == "hello"
    detail = await http_api.studio.get(f"{ENV}/http-functions/echo")
    assert detail["kind"] == "http" and detail["stats"]["requests"] >= 2


async def test_a_bad_config_stops_the_deployment(api):
    bad = """
    import { serve } from "@pawabase/functions";
    export const config = { timeout: 100000, wat: 1 };
    serve(() => new Response("x"));
    """
    from pawabase_server.core.clients import ServiceError

    with pytest.raises(ServiceError) as refused:
        await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive({"functions/bad/index.ts": bad})})
    text = str(refused.value.body)
    assert "config.timeout" in text and "config.wat" in text


async def test_reserved_folder_names_are_refused(api):
    from pawabase_server.core.clients import ServiceError

    source = 'import { serve } from "pawabase"; serve(() => new Response("x"));'
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive({"functions/public/index.ts": source})})
    assert "reserved" in str(refused.value.body)


# ── the runtime as a service of its own ──────────────────────────────────────


async def test_the_runtime_service_runs_both_kinds_and_calls_back_with_grants(http_api):
    """The server forwards to a RuntimeService; the service reaches the server's internal routes. Both over in-process ASGI."""
    from pawabase_server.functions.service.app import RuntimeService
    from pawabase_server.functions.service.gateway import RemoteFunctions

    settings = http_api.settings.model_copy(update={"functions_runtime_url": "http://functions.test"})
    service = RuntimeService(settings)
    service.api = httpx.AsyncClient(transport=httpx.ASGITransport(app=http_api.app), base_url="http://api.test")
    service.engine.variables.client = service.api
    remote = RemoteFunctions(http_api.platform)
    remote.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=service), base_url="http://functions.test")
    embedded = http_api.platform.functions
    http_api.platform.functions = remote
    try:
        got = await http_api.http.post("/functions/v1/data", json={"name": "via-service"}, headers=key(http_api))
        assert got.status_code == 200 and "via-service" in got.json()["names"]
        assert (await http_api.http.post("/functions/v1/add", json={"a": 1, "b": 1}, headers=key(http_api))).json() == {
            "data": {"sum": 2}
        }
        status = await http_api.studio.get(f"{ENV}/functions-runtime")
        assert status["mode"] == "service" and status["reachable"] is True
        for _ in range(50):
            run = await FunctionRun.filter(function="data", path="/").order_by("-created_at").first()
            if run is not None and run.status_code == 200:
                break
            await asyncio.sleep(0.1)
        assert run.kind == "http" and run.status == "succeeded"
        # A grant for one function cannot run another, and the service refuses calls without a service token.
        unauthorized = await remote.client.post("/v1/call", json={})
        assert unauthorized.status_code == 401
    finally:
        http_api.platform.functions = embedded
        await service.engine.close()
        await remote.client.aclose()
        await service.api.aclose()


# ── npm: and jsr: imports, fetched when the deployment is activated ──────────

network = pytest.mark.skipif(
    not __import__("os").environ.get("PAWABASE_TEST_NETWORK"), reason="fetches from npm and jsr (set PAWABASE_TEST_NETWORK=1)"
)

VENDORED = """
import { serve } from "@pawabase/functions";
import ms from "npm:ms@2.1.3";
import { encodeHex } from "jsr:@std/encoding@1.0.5/hex";

serve(() => Response.json({ ms: ms("2 days"), hex: encodeHex(new TextEncoder().encode("hi")) }));
"""


@network
async def test_npm_and_jsr_imports_are_vendored_at_deploy_and_run_from_the_copy(api):
    deployed = await api.studio.post(
        f"{ENV}/function-deployments", json={"archive": archive({"functions/vendored/index.ts": VENDORED})}
    )
    stamp = api.platform.deployments.stamp("development")
    assert stamp["vendor"] and "npm:ms@2.1.3" in stamp["remote_imports"]
    assert api.platform.deployments.vendor_cache("development") is not None
    got = await api.http.get("/functions/v1/vendored", headers=key(api))
    assert got.json() == {"ms": 172800000, "hex": "6869"}
    assert deployed["deployment"]["status"] == "active"
    await api.platform.functions.close()
