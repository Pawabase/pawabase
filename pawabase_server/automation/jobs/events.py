"""Processing one platform event as a queued job.

Every event the platform publishes becomes one of these on the ``events`` queue. The queue gives events what a list popped with ``BRPOP``
could not: a job is held with a visibility deadline and only removed when the worker acknowledges it, so a worker that dies mid-event
leaves it to be taken again; a processing failure is retried later with backoff; an event that keeps failing settles as a failed job
that Studio lists and can replay. The processor itself is resumable (see :meth:`EventProcessor.handle`), so a retry does not dispatch a
consumer twice.

Delivery to consumers is therefore **at least once**: a consumer can, rarely, be dispatched twice (a worker dying after dispatching and
before recording it). Consumers receive the event id and should treat it as an idempotency key.
"""

from __future__ import annotations

from typing import Any

from pawabase_server.automation.event_processor import EventProcessor
from pawabase_server.automation.jobs.base import PawabaseJob
from pawabase_server.core.events import PlatformEvent
from pawabase_server.platform import get_platform


class ProcessEventJob(PawabaseJob):
    """Record one event and hand it to its consumers."""

    queue = "events"
    tries = 8
    backoff = 5
    timeout = 120.0

    async def perform(self) -> Any:
        event = PlatformEvent.from_dict(self.params["event"])
        consumers = await EventProcessor(get_platform()).handle(event, strict=True)
        return {"event_id": event.id, "consumers": len(consumers)}
