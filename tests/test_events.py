"""The platform event bus over Sillo's persistent (Redis backlog) transport."""

import asyncio

import fakeredis
from sillo.events import EventEmitter
from sillo.events.transports.persistent import PersistentTransport

from pawabase_kit.events import EventBus


def _bus(server, source: str) -> EventBus:
    transport = PersistentTransport(url="redis://fake")
    transport._client = fakeredis.FakeAsyncRedis(server=server)
    return EventBus("persistent", source=source, emitter=EventEmitter(transport=transport))


async def test_publish_only_buses_do_not_consume():
    """The backlog is a work queue: every started transport drains it. A
    service that only publishes (the API with a separate worker, Akountz)
    must not take events off it, or they are lost to a process with no
    handlers."""
    server = fakeredis.FakeServer()
    received: list[str] = []
    publishers = [_bus(server, name) for name in ("api", "akountz", "scheduler")]
    worker = _bus(server, "worker")

    async def handle(event):
        received.append(event.name)

    worker.subscribe(handle)
    for bus in (*publishers, worker):
        await bus.start()
    try:
        for i in range(12):
            await publishers[i % 3].emit(f"e.{i}", project="p", env="e")
        for _ in range(100):
            if len(received) == 12:
                break
            await asyncio.sleep(0.05)
    finally:
        for bus in (*publishers, worker):
            await bus.stop()
    assert sorted(received) == sorted(f"e.{i}" for i in range(12))


async def test_handlers_added_after_start_still_consume():
    server = fakeredis.FakeServer()
    bus = _bus(server, "api")
    await bus.start()
    received: list[str] = []

    async def handle(event):
        received.append(event.name)

    bus.subscribe(handle)
    try:
        await bus.emit("late.subscriber", project="p", env="e")
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    assert received == ["late.subscriber"]
