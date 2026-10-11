"""The worker and the scheduler as processes of their own: ``pawabase-server worker`` and ``pawabase-server scheduler``.

Both build the same application the server does, headlessly (no listeners, no realtime), so they share its database, queue, events and
code. A signal asks them to finish: the worker completes the jobs it has started, the scheduler stops firing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket

from pawabase_server.app import create_app
from pawabase_server.automation.event_processor import EventProcessor
from pawabase_server.automation.scheduler import PlatformScheduler
from pawabase_server.automation.worker import build_worker, heartbeat, worker_queues
from pawabase_server.platform import Platform
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.runner")


def _stop_on_signals(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)


async def _leave_with_parent(stop: asyncio.Event, interval: float = 2.0) -> None:
    """End when the process that started this one is gone.

    A supervised child must not outlive its server: if the server is killed outright nothing is left to signal it, and its jobs would then
    run unsupervised and unobserved. A process started by something else (a container's init) keeps the same parent and is unaffected.
    """
    parent = os.getppid()
    while not stop.is_set():
        await asyncio.sleep(interval)
        if os.getppid() != parent:
            logger.warning("the parent process is gone; stopping")
            stop.set()


async def work(platform: Platform, settings: Settings, stop: asyncio.Event) -> None:
    """Consume the platform's queues and events until *stop* is set. Returns once the jobs in progress have finished."""
    EventProcessor(platform).attach()
    queues = worker_queues()
    worker = build_worker(platform, queues=queues, concurrency=settings.worker_concurrency)
    # The worker's own signal handlers would stop it without letting the heartbeat record that; `stop` is the one switch.
    worker._register_signals = lambda: None  # type: ignore[method-assign]
    name = f"worker:{socket.gethostname()}:{os.getpid()}"
    beat = asyncio.create_task(heartbeat(name, queues, worker, stop), name="worker-heartbeat")
    running = asyncio.create_task(worker.run(), name="worker")
    logger.info("worker %s running on %s", name, ", ".join(queues))
    await stop.wait()
    worker.stop()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(running, timeout=settings.drain_grace + 30)
    await beat


async def schedule(platform: Platform, stop: asyncio.Event) -> None:
    """Fire schedules and platform housekeeping until *stop* is set."""
    scheduler = PlatformScheduler(platform)
    await scheduler.start()
    logger.info("scheduler running")
    try:
        await stop.wait()
    finally:
        await scheduler.stop()


async def run(role: str, settings: Settings | None = None) -> None:
    """Run the ``worker`` or ``scheduler`` role until a signal arrives."""
    settings = settings or Settings()
    app = create_app(settings, role=role)
    await app._startup()
    stop = asyncio.Event()
    _stop_on_signals(stop)
    watcher = asyncio.create_task(_leave_with_parent(stop), name="parent-watch")
    platform = app.state["platform"]
    try:
        if role == "worker":
            await work(platform, settings, stop)
        else:
            await schedule(platform, stop)
    finally:
        watcher.cancel()
        await app._shutdown()
