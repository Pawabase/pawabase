"""Run records, written in batches off the request.

Every function call and flow run leaves a record (its input, output, logs and timing) for Studio's run history. Writing each one before the
request returns costs a database round trip on the hot path, about a third of the API's time for a quick function. This buffer takes the record
when the call ends and writes what has gathered every half second, one ``bulk_create`` per kind of record, so a request never waits on it.

A record is observability, not business data: losing the last half second to a crash is acceptable, and the buffer is bounded so a database
that is down cannot grow it without limit. ``record_buffer_seconds=0`` writes each record at once, as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger("pawabase.api.records")

#: Records that make a flush start at once instead of waiting for the interval.
BATCH = 100
#: Most records held. Past this the oldest are dropped (and counted).
LIMIT = 10_000


class RecordBuffer:
    """Collects model instances and writes them in batches."""

    def __init__(self, interval: float = 0.5, *, batch: int = BATCH, limit: int = LIMIT) -> None:
        self.interval = interval
        self.batch = batch
        self.limit = limit
        self.pending: list[Any] = []
        self.dropped = 0
        self.written = 0
        self._task: asyncio.Task | None = None
        self._flush_lock = asyncio.Lock()
        self._kicked: asyncio.Task | None = None

    async def add(self, record: Any) -> None:
        """Take a record to write. With an interval of 0 it is written before this returns."""
        if self.interval <= 0:
            await self._write([record])
            return
        self.pending.append(record)
        if len(self.pending) > self.limit:
            del self.pending[: len(self.pending) - self.limit]
            self.dropped += 1
        if len(self.pending) >= self.batch and (self._kicked is None or self._kicked.done()):
            self._kicked = asyncio.create_task(self.flush())

    async def flush(self) -> None:
        """Write everything gathered so far."""
        async with self._flush_lock:
            batch, self.pending = self.pending, []
            if batch:
                await self._write(batch)

    async def _write(self, records: list[Any]) -> None:
        by_model: dict[type, list[Any]] = defaultdict(list)
        for record in records:
            by_model[type(record)].append(record)
        for model, rows in by_model.items():
            try:
                await model.bulk_create(rows)
                self.written += len(rows)
            except Exception as exc:
                # Observability must not fail anything: say once what was lost and why (the short form, not the traceback).
                logger.error(
                    "could not record %d %s: %s: %s",
                    len(rows),
                    model.__name__,
                    type(exc).__name__,
                    exc,
                )

    def start(self) -> None:
        if self.interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Stop the timer and write what is left. Call before the database closes."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._kicked is not None:
            with contextlib.suppress(Exception):
                await self._kicked
        await self.flush()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            await self.flush()
