"""Assembling the API application. Start reading here.

Order matters, because Sillo builds middleware inside-out (the last registered
runs first):

1. ``create_service`` builds the ``SilloApp`` with its auth backends, then the
   platform-context middleware and telemetry (request ids) around it.
2. Record (the platform database) and the data-plane dispatcher are installed
   before that outer plumbing. The dispatcher therefore runs inside the context
   and telemetry middleware, and every ``/rest/v1`` request is traced and
   attributed to its environment.
3. Routers are mounted most-specific first.
"""

from __future__ import annotations

import asyncio
import logging

from sillo import SilloApp
from sillo.record import Record

from app.config import ApiSettings
from app.dispatch import DataPlaneDispatcher
from app.platform import Platform
from app.request_metrics import RequestRollup
from database.config import MODEL_MODULES, database_config
from pawabase_core.auth import UserBackend
from pawabase_core.service import create_service

logger = logging.getLogger("pawabase.api")

DEFAULT_ENVIRONMENT = "development"


async def ensure_default_environment() -> None:
    """Give a fresh runtime its first environment, so Studio has something to manage."""
    from tortoise.exceptions import IntegrityError

    from database.models import Environment

    if await Environment.exists():
        return
    try:
        await Environment.create(name=DEFAULT_ENVIRONMENT, is_default=True)
        logger.info("created the default environment %r", DEFAULT_ENVIRONMENT)
    except IntegrityError:  # another API instance created it first
        pass


def create_app(
    settings: ApiSettings | None = None, *, platform: Platform | None = None
) -> SilloApp:
    settings = settings or ApiSettings()
    platform = platform or Platform(settings)

    app = create_service(
        "api",
        settings,
        title="Pawabase API",
        description="The Pawabase runtime API: resources, flows, events, jobs, storage and the management plane.",
        backends=[
            UserBackend(settings.jwt_master_secret),
        ],
        inner=[DataPlaneDispatcher(platform)],
        installables=[Record(database_config(settings), tuple(MODEL_MODULES))],
    )
    app.state["platform"] = platform
    platform.app = app
    rollup = RequestRollup(retention_days=settings.request_retention_days).attach(
        app.state["pawabase.telemetry"]
    )
    app.state["request_rollup"] = rollup

    @app.on_startup
    async def start_platform() -> None:
        await platform.start()
        await ensure_default_environment()
        rollup.start()
        from app.events import EventProcessor

        if settings.inline_worker:
            EventProcessor(platform).attach()
            from app.worker import start_inline_worker

            app.state["inline_worker"] = start_inline_worker(platform)
        if settings.inline_scheduler:
            from app.scheduler import PlatformScheduler

            scheduler = PlatformScheduler(platform)
            app.state["inline_scheduler"] = scheduler
            await scheduler.start()

    async def stop_platform() -> None:
        await rollup.stop()
        worker = app.state.get("inline_worker")
        if worker is not None:
            worker.stop()
            task = app.state.get("inline_worker_task")
            if task is not None:
                await asyncio.wait([task], timeout=5)
        scheduler = app.state.get("inline_scheduler")
        if scheduler is not None:
            await scheduler.stop()
        await platform.stop()

    # Shutdown handlers run in the order they were added, and the database installable added
    # its own when the service was created. Stopping the platform *after* that closes the
    # database: the last request-metrics flush and any job still finishing then write to a
    # closed database, which silently opens a new connection nothing closes. Stop the things
    # that use the database first, then let it close.
    app.shutdown_handlers.insert(0, stop_platform)

    from routes import register_routes

    register_routes(app, platform)
    return app
