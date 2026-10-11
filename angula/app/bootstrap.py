"""Assembling Angula, the realtime service."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sillo import SilloApp

from app.config import AngulaSettings
from app.realtime import Realtime
from pawabase_core.auth import UserBackend
from pawabase_core.service import create_service
from routes import register_routes

logger = logging.getLogger("pawabase.angula")


async def housekeeping(realtime: Realtime, interval: float) -> None:
    """Prune idle peers, drop stale presence, close out expired tokens and re-check subscriptions when rules change.

    ``sillo-wire`` leaves pruning cadence to the application. A failing pass is logged and tried again: housekeeping must outlive any one bug.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await realtime.sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("housekeeping pass failed")


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
        backends=[UserBackend(settings.jwt_master_secret)],
    )
    app.state["realtime"] = realtime
    tasks: list[asyncio.Task] = []

    @app.on_startup
    async def start() -> None:
        await realtime.start()
        tasks.append(
            asyncio.create_task(
                housekeeping(realtime, settings.sweep_interval), name="angula-housekeeping"
            )
        )

    @app.on_shutdown
    async def stop() -> None:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        await realtime.stop()

    register_routes(app, realtime)
    return app
