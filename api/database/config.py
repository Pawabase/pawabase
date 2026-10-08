"""How the API connects to its platform database. App, migrations and scripts share it."""

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
