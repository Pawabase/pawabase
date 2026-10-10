"""Functions run in worker processes of their own when the environment asks for it, and everything else about them stays the same."""

import asyncio
import base64
import io
import os
import sys
import tarfile
import textwrap
import time

import pytest

from database.models import FunctionRun

ENV = "/platform/v1/envs/development"

THINGS = {"name": "things", "fields": [{"name": "name", "type": "string"}], "operations": {}}

CODE = textwrap.dedent(
    """
    import asyncio
    import os
    from pawabase_core.functions import FunctionError, function

    @function("sb.echo", policy="public")
    async def echo(ctx):
        leaked = sorted(k for k in os.environ if k.startswith("PAWABASE") or "SECRET" in k or "DATABASE" in k)
        return {"pid": os.getpid(), "input": ctx.input, "leaked": leaked}

    @function("sb.rows", policy="public")
    async def rows(ctx):
        await ctx.runtime.resource_create("things", {"name": ctx.input["name"]})
        db = await ctx.runtime.db()
        return {"listed": await ctx.runtime.resource_list("things"), "count": await db.scalar("SELECT COUNT(*) FROM things")}

    @function("sb.transaction", policy="public")
    async def transaction(ctx):
        try:
            async with ctx.runtime.transaction() as db:
                await db.insert("things", {"name": "inside"})
                raise ValueError("undo")
        except ValueError:
            pass
        return {"left": await (await ctx.runtime.db()).scalar("SELECT COUNT(*) FROM things")}

    @function("sb.spin", policy="public", timeout=1)
    async def spin(ctx):
        while True:  # blocks the event loop: nothing in Python can interrupt this
            pass

    @function("sb.wait", policy="public")
    async def wait(ctx):
        await asyncio.sleep(0.5)  # like waiting on a payment API
        return {"pid": os.getpid()}

    @function("sb.wait_too_long", policy="public", timeout=1)
    async def wait_too_long(ctx):
        await asyncio.sleep(30)

    @function("sb.die", policy="public")
    async def die(ctx):
        os._exit(3)

    @function("sb.boom", policy="public")
    async def boom(ctx):
        return {}["missing"]

    @function("sb.choose", policy="public")
    async def choose(ctx):
        raise FunctionError("That is taken.", status=409, code="conflict")

    @function("sb.reach", policy="public")
    async def reach(ctx):
        return await ctx.runtime.http_request("GET", "http://127.0.0.1:9/")

    @function("sb.memory", policy="public")
    async def memory(ctx):
        return len(bytearray(4 * 1024 * 1024 * 1024))
    """
)


def archive(code: str = CODE) -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as bundle:
        source = code.encode()
        info = tarfile.TarInfo("functions/sb.py")
        info.size = len(source)
        bundle.addfile(info, io.BytesIO(source))
    return base64.b64encode(raw.getvalue()).decode()


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"function_isolation": "process", "sandbox_memory_mb": 512})


@pytest.fixture
async def sandbox(api):
    await api.studio.post(f"{ENV}/resources", json=THINGS)
    await api.studio.post(f"{ENV}/resources/things/migrate")
    await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive()})
    yield api
    await api.platform.sandboxes.close()


async def call(api, name, input=None):
    return await api.studio.post(f"{ENV}/functions/{name}/invoke", json={"input": input or {}})


async def failing(api, name):
    """A test invocation reports a failure inside its answer: ``{"status": "failed", "error", "code"}``."""
    body = await call(api, name)
    assert body["status"] == "failed", body
    return body


async def test_a_function_runs_in_a_process_of_its_own_with_nothing_of_the_apis_environment(
    sandbox,
):
    result = (await call(sandbox, "sb.echo", {"n": 1}))["result"]
    assert result["input"] == {"n": 1} and result["pid"] != os.getpid()
    assert result["leaked"] == []  # no secret, no database address, none of the API's variables


async def test_the_environment_can_stay_in_process(api):
    await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive()})
    api.platform.settings.function_isolation = "inprocess"
    assert (await call(api, "sb.echo"))["result"]["pid"] == os.getpid()


async def test_a_worker_is_reused_until_the_deployment_is_replaced(sandbox):
    first = (await call(sandbox, "sb.echo"))["result"]["pid"]
    assert (await call(sandbox, "sb.echo"))["result"][
        "pid"
    ] == first and sandbox.platform.sandboxes.started == 1
    await sandbox.studio.post(
        f"{ENV}/function-deployments", json={"archive": archive(CODE + "\n# changed\n")}
    )
    assert (await call(sandbox, "sb.echo"))["result"][
        "pid"
    ] != first  # the old deployment's worker was stopped


async def test_the_runtime_works_through_the_pipe(sandbox):
    result = (await call(sandbox, "sb.rows", {"name": "a"}))["result"]
    assert [row["name"] for row in result["listed"]["data"]] == ["a"] and result["count"] == 1


async def test_a_transaction_in_the_worker_rolls_back(sandbox):
    assert (await call(sandbox, "sb.transaction"))["result"] == {"left": 0}
    assert not sandbox.platform.sandbox_transactions.open  # nothing is left open on the platform


async def test_a_function_stuck_in_a_loop_is_killed_and_the_api_goes_on(sandbox):
    before = (await call(sandbox, "sb.echo"))["result"]["pid"]
    error = await failing(sandbox, "sb.spin")
    assert error["code"] == "timeout"
    assert (await call(sandbox, "sb.echo"))["result"][
        "pid"
    ] != before  # the stuck process was killed; the next call has a new one


async def test_a_function_that_exits_takes_down_only_its_own_process(sandbox):
    error = await failing(sandbox, "sb.die")
    assert (
        error["error"] == "FunctionFailed: function 'sb.die' failed"
    )  # nothing of the crash in the answer
    run = await FunctionRun.filter(function="sb.die").first()
    assert "SandboxCrashed" in run.error and "exit code 3" in run.error
    assert (await call(sandbox, "sb.echo"))["result"]["input"] == {}


async def test_a_bug_is_reported_with_where_it_broke_and_nothing_internal_to_the_caller(sandbox):
    error = await failing(sandbox, "sb.boom")
    assert error["error"] == "FunctionFailed: function 'sb.boom' failed"
    assert "missing" not in str(error)  # nothing internal in the answer
    run = await FunctionRun.filter(function="sb.boom").first()
    assert "KeyError" in run.error and "sb.py" in run.error and "boom" in run.error


async def test_a_function_that_chose_to_fail_keeps_its_status_and_code(sandbox):
    error = await failing(sandbox, "sb.choose")
    assert error["code"] == "conflict" and error["error"] == "That is taken."


async def test_outbound_requests_go_through_the_platform_guard(sandbox):
    error = await failing(
        sandbox, "sb.reach"
    )  # a private address: refused by the platform, not attempted from the worker
    assert error["code"] == "runtime_error"
    run = await FunctionRun.filter(function="sb.reach").first()
    assert "OutboundRefused" in run.error and "private address" in run.error


@pytest.mark.skipif(sys.platform != "linux", reason="macOS has no address-space limit")
async def test_a_function_that_takes_too_much_memory_fails_alone(sandbox):
    await failing(sandbox, "sb.memory")
    run = await FunctionRun.filter(function="sb.memory").first()
    assert "MemoryError" in run.error or "SandboxCrashed" in run.error
    assert (await call(sandbox, "sb.echo"))["result"]["input"] == {}


def test_isolation_is_the_default_and_in_process_is_the_opt_out():
    from types import SimpleNamespace

    from app.config import ApiSettings
    from app.platform import Platform

    assert ApiSettings(_env_file=None).function_isolation == "process"
    deployed, mounted = (
        SimpleNamespace(project="runtime/development"),
        SimpleNamespace(project="runtime"),
    )
    platform = Platform.__new__(Platform)
    platform.settings = ApiSettings(_env_file=None)
    state = SimpleNamespace(env_name="development", secret_values={})
    assert platform.isolated(state, deployed) is True
    assert (
        platform.isolated(state, mounted) is False
    )  # the platform's own mounted code is never sandboxed
    platform.settings = ApiSettings(_env_file=None, function_isolation="inprocess")
    assert platform.isolated(state, deployed) is False


def test_one_environment_can_opt_out(monkeypatch):
    from types import SimpleNamespace

    from app.config import ApiSettings
    from app.platform import Platform

    monkeypatch.setenv("LEGACY_FUNCTION_ISOLATION", "inprocess")
    platform = Platform.__new__(Platform)
    platform.settings = ApiSettings(_env_file=None)
    deployed = SimpleNamespace(project="runtime/legacy")
    assert (
        platform.isolated(SimpleNamespace(env_name="legacy", secret_values={}), deployed) is False
    )
    assert platform.isolated(SimpleNamespace(env_name="other", secret_values={}), deployed) is True


async def test_calls_that_wait_run_at_once_in_a_worker_as_they_did_on_the_event_loop(sandbox):
    """40 calls of half a second each take half a second, not ten: a worker runs many calls, it does not queue them."""
    await call(sandbox, "sb.wait")  # the worker is warm
    started = time.perf_counter()
    results = await asyncio.gather(*[call(sandbox, "sb.wait") for _ in range(40)])
    elapsed = time.perf_counter() - started
    assert all(r["result"] for r in results)
    assert elapsed < 3.0, elapsed  # one at a time would be 20 s; four at a time, 5 s
    assert sandbox.platform.sandboxes.started <= 4  # no worker per call


async def test_a_call_that_runs_out_of_time_does_not_cost_its_worker_its_life(sandbox):
    before = (await call(sandbox, "sb.echo"))["result"]["pid"]
    error = await failing(sandbox, "sb.wait_too_long")
    assert error["code"] == "timeout"
    assert (await call(sandbox, "sb.echo"))["result"][
        "pid"
    ] == before  # cancelled politely, so the same process goes on
    assert sandbox.platform.sandboxes.started == 1
