"""Resource limits: every run has them, they are the project's (set by the operator or Pawabase Cloud), and a function may only ask
for a shorter timeout.

The enforcement tests need Linux (CPU and memory are read from /proc) and, for TypeScript, Deno; the rest run anywhere.
"""

import asyncio
import base64
import io
import shutil
import subprocess
import sys
import tarfile
import textwrap

import pytest

from pawabase_server.db.models import FunctionRun
from pawabase_server.functions import limits as L
from pawabase_server.functions.service import watchdog

ENV = "/platform/v1/envs/development"
linux = pytest.mark.skipif(not watchdog.supported(), reason="CPU and memory are measured from /proc (Linux)")
deno = pytest.mark.skipif(not shutil.which("deno"), reason="needs Deno")


def archive(files: dict[str, str]) -> str:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as bundle:
        for name, text in files.items():
            data = textwrap.dedent(text).encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return base64.b64encode(raw.getvalue()).decode()


# ── resolving ────────────────────────────────────────────────────────────────


def test_every_run_has_limits_from_the_project_or_the_installation(settings):
    nothing = L.resolve(settings, kind="call")
    assert nothing.timeout == settings.function_default_timeout_seconds and nothing.memory == settings.function_default_memory_mb
    assert nothing.cpu == nothing.timeout  # "as long as its timeout"
    assert set(nothing.sources.values()) == {"default"}

    project = {"max_function_memory_mb": 256, "max_function_cpu_seconds": 5, "max_concurrent_runs_per_function": 3, "max_function_workers": 2}
    held = L.resolve(settings, kind="http", project=project)
    assert (held.memory, held.cpu, held.concurrency, held.workers) == (256, 5, 3, 2)
    assert held.sources["memory"] == "project" and held.sources["max_body"] == "default"


def test_code_may_only_shorten_the_timeout(settings):
    project = {"max_function_timeout_seconds": 20}
    shorter = L.resolve(settings, kind="call", project=project, timeout=5)
    assert (shorter.timeout, shorter.sources["timeout"], shorter.cpu) == (5, "code", 5)
    longer = L.resolve(settings, kind="call", project=project, timeout=600)
    assert (longer.timeout, longer.sources["timeout"]) == (20, "project")


def test_the_installation_maximum_holds_a_project_that_asks_for_more(settings):
    huge = L.resolve(settings, kind="call", project={"max_function_memory_mb": 10**6})
    assert huge.memory == settings.function_max_memory_mb and huge.sources["memory"] == "installation"


def test_function_limits_are_project_limit_fields():
    from pawabase_server.projects.manager import ProjectError, validate_limits

    assert validate_limits({"max_function_memory_mb": 128, "max_function_workers": 2}) == {"max_function_memory_mb": 128, "max_function_workers": 2}
    with pytest.raises(ProjectError):
        validate_limits({"max_function_memory_mb": -1})


def test_a_function_cannot_choose_its_own_resources(settings):
    from pawabase_server.functions.http_functions import parse_config

    _config, problems = parse_config({"memory": 4096, "cpu": 100, "timeout": 10}, max_timeout=400, max_memory=512, max_concurrency=256)
    assert len(problems) == 2 and all("project's" in problem for problem in problems)


async def test_a_function_runs_at_most_concurrency_times_at_once():
    gates = L.FunctionGates()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with gates.slot("e", "f", 1, wait=1):
            entered.set()
            await release.wait()

    holder = asyncio.create_task(hold())
    await entered.wait()
    assert gates.in_use("e", "f") == (1, 1)
    with pytest.raises(L.ConcurrencyLimitReached):
        async with gates.slot("e", "f", 1, wait=0.05):
            pass
    release.set()
    await holder
    async with gates.slot("e", "f", 1, wait=0.05):  # free again
        pass
    async with gates.slot("e", "f", 0, wait=0):  # 0: no limit of its own
        pass


# ── enforcement on a process ─────────────────────────────────────────────────


@linux
async def test_the_watchdog_stops_a_process_that_burns_cpu():
    process = subprocess.Popen([sys.executable, "-c", "while True: pass"], start_new_session=True)
    try:
        async with watchdog.Watch(process.pid, cpu_seconds=0.3, memory_mb=None) as watch:
            for _ in range(100):
                if watch.hit:
                    break
                await asyncio.sleep(0.05)
        assert watch.hit == "cpu" and "CPU" in watch.reason
        assert process.wait(timeout=5) < 0
    finally:
        process.kill()


@linux
async def test_the_watchdog_stops_a_process_that_holds_too_much_memory():
    code = "import time; b = bytearray(300 * 1024 * 1024); b[::4096] = b'x' * len(b[::4096]); time.sleep(30)"
    process = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    try:
        async with watchdog.Watch(process.pid, cpu_seconds=None, memory_mb=100) as watch:
            for _ in range(200):
                if watch.hit:
                    break
                await asyncio.sleep(0.05)
        assert watch.hit == "memory"
        assert watch.usage()["memory_peak_mb"] > 100
    finally:
        process.kill()


# ── through the API ──────────────────────────────────────────────────────────

SLOW = """
import asyncio
from pawabase_server.functions.registry import function

@function("lim.sleep", policy="public")
async def sleep(ctx):
    await asyncio.sleep(ctx.input.get("seconds", 0))
    return {"slept": ctx.input.get("seconds", 0)}
"""


async def project_limits(api, **limits):
    """What Pawabase Cloud (or an operator) does: change the project's limits."""
    from pawabase_server.projects.manager import ProjectManager

    await ProjectManager(api.settings).patch_limits(api.settings.default_project, limits)  # bumps the environments' versions


@pytest.fixture
async def slow(api):
    await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive({"functions/lim.py": SLOW})})
    yield api
    await api.platform.functions.close()


async def test_limits_are_shown_but_cannot_be_set_from_the_api(slow):
    listing = await slow.studio.get(f"{ENV}/function-limits")
    row = next(r for r in listing["data"] if r["function"] == "lim.sleep")
    assert row["kind"] == "call" and row["limits"]["sources"]["timeout"] == "default" and listing["project"] == {}
    from pawabase_server.core.clients import ServiceError

    with pytest.raises(ServiceError) as refused:
        await slow.studio.put(f"{ENV}/function-limits/lim.sleep", json={"memory": 64})
    assert refused.value.status in (404, 405)


async def test_the_projects_limits_hold_every_run(slow):
    await project_limits(slow, max_function_timeout_seconds=1, max_concurrent_runs_per_function=1)
    for _ in range(50):  # the environment picks up a project's new limits on its next refresh
        listing = await slow.studio.get(f"{ENV}/function-limits")
        if listing["project"].get("max_function_timeout_seconds") == 1:
            break
        await asyncio.sleep(0.1)
    row = next(r for r in listing["data"] if r["function"] == "lim.sleep")
    assert row["limits"]["timeout"] == 1 and row["limits"]["sources"]["timeout"] == "project"

    timed_out = await slow.http.post("/functions/v1/lim.sleep", json={"seconds": 3}, headers=slow.context_headers("development"))
    assert timed_out.status_code == 504 and "1s timeout" in timed_out.json()["message"]
    run = await FunctionRun.filter(function="lim.sleep").first()
    assert run.limit_hit == "timeout" and run.limits["timeout"] == 1 and run.limits["sources"]["timeout"] == "project"


async def test_a_function_over_its_projects_concurrency_waits_then_is_refused(slow):
    await project_limits(slow, max_concurrent_runs_per_function=1)
    slow.platform.settings = slow.platform.settings.model_copy(update={"functions_queue_seconds": 0.2})
    headers = slow.context_headers("development")
    for _ in range(50):
        if (await slow.studio.get(f"{ENV}/function-limits"))["project"].get("max_concurrent_runs_per_function") == 1:
            break
        await asyncio.sleep(0.1)
    first = asyncio.create_task(slow.http.post("/functions/v1/lim.sleep", json={"seconds": 1}, headers=headers))
    await asyncio.sleep(0.2)
    second = await slow.http.post("/functions/v1/lim.sleep", json={"seconds": 0}, headers=headers)
    assert second.status_code == 429 and second.json()["error"] == "too_many_runs"
    assert (await first).status_code == 200


# ── enforced in worker processes (Linux) ─────────────────────────────────────

HEAVY = """
from pawabase_server.functions.registry import function

@function("lim.burn", policy="public")
async def burn(ctx):
    while True:
        pass

@function("lim.hog", policy="public")
async def hog(ctx):
    held = bytearray(400 * 1024 * 1024)
    held[::4096] = b"x" * len(held[::4096])
    import time; time.sleep(5)
    return len(held)

@function("lim.small", policy="public")
async def small(ctx):
    return sum(range(1000))
"""


@linux
async def test_python_runs_are_stopped_at_their_cpu_and_memory_limits(api):
    api.platform.settings = api.platform.settings.model_copy(update={"function_isolation": "process"})
    await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive({"functions/heavy.py": HEAVY})})
    headers = api.context_headers("development")
    try:
        await project_limits(api, max_function_cpu_seconds=1, max_function_memory_mb=150)
        await asyncio.sleep(1.5)
        burned = await api.http.post("/functions/v1/lim.burn", headers=headers)
        run = await FunctionRun.filter(function="lim.burn").first()
        assert burned.status_code == 500 and burned.json()["error"] == "cpu_limit_exceeded", (burned.json(), run.error)
        assert run.limit_hit == "cpu" and run.cpu_ms >= 1000

        hogged = await api.http.post("/functions/v1/lim.hog", headers=headers)
        assert hogged.status_code == 500 and hogged.json()["error"] == "memory_limit_exceeded"
        assert (await FunctionRun.filter(function="lim.hog").first()).limit_hit == "memory"

        fine = await api.http.post("/functions/v1/lim.small", headers=headers)
        assert fine.json() == {"data": 499500}
        run = await FunctionRun.filter(function="lim.small").first()
        assert run.limit_hit is None and run.cpu_ms is not None and run.memory_peak_mb > 0
    finally:
        await api.platform.functions.close()


TS = """
import { fn } from "@pawabase/functions";
export const heavy = fn("ts.heavy", { policy: "public" }, async () => {
  const until = Date.now() + 10_000;
  while (Date.now() < until) {}
  return 1;
});
"""

HTTP_BUSY = """
import { serve } from "@pawabase/functions";
export const config = { timeout: 30 };
serve(async (req) => {
  if (req.method === "POST") return new Response(String((await req.arrayBuffer()).byteLength));
  const until = Date.now() + 10_000;
  while (Date.now() < until) {}   // never yields
  return new Response("done");
});
"""


@linux
@deno
async def test_typescript_functions_are_held_to_their_projects_limits(api):
    await api.studio.post(
        f"{ENV}/function-deployments",
        json={"archive": archive({"functions/heavy.ts": TS, "functions/busy/index.ts": HTTP_BUSY})},
    )
    headers = api.context_headers("development")
    await project_limits(api, max_function_cpu_seconds=1, max_function_body_mb=1, max_function_memory_mb=256)
    await asyncio.sleep(1.5)
    try:
        listing = await api.studio.get(f"{ENV}/function-limits")
        busy = next(r for r in listing["data"] if r["function"] == "busy")
        assert busy["kind"] == "http" and busy["limits"]["cpu"] == 1 and busy["limits"]["max_body"] == 1
        assert busy["limits"]["sources"]["cpu"] == "project"

        burned = await api.http.post("/functions/v1/ts.heavy", headers=headers)
        ts_run = await FunctionRun.filter(function="ts.heavy").first()
        assert burned.status_code == 500 and burned.json()["error"] == "cpu_limit_exceeded", (burned.json(), ts_run.error)

        stuck = await api.http.get("/functions/v1/busy", headers=headers)
        assert stuck.status_code == 500 and stuck.json()["error"] == "cpu_limit_exceeded"
        for _ in range(50):
            run = await FunctionRun.filter(function="busy", method="GET").first()
            if run is not None:
                break
            await asyncio.sleep(0.1)
        assert run.limit_hit == "cpu"

        small = await api.http.post("/functions/v1/busy", content=b"x" * 1000, headers=headers)
        assert small.text == "1000"
        large = await api.http.post("/functions/v1/busy", content=b"x" * (2 * 1024 * 1024), headers=headers)
        assert large.status_code == 413 and large.json()["error"] == "payload_too_large"
    finally:
        await api.platform.functions.close()
