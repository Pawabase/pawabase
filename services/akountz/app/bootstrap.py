"""Assembling Akountz. See the API's bootstrap for why the order matters."""

from __future__ import annotations

import logging

from sillo import SilloApp
from sillo.record import Record

from app.config import AkountzSettings
from app.platform import Akountz
from database.config import MODEL_MODULES, database_config
from pawabase_kit.auth import ProjectUserBackend
from pawabase_kit.service import create_service
from pawabase_kit.settings import PLATFORM_ENV, PLATFORM_PROJECT

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
        description="Pawabase identity: accounts, sessions, social sign-in, MFA, organizations, roles and permissions.",
        backends=[ProjectUserBackend(settings.jwt_master_secret)],
        installables=[Record(database_config(settings), tuple(MODEL_MODULES))],
    )
    app.state["akountz"] = akountz

    @app.on_startup
    async def start() -> None:
        await akountz.start()
        await ensure_operator(akountz)

    @app.on_shutdown
    async def stop() -> None:
        await akountz.stop()

    from routes import register_routes

    register_routes(app, akountz)
    return app


async def ensure_operator(akountz: Akountz) -> None:
    """Create the first platform operator from settings, once."""
    from app import rbac
    from app.accounts import create_account
    from app.environment import platform_config
    from database.models import AuthUser

    settings = akountz.settings
    if not settings.admin_email or not settings.admin_password:
        return
    if await AuthUser.filter(project=PLATFORM_PROJECT, env=PLATFORM_ENV).exists():
        return
    config = platform_config(akountz)
    user = await create_account(
        config,
        email=settings.admin_email,
        password=settings.admin_password,
        name="Administrator",
        verified=True,
    )
    await rbac.define_role(
        PLATFORM_PROJECT,
        PLATFORM_ENV,
        "admin",
        description="Full control of the platform",
        permissions=["*"],
    )
    await rbac.assign_role(user, "admin")
    logger.info("created platform operator %s", user.email)
