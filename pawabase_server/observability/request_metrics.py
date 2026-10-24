"""Per-environment request counts, persisted for Studio's overview charts.

Telemetry keeps recent requests in memory only. This sink rolls every
data-plane request (one carrying an environment) into per-minute
counters: ``pawabase.requests`` tagged by status class, with the summed
duration as the value, so a window's average latency is ``value / count``.

Requests are aggregated in memory and written every few seconds, so serving a
request never waits on a metrics write.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from tortoise import connections
from tortoise.expressions import F

from pawabase_server.core.ids import new_ulid
from pawabase_server.core.telemetry import RequestRecord, Telemetry
from pawabase_server.db.models import MetricCounter, RequestLog

REQUESTS_METRIC = "pawabase.requests"
#: Bytes moved per minute, tagged ``dir=in`` (received) or ``dir=out`` (sent); ``count`` is the requests that moved them.
BANDWIDTH_METRIC = "pawabase.bandwidth"
FLUSH_SECONDS = 5.0
PRUNE_SECONDS = 3600.0

logger = logging.getLogger("pawabase.api.metrics")

Key = tuple[str, datetime, str]


_COLUMNS = (
    "created_at, updated_at, id, request_id, service, env, method, path, route, status, duration_ms, started_at, "
    '"role", "user", ip, user_agent, error, notes, bytes_in, bytes_out'
)
_INSERT = (
    f"INSERT INTO pb_request_logs ({_COLUMNS}) VALUES ("
    + ", ".join(f"${n}::jsonb" if n == 18 else f"${n}" for n in range(1, 21))
    + ")"
)


async def _store_requests(requests: list[RequestRecord]) -> None:
    """Write a batch of request records. On Postgres they go straight in as one prepared statement: building a model object for each
    request costs more than the insert, and every request pays it."""
    connection = connections.get(RequestLog._meta.default_connection or "default")
    if getattr(connection.capabilities, "dialect", "") != "postgres":
        await RequestLog.bulk_create(
            [
                RequestLog(
                    request_id=record.request_id or "unknown",
                    service=record.service,
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
                    bytes_in=record.bytes_in,
                    bytes_out=record.bytes_out,
                )
                for record in requests
            ]
        )
        return
    now = datetime.now(UTC)
    await connection.execute_many(
        _INSERT,
        [
            (
                now,
                now,
                new_ulid(),
                record.request_id or "unknown",
                record.service,
                record.env or "",
                record.method,
                record.path,
                record.route,
                record.status,
                record.duration_ms,
                record.started_at,
                record.role,
                record.user,
                record.ip,
                record.user_agent,
                record.error,
                json.dumps(record.notes or {}, default=str),
                record.bytes_in,
                record.bytes_out,
            )
            for record in requests
        ],
    )


class RequestRollup:
    """Telemetry sink that buffers per-minute request counts and flushes them."""

    def __init__(self, interval: float = FLUSH_SECONDS, *, retention_days: int = 0) -> None:
        self.interval = interval
        self.retention_days = retention_days
        self._last_prune = 0.0
        self.pending: dict[Key, list[float]] = defaultdict(lambda: [0, 0.0])
        self.traffic: dict[Key, list[float]] = defaultdict(lambda: [0, 0.0])
        self.requests: list[RequestRecord] = []
        self._task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()

    def attach(self, telemetry: Telemetry) -> RequestRollup:
        telemetry.add_sink(self.record)
        return self

    async def record(self, record: RequestRecord) -> None:
        if not record.env:
            return  # management-plane calls aren't an environment's traffic
        window = datetime.now(UTC).replace(second=0, microsecond=0)
        key = (record.env, window, f"status={record.status // 100}xx")
        bucket = self.pending[key]
        bucket[0] += 1
        bucket[1] += record.duration_ms
        for direction, size in (("in", record.bytes_in), ("out", record.bytes_out)):
            if size:
                moved = self.traffic[(record.env, window, f"dir={direction}")]
                moved[0] += 1
                moved[1] += size
        self.requests.append(record)

    async def flush(self) -> None:
        async with self._flush_lock:
            await self._flush_pending()

    async def _flush_pending(self) -> None:
        pending, self.pending = self.pending, defaultdict(lambda: [0, 0.0])
        traffic, self.traffic = self.traffic, defaultdict(lambda: [0, 0.0])
        requests, self.requests = self.requests, []
        if requests:
            try:
                await _store_requests(requests)
            except Exception:
                logger.exception("could not store request history")
        for name, counters in ((REQUESTS_METRIC, pending), (BANDWIDTH_METRIC, traffic)):
            await self._store(name, counters)

    async def _store(self, name: str, counters: dict[Key, list[float]]) -> None:
        for (env, window, tags), (count, total_ms) in counters.items():
            match = MetricCounter.filter(env=env, name=name, tags=tags, window=window)
            try:
                if not await match.update(value=F("value") + total_ms, count=F("count") + count):
                    try:
                        await MetricCounter.create(
                            env=env,
                            name=name,
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
