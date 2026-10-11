"""The queue worker and the scheduler are processes of their own, not tasks of the server."""

import asyncio
import signal

import pytest

from pawabase_server import cli
from pawabase_server.automation import runner
from pawabase_server.automation.supervisor import (
    HEALTHY_AFTER,
    MAX_PAUSE,
    MIN_PAUSE,
    Supervisor,
    next_pause,
)
from pawabase_server.server import planned_children
from pawabase_server.settings import Settings


def settings(**kw):
    return Settings(_env_file=None, app_env="testing", **kw)


def test_neither_runs_inside_the_server_unless_asked():
    default = settings()
    assert default.inline_worker is False and default.inline_scheduler is False
    assert default.worker_mode == "process"


def test_a_mode_that_does_not_exist_is_a_configuration_problem():
    problems = settings(worker_mode="thread").validate_for_production()
    assert any("PAWABASE_WORKER_MODE" in p for p in problems)


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, [("worker", "worker"), ("scheduler", "scheduler")]),
        ({"worker_mode": "external"}, []),
        ({"inline_worker": True}, [("scheduler", "scheduler")]),
        ({"inline_scheduler": True}, [("worker", "worker")]),
        ({"inline_worker": True, "inline_scheduler": True}, []),
    ],
)
def test_serve_starts_a_child_process_for_whatever_is_not_inline_or_external(kw, expected):
    assert planned_children(settings(**kw)) == expected


def test_restart_pauses_grow_on_quick_failures_and_reset_after_a_long_run():
    pause = MIN_PAUSE / 2
    seen = []
    for _ in range(8):
        pause = next_pause(pause, lived=0.5)
        seen.append(pause)
    assert seen[0] == MIN_PAUSE and seen[1] == 2 * MIN_PAUSE and seen[-1] == MAX_PAUSE
    assert next_pause(MAX_PAUSE, lived=HEALTHY_AFTER) == MIN_PAUSE


class FakeProcess:
    """A child that ends when told to, or on its own."""

    def __init__(self, pid, *, exits_on_term=True):
        self.pid = pid
        self.returncode = None
        self.signals = []
        self.killed = False
        self._done = asyncio.Event()
        self._exits_on_term = exits_on_term

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def finish(self, code):
        self.returncode = code
        self._done.set()

    def send_signal(self, sig):
        self.signals.append(sig)
        if self._exits_on_term and sig == signal.SIGTERM:
            self.finish(0)

    def kill(self):
        self.killed = True
        self.finish(-9)


async def test_a_child_that_crashes_is_started_again_and_counted():
    started = []

    async def spawn(argv):
        process = FakeProcess(len(started) + 1)
        started.append((argv, process))
        return process

    supervisor = Supervisor(spawn=spawn)
    child = supervisor.add("worker", "worker")
    supervisor.start()
    await asyncio.sleep(0.05)
    assert started[0][0][-2:] == ["pawabase_server", "worker"]
    started[0][1].finish(1)  # it crashes
    for _ in range(40):
        await asyncio.sleep(0.05)
        if len(started) == 2:
            break
    assert len(started) == 2 and child.restarts == 1
    await supervisor.stop(grace=1)


async def test_stopping_asks_children_to_finish_and_only_then_kills():
    polite, stubborn = FakeProcess(1), FakeProcess(2, exits_on_term=False)
    queue = [polite, stubborn]

    async def spawn(argv):
        return queue.pop(0)

    supervisor = Supervisor(spawn=spawn)
    supervisor.add("worker", "worker")
    supervisor.add("scheduler", "scheduler")
    supervisor.start()
    await asyncio.sleep(0.05)
    await supervisor.stop(grace=0.1)
    assert polite.signals == [signal.SIGTERM] and not polite.killed
    assert stubborn.signals == [signal.SIGTERM] and stubborn.killed


async def test_a_child_that_cannot_be_spawned_does_not_stop_the_supervisor():
    calls = []

    async def spawn(argv):
        calls.append(1)
        raise OSError("no such interpreter")

    supervisor = Supervisor(spawn=spawn)
    supervisor.add("worker", "worker")
    supervisor.start()
    await asyncio.sleep(0.05)
    assert len(calls) >= 1
    await supervisor.stop(grace=0.1)


def test_the_command_line_has_the_two_commands(capsys):
    assert cli.main(["bogus"]) == 64
    usage = capsys.readouterr().err
    assert "worker" in usage and "scheduler" in usage


async def test_the_worker_process_reports_itself_and_stops_on_request(api):
    from pawabase_server.db.models import WorkerHeartbeat

    stop = asyncio.Event()
    task = asyncio.create_task(runner.work(api.platform, api.settings, stop))
    row = None
    for _ in range(100):
        await asyncio.sleep(0.05)
        row = await WorkerHeartbeat.filter(kind="worker", name__startswith="worker:").first()
        if row:
            break
    assert row is not None and row.status == "running"
    stop.set()
    await asyncio.wait_for(task, timeout=15)
    row = await WorkerHeartbeat.get(name=row.name)
    assert row.status == "stopped"


async def test_the_scheduler_process_reports_itself_and_stops_on_request(api):
    from pawabase_server.db.models import WorkerHeartbeat

    stop = asyncio.Event()
    task = asyncio.create_task(runner.schedule(api.platform, stop))
    row = None
    for _ in range(100):
        await asyncio.sleep(0.05)
        row = await WorkerHeartbeat.filter(kind="scheduler").first()
        if row:
            break
    assert row is not None and row.status == "running"
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert (await WorkerHeartbeat.get(name=row.name)).status == "stopped"


async def test_a_child_stops_when_its_parent_is_gone(monkeypatch):
    parents = iter([100, 100, 1, 1, 1])
    monkeypatch.setattr(runner.os, "getppid", lambda: next(parents))
    stop = asyncio.Event()
    await asyncio.wait_for(runner._leave_with_parent(stop, interval=0.01), timeout=2)
    assert stop.is_set()


async def test_a_process_started_by_an_init_keeps_running(monkeypatch):
    monkeypatch.setattr(runner.os, "getppid", lambda: 1)  # its parent was always 1: nothing changed
    stop = asyncio.Event()
    task = asyncio.create_task(runner._leave_with_parent(stop, interval=0.01))
    await asyncio.sleep(0.1)
    assert not stop.is_set()
    stop.set()
    await task
