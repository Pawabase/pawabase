"""Sandbox workers are confined (Landlock + seccomp) where the system allows, and refuse to run where confinement is required but missing.

The escape tests run only on Linux; on macOS the fail-closed test runs instead (there is no Landlock to apply there).
"""

import sys
import textwrap

import pytest

from pawabase_server.db.models import FunctionRun
from pawabase_server.functions.sandbox.confine import SYSCALLS, seccomp_program
from pawabase_server.functions.sandbox.host import resolve_confinement
from tests.platform.test_sandbox import ENV, archive, call, failing

linux = pytest.mark.skipif(
    sys.platform != "linux", reason="Landlock and seccomp are Linux features"
)

CODE = textwrap.dedent(
    """
    import asyncio, hashlib, os, socket, threading
    from pawabase_server.functions.registry import function

    def attempt(fn):
        try:
            fn()
            return "allowed"
        except OSError as exc:
            return type(exc).__name__

    @function("cf.escape", policy="public")
    async def escape(ctx):
        return {
            "own_tmp": attempt(lambda: open(os.path.join(os.environ["TMPDIR"], "x"), "w").write("ok")),
            "platform_db": attempt(lambda: open(ctx.input["db"], "rb").read(1)),
            "passwd": attempt(lambda: open("/etc/passwd").read(1)),
            "socket": attempt(lambda: socket.socket()),
            "signal_server": attempt(lambda: os.kill(os.getppid(), 0)),
        }

    @function("cf.burn", policy="public", timeout=8)
    async def burn(ctx):
        def work():
            block = b"x" * (1 << 20)
            while True:
                hashlib.sha256(block).digest()  # releases the GIL: real CPU on every thread
        for _ in range(4):
            threading.Thread(target=work, daemon=True).start()
        await asyncio.sleep(30)

    @function("cf.ok", policy="public")
    async def ok(ctx):
        return {"ok": True}
    """
)


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"function_isolation": "process", "sandbox_memory_mb": 512})


@pytest.fixture
async def confined(api):
    await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive(CODE)})
    yield api
    await api.platform.sandboxes.close()


def test_auto_requires_confinement_on_linux_only():
    assert resolve_confinement("off") == "off"
    assert resolve_confinement("required") == "required"
    assert resolve_confinement("auto") == ("required" if sys.platform == "linux" else "off")


@pytest.mark.parametrize("machine", sorted(SYSCALLS))
def test_the_filter_builds_for_every_supported_machine(machine):
    program = seccomp_program(machine, 4242)
    assert 30 < len(program) < 255
    assert all(0 <= jt <= 255 and 0 <= jf <= 255 for _, jt, jf, _ in program)


@linux
async def test_a_confined_function_cannot_reach_past_its_sandbox(confined, settings):
    db = settings.database_url.removeprefix("sqlite://")
    result = (await call(confined, "cf.escape", {"db": db}))["result"]
    assert result == {
        "own_tmp": "allowed",
        "platform_db": "PermissionError",
        "passwd": "PermissionError",
        "socket": "PermissionError",
        "signal_server": "PermissionError",
    }
    workers = [w for w in confined.platform.sandboxes.all if w.alive]
    assert workers and workers[0].confinement["mode"] == "required"
    assert workers[0].confinement["problems"] == []


@linux
async def test_cpu_beyond_the_budget_stops_the_worker(confined):
    await failing(confined, "cf.burn")
    run = await FunctionRun.filter(function="cf.burn").first()
    assert "CpuLimitExceeded" in run.error, run.error
    assert (await call(confined, "cf.ok"))["result"] == {
        "ok": True
    }  # the next call gets a fresh worker


@pytest.mark.skipif(
    sys.platform == "linux", reason="Linux can confine; this checks refusing where it cannot"
)
async def test_required_confinement_that_cannot_be_applied_runs_nothing(confined):
    confined.platform.sandboxes.limits = confined.platform.sandboxes.limits.__class__(
        **{**confined.platform.sandboxes.limits.__dict__, "confinement": "required"}
    )
    body = await failing(confined, "cf.ok")
    run = await FunctionRun.filter(function="cf.ok").first()
    assert "SandboxConfinementUnavailable" in run.error, (body, run.error)
