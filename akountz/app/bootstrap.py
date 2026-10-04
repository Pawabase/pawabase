"""Assembling Akountz. See the API's bootstrap for why the order matters."""

from __future__ import annotations

import logging

from sillo import SilloApp
from sillo.record import Record

from app.config import AkountzSettings
from app.platform import Akountz
from database.config import MODEL_MODULES, database_config
from pawabase_core.auth import UserBackend
from pawabase_core.service import create_service

logger = logging.getLogger("pawabase.akountz")


def create_app(
    settings: AkountzSettings | None = None, *, akountz: Akountz | None = None
) -> SilloApp:
    settings = settings or AkountzSettings()
    akountz = akountz or Akountz(settings)
    app = create_service(
        "akountz",
        settings,
        title="Akountz",
        description="Pawabase Auth: your application's users, sessions, social sign-in, MFA, organizations, roles and permissions.",
        backends=[UserBackend(settings.jwt_master_secret)],
        installables=[Record(database_config(settings), tuple(MODEL_MODULES))],
    )
    app.state["akountz"] = akountz

    @app.on_startup
    async def start() -> None:
        await akountz.start()

    @app.on_shutdown
    async def stop() -> None:
        await akountz.stop()

    from routes import register_routes

    register_routes(app, akountz)
    return app
