"""Keeping the worker and the scheduler running as child processes of ``serve``.

Each is a command of the same image (``pawabase-server worker`` / ``scheduler``). A child that exits is started again after a pause that
grows with each quick failure and resets once it has stayed up for a while, so a crash loop is slow rather than a spin. When the server
stops, children are asked to finish (SIGTERM, so a worker completes the job it is running) and only killed after a grace period.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("pawabase.supervisor")

HEALTHY_AFTER = 60.0
MIN_PAUSE = 1.0
MAX_PAUSE = 30.0
GRACE = 20.0

Spawn = Callable[[list[str]], Awaitable[asyncio.subprocess.Process]]


async def spawn_process(argv: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(*argv)


def next_pause(previous: float, lived: float) -> float:
    """How long to wait before restarting: the first failure waits a second, each quick one doubles it, a long run resets it."""
    if lived >= HEALTHY_AFTER:
        return MIN_PAUSE
    return min(MAX_PAUSE, max(MIN_PAUSE, previous * 2))


@dataclass
class Child:
    name: str
    argv: list[str]
    process: asyncio.subprocess.Process | None = None
    restarts: int = 0
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class Supervisor:
    def __init__(
        self, *, spawn: Spawn = spawn_process, clock: Callable[[], float] | None = None
    ) -> None:
        self._spawn = spawn
        self._clock = clock or asyncio.get_running_loop().time
        self._children: dict[str, Child] = {}
        self._stopping = asyncio.Event()

    def add(self, name: str, *command: str) -> Child:
        child = Child(name, [sys.executable, "-m", "pawabase_server", *command])
        self._children[name] = child
        return child

    @property
    def children(self) -> dict[str, Child]:
        return dict(self._children)

    def start(self) -> None:
        for child in self._children.values():
            child.task = asyncio.create_task(self._keep(child), name=f"supervise-{child.name}")

    async def _keep(self, child: Child) -> None:
        pause = MIN_PAUSE / 2
        while not self._stopping.is_set():
            started = self._clock()
            try:
                child.process = await self._spawn(child.argv)
                logger.info("%s started (pid %s)", child.name, child.process.pid)
                code = await child.process.wait()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s could not be started", child.name)
                code = -1
            if self._stopping.is_set():
                return
            lived = self._clock() - started
            pause = next_pause(pause, lived)
            child.restarts += 1
            logger.warning(
                "%s exited with %s after %.0fs; starting it again in %.0fs",
                child.name,
                code,
                lived,
                pause,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=pause)

    async def stop(self, grace: float = GRACE) -> None:
        self._stopping.set()
        for child in self._children.values():
            process = child.process
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(signal.SIGTERM)
        for child in self._children.values():
            process = child.process
            if process is not None and process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=grace)
                except TimeoutError:
                    logger.warning("%s did not stop within %.0fs; killing it", child.name, grace)
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
        for child in self._children.values():
            if child.task is not None:
                child.task.cancel()
                with contextlib.suppress(BaseException):
                    await child.task
