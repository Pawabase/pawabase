"""Measuring and enforcing a worker's CPU time and memory from outside the worker.

The runtime reads a worker's own accounting from ``/proc/<pid>/stat`` (CPU time) and ``/proc/<pid>/status`` (resident and peak
memory) while a run is in progress and stops the worker the moment it goes over a limit. Reading from outside means the function cannot
turn it off, the same code serves Python and Deno workers, and nothing is added to the function's own process.

``/proc`` is Linux. Elsewhere (macOS, during development) usage is not measured, CPU and memory limits are not enforced, and the run
records ``None`` for what it used; the timeout always applies. :func:`supported` says which it is.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")
TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") and "SC_CLK_TCK" in os.sysconf_names else 100
#: How often a run is checked. Small enough that a limit is overshot by a fraction of a second at most.
INTERVAL = 0.05


def supported() -> bool:
    return (PROC / "self" / "stat").exists()


@dataclass
class Usage:
    cpu_seconds: float
    rss_mb: float
    peak_mb: float


def read(pid: int) -> Usage | None:
    """The process's CPU time so far and its memory, or ``None`` when it cannot be read (gone, or no ``/proc``)."""
    try:
        stat = (PROC / str(pid) / "stat").read_text()
        status = (PROC / str(pid) / "status").read_text()
    except OSError:
        return None
    # The command name may hold spaces and parentheses: the fields start after the last ")".
    fields = stat[stat.rfind(")") + 2 :].split()
    try:
        cpu = (int(fields[11]) + int(fields[12])) / TICKS  # utime, stime
    except (IndexError, ValueError):
        return None
    rss = peak = 0.0
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1]) / 1024
        elif line.startswith("VmHWM:"):
            peak = int(line.split()[1]) / 1024
    return Usage(cpu, rss, max(peak, rss))


class Watch:
    """Watches one process during one run. ``hit`` names the limit that stopped it (``cpu`` or ``memory``), if one did.

    *cpu_seconds* is measured from when the watch starts, so a worker that served earlier calls starts each run from zero. *memory_mb*
    is resident memory. Both ``None``/``0`` mean "not limited here".
    """

    def __init__(
        self,
        pid: int,
        *,
        cpu_seconds: float | None,
        memory_mb: float | None,
        on_hit: Callable[[str, str], None] | None = None,
        interval: float = INTERVAL,
    ) -> None:
        self.pid = pid
        self.cpu_limit = cpu_seconds or 0
        self.memory_limit = memory_mb or 0
        self.on_hit = on_hit
        self.interval = interval
        self.hit: str | None = None
        self.reason: str | None = None
        self.start = read(pid)
        self.cpu_used: float | None = 0.0 if self.start else None
        self.peak_mb: float | None = self.start.rss_mb if self.start else None
        self.started = time.monotonic()
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> Watch:
        if self.start is not None:
            self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.sample()

    def sample(self) -> None:
        now = read(self.pid)
        if now is None or self.start is None:
            return
        self.cpu_used = max(0.0, now.cpu_seconds - self.start.cpu_seconds)
        self.peak_mb = max(self.peak_mb or 0.0, now.rss_mb)

    async def _loop(self) -> None:
        while self.hit is None:
            await asyncio.sleep(self.interval)
            self.sample()
            if self.cpu_limit and self.cpu_used is not None and self.cpu_used > self.cpu_limit:
                self._stop("cpu", f"it used more than {self.cpu_limit:g}s of CPU time")
            elif self.memory_limit and self.peak_mb is not None and self.peak_mb > self.memory_limit:
                self._stop("memory", f"it used more than {self.memory_limit:g} MB of memory")

    def _stop(self, limit: str, reason: str) -> None:
        self.hit, self.reason = limit, reason
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(self.pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(self.pid, signal.SIGKILL)
        if self.on_hit is not None:
            self.on_hit(limit, reason)

    def usage(self) -> dict[str, float | None]:
        return {
            "cpu_ms": round(self.cpu_used * 1000, 2) if self.cpu_used is not None else None,
            "memory_peak_mb": round(self.peak_mb, 2) if self.peak_mb is not None else None,
        }
