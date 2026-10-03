"""Per-environment request counts, persisted for Studio's overview charts.

Telemetry keeps recent requests in memory only. This sink rolls every
data-plane request (one carrying a project and environment) into per-minute
counters: ``pawabase.requests`` tagged by status class, with the summed
duration as the value, so a window's average latency is ``value / count``.

Requests are aggregated in memory and written every few seconds, so serving a
request never waits on a metrics write.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
import time
from datetime import UTC, datetime, timedelta

from tortoise.expressions import F

from database.models import MetricCounter, RequestLog
from pawabase_core.telemetry import RequestRecord, Telemetry

REQUESTS_METRIC = "pawabase.requests"
FLUSH_SECONDS = 5.0
PRUNE_SECONDS = 3600.0

logger = logging.getLogger("pawabase.api.metrics")

Key = tuple[str, str, datetime, str]


class RequestRollup:
    """Telemetry sink that buffers per-minute request counts and flushes them."""

    def __init__(self, interval: float = FLUSH_SECONDS, *, retention_days: int = 0) -> None:
        self.interval = interval
        self.retention_days = retention_days
        self._last_prune = 0.0
        self.pending: dict[Key, list[float]] = defaultdict(lambda: [0, 0.0])
        self.requests: list[RequestRecord] = []
        self._task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()

    def attach(self, telemetry: Telemetry) -> RequestRollup:
        telemetry.add_sink(self.record)
        return self

    async def record(self, record: RequestRecord) -> None:
        if not record.project or not record.env:
            return  # management-plane calls aren't an environment's traffic
        window = datetime.now(UTC).replace(second=0, microsecond=0)
        key = (record.project, record.env, window, f"status={record.status // 100}xx")
        bucket = self.pending[key]
        bucket[0] += 1
        bucket[1] += record.duration_ms
        self.requests.append(record)

    async def flush(self) -> None:
        async with self._flush_lock:
            await self._flush_pending()

    async def _flush_pending(self) -> None:
        pending, self.pending = self.pending, defaultdict(lambda: [0, 0.0])
        requests, self.requests = self.requests, []
        if requests:
            try:
                await RequestLog.bulk_create(
                    [
                        RequestLog(
                            request_id=record.request_id or "unknown",
                            service=record.service,
                            project=record.project or "",
                            env=record.env or "",
                            method=record.method,
                            path=record.path,
                            route=record.route,
                            status=record.status,
                            duration_ms=record.duration_ms,
                            started_at=record.started_at,
                            role=record.role,
                            user=record.user,
                            ip=record.ip,
                            user_agent=record.user_agent,
                            error=record.error,
                            notes=record.notes,
                        )
                        for record in requests
                    ]
                )
            except Exception:
                logger.exception("could not store request history")
        for (project, env, window, tags), (count, total_ms) in pending.items():
            match = MetricCounter.filter(
                project=project, env=env, name=REQUESTS_METRIC, tags=tags, window=window
            )
            try:
                if not await match.update(value=F("value") + total_ms, count=F("count") + count):
                    try:
                        await MetricCounter.create(
                            project=project,
                            env=env,
                            name=REQUESTS_METRIC,
                            tags=tags,
                            window=window,
                            value=total_ms,
                            count=int(count),
                        )
                    except Exception:  # another process created the row first
                        await match.update(value=F("value") + total_ms, count=F("count") + count)
            except Exception:
                logger.exception("could not store request metrics")

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.flush()

    async def prune(self) -> int:
        """Delete request history older than the retention window (``0`` keeps everything).

        Returns:
            How many request rows were removed.
        """
        if self.retention_days <= 0:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        try:
            return await RequestLog.filter(started_at__lt=cutoff).delete()
        except Exception:
            logger.exception("could not prune request history")
            return 0

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            await self.flush()
            if time.monotonic() - self._last_prune >= PRUNE_SECONDS:
                self._last_prune = time.monotonic()
                await self.prune()
