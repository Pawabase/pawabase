"""Platform events, carried by Sillo's event system.

Every service publishes domain events (``user.created``, ``order.paid``,
``file.uploaded``…) through an :class:`EventBus`. The bus is a thin shell over
:class:`sillo.events.EventEmitter`, and the backend is Sillo's:

* ``memory``: in process; tests and single-process development.
* ``persistent``: a Redis-backed backlog with at-least-once delivery. Events
  published while the API's event processor is down wait for it. The backlog
  is a work queue (each event goes to one consumer), so only buses with
  handlers drain it; a publish-only bus never takes events off it.
* ``redis``: pub/sub fan-out to every instance; Angula uses this for realtime.

All platform events travel on one channel, so a consumer sees every event and
filters by name. Each event is a plain JSON envelope (:class:`PlatformEvent`).
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sillo.events import EventEmitter

logger = logging.getLogger("pawabase.events")

PLATFORM_CHANNEL = "pawabase.events"

Handler = Callable[["PlatformEvent"], Awaitable[None]]


@dataclass(slots=True)
class PlatformEvent:
    """One thing that happened.

    Attributes:
        name: Dotted name, ``<noun>.<verb>`` by convention (``order.paid``).
        project, env: Where it happened.
        payload: Event data. Keep it JSON and keep it small; consumers fetch
            full records when they need them.
        source: The service that published it.
        actor: Who caused it (a user id, ``service``, ``system``).
        id: Unique id, for de-duplication and tracing.
        occurred_at: ISO-8601 UTC timestamp.
        request_id: The request that caused it, when there was one.
        release_id, api_version: The immutable runtime definition that emitted it.
    """

    name: str
    project: str
    env: str
    payload: Any = None
    source: str = "unknown"
    actor: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    request_id: str | None = None
    release_id: str | None = None
    api_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlatformEvent:
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})

    def matches(self, pattern: str) -> bool:
        """Whether ``pattern`` (``order.*``, ``*``) selects this event."""
        return fnmatch.fnmatchcase(self.name, pattern)


class EventBus:
    """Publishes and consumes :class:`PlatformEvent` over a Sillo emitter.

    Args:
        backend: A Sillo event backend name.
        url: Redis URL for networked backends.
        channel: The channel carrying platform events.
        source: This service's name, stamped on events it publishes.
    """

    def __init__(
        self,
        backend: str = "memory",
        *,
        url: str | None = None,
        channel: str = PLATFORM_CHANNEL,
        source: str = "unknown",
        emitter: EventEmitter | None = None,
    ) -> None:
        options: dict[str, Any] = {}
        if url and backend in ("redis", "persistent"):
            options["url"] = url
        self.emitter = emitter or EventEmitter(backend, on_error=self._on_error, **options)
        self.backend = backend
        self.channel = channel
        self.source = source
        self.published = 0
        self._handlers: list[Handler] = []
        self._subscribed = False
        self._started = False
        self._consuming = False

    @classmethod
    def from_url(cls, url: str | None, *, source: str, mode: str = "persistent") -> EventBus:
        """A persistent (or pub/sub) Redis bus when *url* is set, memory otherwise."""
        if url:
            return cls(mode, url=url, source=source)
        return cls("memory", source=source)

    async def _on_error(self, exc: Exception, channel: str, envelope: Any) -> None:
        logger.error("event consumer failed on %s: %s", channel, exc)

    def subscribe(self, handler: Handler) -> Handler:
        """Receive every platform event. Usable as a decorator."""
        self._handlers.append(handler)
        if not self._subscribed:
            self.emitter.on(self.channel, self._dispatch)
            self._subscribed = True
        if self._started and not self._consuming:
            # Subscribed after start(): begin consuming now.
            self._consuming = True
            asyncio.get_running_loop().create_task(self.emitter.start())
        return handler

    async def _dispatch(self, data: dict[str, Any]) -> None:
        event = PlatformEvent.from_dict(data)
        for handler in list(self._handlers):
            try:
                await handler(event)
            except Exception:
                logger.exception("event handler %r failed for %s", handler, event.name)

    async def publish(self, event: PlatformEvent) -> str:
        """Publish *event*. Returns its id."""
        if event.source == "unknown":
            event.source = self.source
        await self.emitter.emit_async(self.channel, event.to_dict())
        self.published += 1
        return event.id

    async def emit(
        self,
        name: str,
        *,
        project: str,
        env: str,
        payload: Any = None,
        actor: str | None = None,
        request_id: str | None = None,
        release_id: str | None = None,
        api_version: str | None = None,
    ) -> str:
        """Build and publish an event."""
        return await self.publish(
            PlatformEvent(
                name=name,
                project=project,
                env=env,
                payload=payload,
                actor=actor,
                source=self.source,
                request_id=request_id,
                release_id=release_id,
                api_version=api_version,
            )
        )

    async def start(self) -> None:
        """Start delivering to this bus's handlers.

        Publishing needs no start: the transport connects on first use. For
        the ``persistent`` backend, starting means draining the shared backlog,
        and a bus without handlers would drain events only to drop them, so
        it starts when its first handler arrives instead.
        """
        self._started = True
        if self.backend == "persistent" and not self._handlers:
            return
        self._consuming = True
        await self.emitter.start()

    async def stop(self) -> None:
        self._started = False
        self._consuming = False
        await self.emitter.stop()
