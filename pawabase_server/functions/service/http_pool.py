"""Workers for HTTP functions: how many run, which one answers a request, and when they stop.

* **Per function.** Each HTTP function of each deployment (and each set of secrets) has workers of its own, like an isolate per function:
  one function's memory, crash or slow code does not touch another's.
* **Concurrent.** A worker serves up to ``concurrency`` requests at once (its ``config.concurrency``, or the installation's). The least
  busy worker with room takes the next request; when all are full another is started, up to ``functions_max_workers``; beyond that a
  request waits up to ``functions_queue_seconds`` for room and then gets ``503``.
* **Warm.** ``config.warm`` workers are started when a deployment is first seen and kept while idle, so their requests never wait for a
  cold start. Other idle workers stop after ``sandbox_idle_seconds``.
* **Draining.** A new deployment, or new secrets, make the old workers *draining*: they take no new requests, finish what they have
  (including ``waitUntil`` work) and stop.
* **Statistics** per function: requests, errors, cold starts, rejections and latency percentiles, for Studio and the management API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from pawabase_server.functions.service.egress import Egress
from pawabase_server.functions.service.http_worker import HttpWorker, WorkerSpec

logger = logging.getLogger("pawabase.functions")

REAP_SECONDS = 10.0
LATENCY_SAMPLES = 1000

Group = tuple[str, str, str]


class PoolBusy(Exception):
    """Every worker of the function is full and no more may be started; the request waited and gave up."""

    def __init__(self, function: str, waited: float) -> None:
        super().__init__(f"{function} is at capacity; waited {waited:.1f}s for room")
        self.function = function
        self.waited = waited


@dataclass
class FunctionStats:
    requests: int = 0
    errors: int = 0
    cold_starts: int = 0
    rejected: int = 0
    timeouts: int = 0
    started_workers: int = 0
    crashed_workers: int = 0
    last_error: str | None = None
    last_error_at: float | None = None
    latencies: deque = field(default_factory=lambda: deque(maxlen=LATENCY_SAMPLES))
    cold_start_ms: deque = field(default_factory=lambda: deque(maxlen=100))

    def record(self, duration_ms: float, *, failed: bool, error: str | None = None) -> None:
        self.requests += 1
        self.latencies.append(duration_ms)
        if failed:
            self.errors += 1
            if error:
                self.last_error, self.last_error_at = error[:500], time.time()

    @staticmethod
    def _percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return round(ordered[index], 2)

    def describe(self) -> dict[str, Any]:
        samples = list(self.latencies)
        cold = list(self.cold_start_ms)
        return {
            "requests": self.requests,
            "errors": self.errors,
            "error_rate": round(self.errors / self.requests, 4) if self.requests else 0.0,
            "cold_starts": self.cold_starts,
            "rejected": self.rejected,
            "timeouts": self.timeouts,
            "workers_started": self.started_workers,
            "workers_crashed": self.crashed_workers,
            "latency_ms": {
                "p50": self._percentile(samples, 0.5),
                "p95": self._percentile(samples, 0.95),
                "p99": self._percentile(samples, 0.99),
                "samples": len(samples),
            },
            "cold_start_ms": {"p50": self._percentile(cold, 0.5), "p95": self._percentile(cold, 0.95)},
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
        }


class HttpPool:
    def __init__(
        self,
        *,
        egress: Egress,
        max_workers: int = 8,
        queue_seconds: float = 10.0,
        idle_seconds: float = 300.0,
        reap_seconds: float = REAP_SECONDS,
    ) -> None:
        self.egress = egress
        self.max_workers = max_workers
        self.queue_seconds = queue_seconds
        self.idle_seconds = idle_seconds
        self.reap_seconds = reap_seconds
        self.workers: dict[Group, list[HttpWorker]] = {}
        self.starting: dict[Group, int] = {}
        self.warm: dict[Group, tuple[WorkerSpec, int]] = {}
        self.stats: dict[Group, FunctionStats] = {}
        self._conditions: dict[Group, asyncio.Condition] = {}
        self._reaper: asyncio.Task | None = None
        self._warming: set[asyncio.Task] = set()
        self._closed = False

    # ── bookkeeping ──────────────────────────────────────────────────────

    @staticmethod
    def group_of(spec: WorkerSpec) -> Group:
        return (spec.env, spec.branch, spec.function)

    def _condition(self, group: Group) -> asyncio.Condition:
        return self._conditions.setdefault(group, asyncio.Condition())

    def stats_for(self, group: Group) -> FunctionStats:
        return self.stats.setdefault(group, FunctionStats())

    def _current(self, group: Group, spec: WorkerSpec) -> list[HttpWorker]:
        return [w for w in self.workers.get(group, []) if w.alive and not w.draining and w.spec.key == spec.key]

    def _retire_others(self, group: Group, spec: WorkerSpec) -> None:
        """Workers of another deployment or other secrets take no new requests; the reaper stops them once they are idle."""
        for worker in self.workers.get(group, []):
            if worker.spec.key != spec.key and not worker.draining:
                worker.draining = True
                logger.info(
                    "worker %s of %s/%s is draining (replaced by deployment %s)",
                    worker.id,
                    group[0],
                    group[2],
                    spec.deployment_id,
                )

    def _ceiling(self, spec: WorkerSpec) -> int:
        """Workers *spec*'s function may run: its project's limit, never more than the installation's."""
        return min(spec.max_workers, self.max_workers) if spec.max_workers > 0 else self.max_workers

    def notify(self, group: Group) -> None:
        """Room may have opened on *group*: wake the requests waiting for it."""
        condition = self._conditions.get(group)
        if condition is None:
            return

        async def wake() -> None:
            async with condition:
                condition.notify_all()

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(wake())

    def _on_exit(self, worker: HttpWorker) -> None:
        group = self.group_of(worker.spec)
        if not worker.draining:
            self.stats_for(group).crashed_workers += 1
        self.notify(group)

    # ── taking a worker ──────────────────────────────────────────────────

    async def acquire(self, spec: WorkerSpec) -> tuple[HttpWorker, bool]:
        """A worker of *spec* with room for one more request, and whether it was started for this request (a cold start)."""
        if self._closed:
            raise RuntimeError("the function pool is closed")
        group = self.group_of(spec)
        self._retire_others(group, spec)
        self._ensure_reaper()
        condition = self._condition(group)
        waited_from = time.monotonic()
        deadline = waited_from + self.queue_seconds
        async with condition:
            while True:
                ready = [w for w in self._current(group, spec) if w.has_room]
                if ready:
                    return min(ready, key=lambda w: w.active), False
                running = len(self._current(group, spec)) + self.starting.get(group, 0)
                if running < self._ceiling(spec):
                    self.starting[group] = self.starting.get(group, 0) + 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stats_for(group).rejected += 1
                    raise PoolBusy(spec.function, time.monotonic() - waited_from)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(condition.wait(), remaining)
        try:
            worker = await self._start(spec)
        finally:
            self.starting[group] -= 1
        self.notify(group)
        return worker, True

    async def _start(self, spec: WorkerSpec) -> HttpWorker:
        group = self.group_of(spec)
        worker = HttpWorker(spec, egress=self.egress, on_exit=self._on_exit)
        started = time.monotonic()
        await worker.start()
        stats = self.stats_for(group)
        stats.started_workers += 1
        stats.cold_start_ms.append(round((time.monotonic() - started) * 1000, 2))
        self.workers.setdefault(group, []).append(worker)
        logger.info(
            "started worker %s for %s/%s (%s) in %.0f ms",
            worker.id,
            spec.env,
            spec.function,
            spec.deployment_id,
            (time.monotonic() - started) * 1000,
        )
        return worker

    # ── warm workers ─────────────────────────────────────────────────────

    def keep_warm(self, spec: WorkerSpec, count: int) -> None:
        """Keep *count* workers of *spec* running while idle (``config.warm``), starting them in the background now."""
        group = self.group_of(spec)
        if count <= 0:
            self.warm.pop(group, None)
            return
        known = self.warm.get(group)
        self.warm[group] = (spec, count)
        if known is not None and known[0].key == spec.key and len(self._current(group, spec)) >= count:
            return
        task = asyncio.get_running_loop().create_task(self._warm_up(spec, count))
        self._warming.add(task)
        task.add_done_callback(self._warming.discard)

    async def _warm_up(self, spec: WorkerSpec, count: int) -> None:
        group = self.group_of(spec)
        self._retire_others(group, spec)
        self._ensure_reaper()
        while len(self._current(group, spec)) + self.starting.get(group, 0) < min(count, self._ceiling(spec)):
            self.starting[group] = self.starting.get(group, 0) + 1
            try:
                await self._start(spec)
            except Exception as exc:  # noqa: BLE001 - a warm worker that cannot start is retried by the next request, which reports why
                logger.warning("could not start a warm worker for %s/%s: %s", spec.env, spec.function, exc)
                return
            finally:
                self.starting[group] -= 1
            self.notify(group)

    # ── stopping ─────────────────────────────────────────────────────────

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap_loop())

    async def _reap_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(self.reap_seconds)
            try:
                await self.reap()
            except Exception:  # noqa: BLE001 - the reaper must keep running
                logger.exception("reaping function workers failed")

    async def reap(self) -> int:
        """Stop dead, drained and idle workers (keeping the warm ones). Returns how many were stopped."""
        stopped = 0
        now = time.monotonic()
        for group, workers in list(self.workers.items()):
            warm_spec, warm_count = self.warm.get(group, (None, 0))
            keep_warm = sorted(
                (w for w in workers if w.alive and not w.draining and warm_spec is not None and w.spec.key == warm_spec.key),
                key=lambda w: w.started_at,
                reverse=True,
            )[:warm_count]
            for worker in list(workers):
                idle = not worker.busy and now - worker.last_used > self.idle_seconds
                if (
                    not worker.alive
                    or (worker.draining and not worker.busy)
                    or (idle and worker not in keep_warm)
                ):
                    workers.remove(worker)
                    await worker.stop()
                    stopped += 1
            if not workers:
                self.workers.pop(group, None)
            self.notify(group)
        return stopped

    async def restart(self, env: str | None = None, function: str | None = None) -> int:
        """Drain the workers of *function* (or of every function of *env*, or all): the next request starts fresh ones."""
        count = 0
        for (group_env, _branch, group_function), workers in self.workers.items():
            if env is not None and group_env != env:
                continue
            if function is not None and group_function != function:
                continue
            for worker in workers:
                if not worker.draining:
                    worker.draining = True
                    count += 1
        for group, (spec, warm) in list(self.warm.items()):
            if (env is None or group[0] == env) and (function is None or group[2] == function):
                task = asyncio.get_running_loop().create_task(self._warm_up(spec, warm))
                self._warming.add(task)
                task.add_done_callback(self._warming.discard)
        await self.reap()
        return count

    async def close(self) -> None:
        self._closed = True
        if self._reaper is not None:
            self._reaper.cancel()
        for task in list(self._warming):
            task.cancel()
        for workers in self.workers.values():
            for worker in workers:
                with contextlib.suppress(Exception):
                    await worker.stop()
        self.workers.clear()

    # ── what is running ──────────────────────────────────────────────────

    def describe(self, env: str | None = None) -> dict[str, Any]:
        functions: list[dict[str, Any]] = []
        groups = set(self.workers) | set(self.stats) | set(self.warm)
        for group in sorted(groups):
            if env is not None and group[0] != env:
                continue
            workers = self.workers.get(group, [])
            functions.append(
                {
                    "env": group[0],
                    "branch": group[1],
                    "function": group[2],
                    "warm": self.warm.get(group, (None, 0))[1],
                    "starting": self.starting.get(group, 0),
                    "workers": [w.describe() for w in workers],
                    "stats": self.stats_for(group).describe(),
                }
            )
        return {
            "max_workers": self.max_workers,
            "queue_seconds": self.queue_seconds,
            "idle_seconds": self.idle_seconds,
            "functions": functions,
            "workers": sum(len(w) for w in self.workers.values()),
        }
