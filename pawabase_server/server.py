"""Running the server: two listeners, one process.

The **public** listener serves the application behind the edge: clients, SDKs and the CLI. The **internal** listener serves the application
itself, for the services that manage it (Studio), which authenticate with service tokens. It is the same process and the same objects, so
what Studio manages is exactly what the public side serves.

Signals stop both. If either listener ends on its own the other is stopped too, so a container's restart policy sees one process end.

The queue worker and the scheduler are not part of this process. Unless ``worker_mode`` is ``external``, ``serve`` starts them as child
processes and keeps them running; either way they run in processes of their own, so a slow job never shares the server's event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn

from pawabase_server.app import Server, create_server
from pawabase_server.automation.supervisor import Supervisor
from pawabase_server.db.database import prepare
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.server")


class _Listener(uvicorn.Server):
    """A listener that leaves signal handling to the primary one."""

    def capture_signals(self):  # type: ignore[override]
        return contextlib.nullcontext()


def listeners(server: Server) -> list[uvicorn.Server]:
    """The uvicorn servers for *server*, the public one first."""
    s = server.settings
    common = dict(
        log_level=s.log_level,
        proxy_headers=True,
        forwarded_allow_ips=s.forwarded_allow_ips,
        access_log=s.log_level == "debug",
    )
    public = uvicorn.Server(uvicorn.Config(server.public, host=s.host, port=s.port, **common))
    # The application's lifespan is run by the public listener; this one only serves.
    internal = _Listener(
        uvicorn.Config(
            server.app, host=s.internal_host, port=s.internal_port, lifespan="off", **common
        )
    )
    return [public, internal]


def planned_children(settings: Settings) -> list[tuple[str, str]]:
    """The ``(name, command)`` of each child process ``serve`` starts: none when something else runs them, or when they were put
    in this process on purpose."""
    if settings.worker_mode != "process":
        return []
    children = []
    if not settings.inline_worker:
        children.append(("worker", "worker"))
    if not settings.inline_scheduler:
        children.append(("scheduler", "scheduler"))
    return children


async def serve(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    if outcome := await prepare(settings):
        logger.info("database %s", outcome)
    server = create_server(settings)
    running = listeners(server)
    s = server.settings
    logger.info(
        "public on %s:%s, internal on %s:%s", s.host, s.port, s.internal_host, s.internal_port
    )

    async def run(listener: uvicorn.Server) -> None:
        try:
            await listener.serve()
        finally:
            for other in running:
                other.should_exit = True

    supervisor = Supervisor()
    for name, command in planned_children(settings):
        supervisor.add(name, command)
    supervisor.start()
    # Children are stopped by the application's shutdown, not after the listeners return: on a signal uvicorn finishes its shutdown and
    # then re-raises the signal, which ends this process before anything placed after it would run, and would leave the children behind.
    server.app.shutdown_handlers.insert(0, supervisor.stop)
    try:
        await asyncio.gather(*(run(listener) for listener in running))
    finally:
        await supervisor.stop()
