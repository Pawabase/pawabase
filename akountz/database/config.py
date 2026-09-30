"""How Akountz connects to its database.

Sillo's own models are registered alongside Akountz's: ``JWTToken`` and
``TokenBlacklist`` hold token families, and Sillo's permission models hold
roles and permissions.
"""

from __future__ import annotations

from sillo.record import DatabaseConfig, DatabaseManager

MODEL_MODULES = ["database.models", "sillo.auth.jwt_auth.models", "sillo.permissions.models"]
MIGRATIONS_MODULE = "database.migrations"


def database_config(settings) -> DatabaseConfig:
    return DatabaseConfig(url=settings.database_url, generate_schemas=settings.db_generate_schemas)


def database(settings) -> DatabaseManager:
    manager = DatabaseManager(database_config(settings))
    manager.register_models(*MODEL_MODULES).set_migrations(MIGRATIONS_MODULE)
    return manager
