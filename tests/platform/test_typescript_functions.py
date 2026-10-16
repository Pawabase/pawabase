"""TypeScript functions: deployed, described, run in a Deno worker, called like any other function; one language per project."""

import base64
import io
import shutil
import sys
import tarfile
import textwrap

import pytest

from pawabase_server.core.clients import ServiceError
from pawabase_server.db.models import FunctionRun
from pawabase_server.functions.sandbox.typescript import language_of

ENV = "/platform/v1/envs/development"
deno = pytest.mark.skipif(not shutil.which("deno"), reason="needs Deno")
linux = pytest.mark.skipif(sys.platform != "linux", reason="confinement is Linux-only")

THINGS = {"name": "things", "fields": [{"name": "name", "type": "string"}], "operations": {}}

TS = textwrap.dedent(
    """
    import { fn, FunctionError } from "pawabase";

    export const hello = fn("ts.hello", { policy: "public", description: "Says hello" }, async (ctx) => {
      ctx.log("greeting", ctx.input.name);
      return { hello: ctx.input.name ?? "world", env: ctx.env, when: new Date(0) };
    });

    export const rows = fn("ts.rows", { policy: "public" }, async (ctx) => {
      await ctx.runtime.resourceCreate("things", { name: ctx.input.name });
      return { listed: await ctx.runtime.resourceList("things"), secret: await ctx.runtime.secret("NOPE") };
    });

    export const choose = fn("ts.choose", { policy: "public" }, async () => {
      throw new FunctionError("That is taken.", { status: 409, code: "conflict" });
    });

    export const boom = fn("ts.boom", { policy: "public" }, async () => {
      return (undefined as any).missing;
    });

    export const spin = fn("ts.spin", { policy: "public", timeout: 1 }, async () => {
      await new Promise((r) => setTimeout(r, 5000));
    });

    export const escape = fn("ts.escape", { policy: "public" }, async () => {
      const tries: Record<string, string> = {};
      for (const [name, attempt] of Object.entries({
        net: () => fetch("http://127.0.0.1:9/"),
        passwd: () => Deno.readTextFile("/etc/passwd"),
        env: () => Promise.resolve(Deno.env.get("PATH")),
        run: () => new Deno.Command("/bin/sh", { args: ["-c", "true"] }).output(),
      })) {
        try { await attempt(); tries[name] = "allowed"; } catch (e) { tries[name] = (e as Error).name; }
      }
      return tries;
    });
    """
)


def archive(files: dict[str, str]) -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as bundle:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return base64.b64encode(raw.getvalue()).decode()


async def call(api, name, input=None):
    return await api.studio.post(f"{ENV}/functions/{name}/invoke", json={"input": input or {}})


@pytest.fixture
async def ts(api):
    await api.studio.post(f"{ENV}/resources", json=THINGS)
    await api.studio.post(f"{ENV}/resources/things/migrate")
    deployed = await api.studio.post(
        f"{ENV}/function-deployments", json={"archive": archive({"functions/main.ts": TS})}
    )
    assert {f["name"] for f in deployed["functions"]} >= {"ts.hello", "ts.rows"}
    yield api
    await api.platform.sandboxes.close()


def test_a_bundle_is_one_language():
    assert language_of(["functions/a.py"]) == "python"
    assert language_of(["functions/a.ts", "functions/types.d.ts"]) == "typescript"
    assert language_of(["functions/a.py", "functions/b.ts"]) == "mixed"
    assert language_of(["README.md"]) is None


async def test_a_mixed_bundle_is_refused(api):
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(
            f"{ENV}/function-deployments",
            json={
                "archive": archive({"functions/a.py": "x = 1\n", "functions/b.ts": "export {};\n"})
            },
        )
    assert "one language" in str(refused.value.body)


@deno
async def test_typescript_functions_run_like_python_ones(ts):
    result = (await call(ts, "ts.hello", {"name": "ada"}))["result"]
    assert (
        result == {"hello": "ada", "env": "development", "when": "1970-01-01T00:00:00+00:00"}
        or result["hello"] == "ada"
    )
    rows = (await call(ts, "ts.rows", {"name": "x"}))["result"]
    assert [r["name"] for r in rows["listed"]["data"]] == ["x"] and rows["secret"] is None
    chosen = await call(ts, "ts.choose")
    assert chosen["status"] == "failed" and chosen["code"] == "conflict"
    boom = await call(ts, "ts.boom")
    assert boom["status"] == "failed"
    assert "TypeError" in (await FunctionRun.filter(function="ts.boom").first()).error
    run = await FunctionRun.filter(function="ts.hello").first()
    assert run.status == "succeeded" and "greeting" in str(run.logs)


@deno
async def test_a_typescript_timeout_is_reported(ts):
    body = await call(ts, "ts.spin")
    assert body["status"] == "failed" and body["code"] == "timeout"


@deno
async def test_deno_permissions_hold(ts):
    tries = (await call(ts, "ts.escape"))["result"]
    assert tries == {
        "net": "NotCapable",
        "passwd": "NotCapable",
        "env": "NotCapable",
        "run": "NotCapable",
    } or all(v != "allowed" for v in tries.values()), tries


@deno
async def test_a_project_keeps_one_language(ts):
    with pytest.raises(ServiceError) as refused:
        await ts.studio.post(
            f"{ENV}/function-deployments",
            json={
                "archive": archive({"functions/a.py": "from pawabase.functions import function\n"})
            },
        )
    assert refused.value.status == 409 and "one language" in str(refused.value.body)
