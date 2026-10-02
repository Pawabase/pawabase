"""Assembling Angula, the realtime service."""

from __future__ import annotations

import asyncio
import contextlib

from sillo import SilloApp

from app.config import AngulaSettings
from app.realtime import Realtime
from pawabase_core.auth import ProjectUserBackend
from pawabase_core.service import create_service

PRUNE_SECONDS = 30


def create_app(
    settings: AngulaSettings | None = None, *, realtime: Realtime | None = None
) -> SilloApp:
    settings = settings or AngulaSettings()
    realtime = realtime or Realtime(settings)
    app = create_service(
        "angula",
        settings,
        title="Angula",
        description="Pawabase realtime: channels, publish/subscribe and presence over WebSockets.",
        backends=[ProjectUserBackend(settings.jwt_master_secret)],
    )
    app.state["realtime"] = realtime
    tasks: list[asyncio.Task] = []

    async def prune() -> None:
        # sillo-wire leaves pruning cadence to the application.
        while True:
            await asyncio.sleep(PRUNE_SECONDS)
            for peer in await realtime.hub.prune():
                connection = realtime.connections.get(str(peer.id))
                if connection is not None:
                    await realtime.disconnect(connection)

    @app.on_startup
    async def start() -> None:
        await realtime.start()
        tasks.append(asyncio.create_task(prune(), name="angula-prune"))

    @app.on_shutdown
    async def stop() -> None:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        await realtime.stop()

    from routes import register_routes

    register_routes(app, realtime)
    return app
