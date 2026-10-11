"""The queue worker: a task of the server process.

Runs Sillo :class:`~sillo.work.queue.QueueWorker`\\s over the platform queues
(plus any listed in ``PAWABASE_WORKER_QUEUES``), consumes platform events with
the event processor, and reports a heartbeat Studio shows under Workers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from sillo.work.queue import ConnectionManager, PayloadSerializer, QueueWorker, WorkerOptions

from pawabase_server.automation.jobs.failed import RecordFailedJobRepository
from pawabase_server.core.records import upsert
from pawabase_server.platform import PLATFORM_QUEUES, Platform

logger = logging.getLogger("pawabase.worker")


def worker_queues() -> list[str]:
    extra = [q.strip() for q in os.getenv("PAWABASE_WORKER_QUEUES", "").split(",") if q.strip()]
    return PLATFORM_QUEUES + [q for q in extra if q not in PLATFORM_QUEUES]


def build_worker(
    platform: Platform, *, queues: list[str] | None = None, concurrency: int = 4, sleep: float = 1.0
) -> QueueWorker:
    import pawabase_server.automation.jobs  # noqa: F401  (every job class must be importable by name)

    queues = queues or worker_queues()
    manager = ConnectionManager()
    for name in queues:
        # The worker asks for a connection by queue name; one shared connection serves all.
        manager.add(name, platform.queue)
    return QueueWorker(
        manager,
        PayloadSerializer(),
        RecordFailedJobRepository(),
        options=WorkerOptions(concurrency=concurrency, queues=queues, sleep=sleep, timeout=300.0),
    )


class InlineWorker:
    """A worker running inside the API process (single-process setups)."""

    def __init__(self, platform: Platform) -> None:
        self.worker = build_worker(platform, concurrency=2, sleep=0.2)
        self.task: asyncio.Task | None = None

    def start(self) -> InlineWorker:
        # Signal handlers belong to the server, not to a worker inside it.
        self.worker._register_signals = lambda: None  # type: ignore[method-assign]
        self.task = asyncio.create_task(self.worker.run(), name="pawabase-inline-worker")
        return self

    def stop(self) -> None:
        self.worker.stop()


def start_inline_worker(platform: Platform) -> InlineWorker:
    return InlineWorker(platform).start()


async def heartbeat(name: str, queues: list[str], worker: QueueWorker, stop: asyncio.Event) -> None:
    from pawabase_server.db.models import WorkerHeartbeat

    started = datetime.now(UTC)
    while not stop.is_set():
        await upsert(
            WorkerHeartbeat,
            name=name,
            defaults={
                "queues": queues,
                "started_at": started,
                "last_seen": datetime.now(UTC),
                "processed": worker._jobs_processed,
                "concurrency": worker.options.concurrency,
                "status": "paused" if worker._paused else "running",
            },
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except TimeoutError:
            pass
    await WorkerHeartbeat.filter(name=name).update(status="stopped", last_seen=datetime.now(UTC))
