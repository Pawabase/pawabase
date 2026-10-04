"""Migrations 0009 and 0010 (integer keys become ULIDs, rows and links intact) and 0011 (one runtime, no projects)."""

import json
import sqlite3

import pytest
from sillo.record.commands import migrate

from app.config import ApiSettings
from database.config import database
from pawabase_core.ids import is_ulid, legacy_ulid, ulid_timestamp_ms

LEGACY = "0008_function_branches"
ULID = "0010_user_ids_are_ulids"  # the step under test; 0011 (single runtime) has its own tests below


def manager_for(tmp_path, name):
    path = tmp_path / name
    return database(
        ApiSettings(_env_file=None, app_env="testing", database_url=f"sqlite://{path}")
    ), path


def insert(db, table, **values):
    """INSERT with a value for every NOT NULL column without a default (the old schema is the contract)."""
    row = dict(values)
    for _, name, kind, notnull, default, pk in db.execute(
        f'PRAGMA table_info("{table}")'
    ).fetchall():
        if name in row or (pk and name == "id"):
            continue
        if notnull and default is None:
            kind = (kind or "").upper()
            row[name] = (
                0 if "INT" in kind else 0.0 if "REAL" in kind or "FLOA" in kind else
                "2026-01-01T00:00:00+00:00" if name in ("created_at", "updated_at", "started_at") else
                "{}" if "JSON" in kind else "x"
            )  # fmt: skip
    columns = ", ".join(f'"{c}"' for c in row)
    db.execute(
        f'INSERT INTO "{table}" ({columns}) VALUES ({", ".join("?" for _ in row)})',
        list(row.values()),
    )


def rows(db, sql):
    cursor = db.execute(sql)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


async def test_integer_keys_become_ulids_and_every_link_survives(tmp_path):
    manager, path = manager_for(tmp_path, "legacy.db")
    async with manager:
        await migrate(manager, target=LEGACY)
    db = sqlite3.connect(path)
    insert(
        db,
        "pb_organizations",
        id=7,
        slug="acme",
        name="Acme",
        created_at="2026-03-01T10:00:00+00:00",
        created_by="42",
    )
    insert(
        db,
        "pb_organizations",
        id=9,
        slug="beta",
        name="Beta",
        created_at="2026-04-01T10:00:00+00:00",
    )
    insert(
        db,
        "pb_projects",
        id=3,
        ref="shop",
        name="Shop",
        organization_id=7,
        created_by="ops@example.com",
    )
    insert(db, "pb_environments", id=11, project_id=3, name="development", version=4)
    insert(db, "pb_environments", id=12, project_id=3, name="production", version=1)
    insert(db, "pb_org_members", id=1, organization_id=7, user_id="42", role="owner")
    insert(
        db,
        "pb_resources",
        id=5,
        environment_id=11,
        name="notes",
        fields=json.dumps([{"name": "t"}]),
    )
    for number, status in ((100, 200), (101, 500)):
        insert(db, "pb_request_logs", id=number, request_id=f"r{number}", service="api", project="shop",
               env="development", method="GET", path="/x", status=status,
               started_at="2026-03-01T10:00:00+00:00")  # fmt: skip
    db.commit()
    db.close()

    manager, _ = manager_for(tmp_path, "legacy.db")
    async with manager:
        await migrate(manager, target=ULID)

    db = sqlite3.connect(path)
    orgs = rows(db, "SELECT * FROM pb_organizations ORDER BY id")
    assert [o["slug"] for o in orgs] == ["acme", "beta"]
    assert (
        all(is_ulid(o["id"]) for o in orgs) and orgs[0]["id"] < orgs[1]["id"]
    )  # old order is kept
    assert (
        ulid_timestamp_ms(orgs[0]["id"]) == 1_772_359_200_000
    )  # the row's created_at, not the migration's

    project = rows(db, "SELECT * FROM pb_projects")[0]
    assert project["organization_id"] == orgs[0]["id"] and project["ref"] == "shop"
    envs = rows(db, "SELECT * FROM pb_environments ORDER BY name")
    assert [e["name"] for e in envs] == ["development", "production"]
    assert {e["project_id"] for e in envs} == {project["id"]} and envs[0]["version"] == 4

    member = rows(db, "SELECT * FROM pb_org_members")[0]
    # User ids are Akountz's, held here as text: they become the same fixed ULIDs Akountz's migration gives them.
    assert member["organization_id"] == orgs[0]["id"] and member["user_id"] == legacy_ulid(42)
    assert orgs[0]["created_by"] == legacy_ulid(42)
    assert project["created_by"] == "ops@example.com"  # not an id: left alone

    resource = rows(db, "SELECT * FROM pb_resources")[0]
    assert resource["environment_id"] == envs[0]["id"] and json.loads(resource["fields"]) == [
        {"name": "t"}
    ]

    logs = rows(db, "SELECT * FROM pb_request_logs ORDER BY id")
    assert [r["request_id"] for r in logs] == ["r100", "r101"] and all(
        is_ulid(r["id"]) for r in logs
    )
    db.close()

async def test_a_fresh_postgres_goes_straight_through_to_the_single_runtime_schema():
    """Opt-in: PAWABASE_TEST_POSTGRES_URL=postgres://user:pass@host:5432/postgres (a server; a scratch database is made and dropped).

    SQLite cannot drop the unique index migration 0011 removes, and the platform database is PostgreSQL, so the whole chain is checked there.
    """
    import os
    import uuid

    import asyncpg

    server = os.environ.get("PAWABASE_TEST_POSTGRES_URL")
    if not server:
        pytest.skip("set PAWABASE_TEST_POSTGRES_URL to run the migrations against a real PostgreSQL")
    name = f"pw_test_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(server)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = server.rsplit("/", 1)[0] + f"/{name}"
    try:
        manager = database(ApiSettings(_env_file=None, app_env="testing", database_url=url))
        async with manager:
            await migrate(manager)
        db = await asyncpg.connect(url)
        tables = {r["tablename"] for r in await db.fetch("select tablename from pg_tables where schemaname='public'")}
        await db.close()
        assert {"pb_environments", "pb_api_keys", "pb_secrets", "pb_audit"} <= tables
        assert not tables & {"pb_projects", "pb_organizations", "pb_org_members", "pb_org_invitations", "pb_project_keys"}
        manager = database(ApiSettings(_env_file=None, app_env="testing", database_url=url))
        async with manager:
            from database.models import Environment, Secret

            environment = await Environment.create(name="development")
            secret = await Secret.create(environment=environment, name="TOKEN", ciphertext="x")
            assert is_ulid(environment.id) and is_ulid(secret.id) and secret.environment_id == environment.id
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_the_single_runtime_migration_refuses_a_database_that_still_holds_projects(tmp_path):
    manager, path = manager_for(tmp_path, "old.db")
    async with manager:
        await migrate(manager, target=ULID)
    db = sqlite3.connect(path)
    insert(db, "pb_organizations", id="01ARZ3NDEKTSV4RRFFQ69G5FAV", slug="acme", name="Acme")
    insert(db, "pb_projects", id="01ARZ3NDEKTSV4RRFFQ69G5FAW", ref="shop", name="Shop", organization_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
    db.commit()
    db.close()

    manager, _ = manager_for(tmp_path, "old.db")
    async with manager:
        with pytest.raises(RuntimeError, match="one project"):
            await migrate(manager)
    db = sqlite3.connect(path)  # refused atomically: nothing was dropped
    assert rows(db, "SELECT ref FROM pb_projects") == [{"ref": "shop"}]
    db.close()


async def test_an_orphan_row_aborts_the_migration_and_keeps_the_old_tables(tmp_path):
    manager, path = manager_for(tmp_path, "orphan.db")
    async with manager:
        await migrate(manager, target=LEGACY)
    db = sqlite3.connect(path)
    insert(db, "pb_projects", id=3, ref="shop", name="Shop", organization_id=999)
    db.commit()
    db.close()

    manager, _ = manager_for(tmp_path, "orphan.db")
    async with manager:
        with pytest.raises(RuntimeError, match="no matching row"):
            await migrate(manager)
    db = sqlite3.connect(path)
    assert rows(db, "SELECT id, organization_id FROM pb_projects") == [
        {"id": 3, "organization_id": 999}
    ]  # still the integer schema
