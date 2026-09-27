"""Assembling the API application. Start reading here.

Order matters, because Sillo builds middleware inside-out (the last registered
runs first):

1. ``create_service`` builds the ``SilloApp`` with its auth backends, then the
   platform-context middleware and telemetry (request ids) around it.
2. Record (the platform database) and the data-plane dispatcher are installed
   before that outer plumbing. The dispatcher therefore runs inside the context
   and telemetry middleware, and every ``/rest/v1`` request is traced and
   attributed to its project.
3. Routers are mounted most-specific first.
"""

from __future__ import annotations

import asyncio
import logging

from pawabase_kit.auth import OperatorBackend, ProjectUserBackend
from pawabase_kit.service import create_service
from sillo import SilloApp
from sillo.record import Record

from app.config import ApiSettings
from app.dispatch import DataPlaneDispatcher
from app.platform import Platform
from database.config import MODEL_MODULES, database_config

logger = logging.getLogger("pawabase.api")


def create_app(settings: ApiSettings | None = None, *, platform: Platform | None = None) -> SilloApp:
    settings = settings or ApiSettings()
    platform = platform or Platform(settings)

    app = create_service(
        "api",
        settings,
        title="Pawabase API",
        description="The Pawabase platform API: projects, resources, flows, events, jobs, storage and the management plane.",
        backends=[OperatorBackend(settings.jwt_master_secret), ProjectUserBackend(settings.jwt_master_secret)],
        inner=[DataPlaneDispatcher(platform)],
        installables=[Record(database_config(settings), tuple(MODEL_MODULES))],
    )
    app.state["platform"] = platform

    @app.on_startup
    async def start_platform() -> None:
        await platform.start()
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

    @app.on_shutdown
    async def stop_platform() -> None:
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

    from routes import register_routes

    register_routes(app, platform)
    return app
