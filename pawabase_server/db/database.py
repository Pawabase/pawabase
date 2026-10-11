"""The platform database: how the server connects, and the migration commands.

One database holds everything the server stores (``pb_*`` tables for environments, definitions and activity; ``akz_*`` and the permission
tables for application users). The app, the migration commands and scripts all build it from here.

``python -m pawabase_server.db.database`` applies pending migrations; ``python -m pawabase_server.db.database make NAME`` writes a new
migration from the current models, for contributors changing them.

A database with nothing in it is not walked through the history: its schema is created from the models (which are what the migrations were
generated from) and the whole chain is recorded as applied. Replaying fourteen migrations, some of which rewrite data that does not exist yet,
would only be slower and, on SQLite, impossible. A database that already holds a platform is migrated step by step, as always.
"""

from __future__ import annotations

import asyncio
import sys

from sillo.record import DatabaseConfig, DatabaseManager

from pawabase_server.settings import Settings

MODEL_MODULES = ["pawabase_server.db.models"]
MIGRATIONS_MODULE = "pawabase_server.db.migrations"


def database_config(settings: Settings) -> DatabaseConfig:
    return DatabaseConfig(
        url=settings.tuned(settings.database_url), generate_schemas=settings.db_generate_schemas
    )


def database(settings: Settings) -> DatabaseManager:
    manager = DatabaseManager(database_config(settings))
    manager.register_models(*MODEL_MODULES).set_migrations(MIGRATIONS_MODULE)
    return manager


async def is_fresh() -> bool:
    """Whether the connected database holds no platform yet (there is no ``pb_environments`` table).

    Only "that table does not exist" means yes. A connection that cannot be made, a permission error or a timeout is not an empty database,
    and treating it as one would try to create a schema on top of something unknown, so those propagate.
    """
    from tortoise import connections

    try:
        await connections.get("default").execute_query("SELECT 1 FROM pb_environments LIMIT 1")
    except Exception as exc:
        message = str(exc).lower()
        if "does not exist" in message or "doesn't exist" in message or "no such table" in message:
            return True
        raise
    return False


async def apply(manager: DatabaseManager) -> str:
    """Bring the connected database up to date. Returns ``"created"`` for a fresh one, else ``"migrated"``."""
    from sillo.record.commands import migrate
    from tortoise import Tortoise

    if await is_fresh():
        await Tortoise.generate_schemas(safe=True)
        await migrate(manager, fake=True)
        return "created"
    await migrate(manager)
    return "migrated"


async def prepare(settings: Settings) -> str | None:
    """Make sure the database has the platform's schema before the server opens it. Returns what was done, or ``None``.

    Called by the server before it starts, so a server started any way at all (the container's entrypoint, a bare ``python -m``, a platform
    that replaces the entrypoint) cannot come up against an empty database and fail on its first query. Migrating closes the connections
    it was given, which is why this runs on a manager of its own, not inside the application.

    Raises:
        RuntimeError: The database is empty and ``PAWABASE_MIGRATE`` is false.
    """
    if (
        settings.db_generate_schemas
    ):  # tests and throwaway setups create tables from the models themselves
        return None
    async with database(settings) as manager:
        if settings.migrate:
            return await apply(manager)
        if await is_fresh():
            raise RuntimeError(
                "the database has no Pawabase schema and PAWABASE_MIGRATE is false: "
                "run `python -m pawabase_server migrate`, or set PAWABASE_MIGRATE=true"
            )
    return None


async def main(argv: list[str]) -> None:
    from sillo.record.commands import make

    manager = database(Settings())
    async with manager:
        if argv[:1] == ["make"]:
            await make(manager, argv[1] if len(argv) > 1 else "auto")
        else:
            await apply(manager)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
