"""Running the server: two listeners, one process.

The **public** listener serves the application behind the edge: clients, SDKs and the CLI. The **internal** listener serves the application
itself, for the services that manage it (Studio), which authenticate with service tokens. It is the same process and the same objects, so
what Studio manages is exactly what the public side serves.

Signals stop both. If either listener ends on its own the other is stopped too, so a container's restart policy sees one process end.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn

from pawabase_server.app import Server, create_server
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

    await asyncio.gather(*(run(listener) for listener in running))
