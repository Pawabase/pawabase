"""How Akountz connects to its database.

Akountz's models include ULID-keyed versions of Sillo's ``JWTToken``,
``TokenBlacklist`` and permission models (``database.models.framework``), so
Sillo's own modules are not registered: their tables key on integers.
"""

from __future__ import annotations

from sillo.record import DatabaseConfig, DatabaseManager

MODEL_MODULES = ["database.models"]
MIGRATIONS_MODULE = "database.migrations"


def database_config(settings) -> DatabaseConfig:
    return DatabaseConfig(
        url=settings.tuned(settings.database_url), generate_schemas=settings.db_generate_schemas
    )


def database(settings) -> DatabaseManager:
    manager = DatabaseManager(database_config(settings))
    manager.register_models(*MODEL_MODULES).set_migrations(MIGRATIONS_MODULE)
    return manager
