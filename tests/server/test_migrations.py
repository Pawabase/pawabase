"""The database commands: a fresh database is created from the models, an existing one is migrated."""

import sqlite3

from pawabase_server.db.database import apply, database
from pawabase_server.settings import Settings


def tables(path) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()


async def test_a_fresh_database_is_created_whole_and_recorded_as_migrated(tmp_path):
    path = tmp_path / "fresh.db"
    manager = database(Settings(_env_file=None, app_env="testing", database_url=f"sqlite://{path}"))
    async with manager:
        assert await apply(manager) == "created"
    names = tables(path)
    # platform and identity live together
    assert {"pb_environments", "pb_api_keys", "akz_users", "akz_sessions", "permissions"} <= names

    manager = database(Settings(_env_file=None, app_env="testing", database_url=f"sqlite://{path}"))
    async with manager:
        assert (
            await apply(manager) == "migrated"
        )  # nothing left to do, and it does not try to recreate anything


async def test_preparing_an_empty_database_creates_its_schema(tmp_path):
    from pawabase_server.db.database import prepare

    path = tmp_path / "empty.db"
    settings = Settings(_env_file=None, app_env="testing", database_url=f"sqlite://{path}")
    assert await prepare(settings) == "created"
    assert "pb_environments" in tables(path) and "akz_users" in tables(path)
    assert await prepare(settings) == "migrated"  # and again, harmlessly


async def test_a_server_told_not_to_migrate_refuses_an_empty_database(tmp_path):
    import pytest

    from pawabase_server.db.database import prepare

    settings = Settings(
        _env_file=None,
        app_env="testing",
        database_url=f"sqlite://{tmp_path / 'empty.db'}",
        migrate=False,
    )
    with pytest.raises(RuntimeError, match="no Pawabase schema"):
        await prepare(settings)
