"""Migration 0003: Akountz moves to ULID keys, and user ids map to fixed ULIDs the API can compute too."""

import sqlite3

import pytest
from sillo.record.commands import migrate

from app.config import AkountzSettings
from database.config import database
from pawabase_core.ids import is_ulid, legacy_ulid

LEGACY = "0002_session_org"
ULID = "0003_ulid_keys"  # the step under test; 0004 (single runtime) has its own tests below


def manager_for(tmp_path, name):
    path = tmp_path / name
    return database(
        AkountzSettings(_env_file=None, app_env="testing", database_url=f"sqlite://{path}")
    ), path


def insert(db, table, **values):
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
                "2026-01-01T00:00:00+00:00" if name.endswith("_at") else "{}" if "JSON" in kind else "x"
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


async def test_users_get_fixed_ulids_and_everything_that_points_at_them_follows(tmp_path):
    manager, path = manager_for(tmp_path, "legacy.db")
    async with manager:
        await migrate(manager, target=LEGACY)
    db = sqlite3.connect(path)
    insert(
        db,
        "akz_users",
        id=7,
        project="acme",
        env="development",
        email="ada@example.com",
        username="ada",
        password="x",
        is_active=1,
    )
    insert(
        db,
        "akz_users",
        id=12,
        project="acme",
        env="development",
        email="bob@example.com",
        username="bob",
        password="x",
    )
    insert(
        db,
        "akz_organizations",
        id=3,
        project="acme",
        env="development",
        slug="acme",
        name="Acme",
        created_by=7,
    )
    insert(db, "akz_memberships", id=1, organization_id=3, user_id=7, role="owner")
    insert(db, "akz_memberships", id=2, organization_id=3, user_id=12, role="member")
    insert(db, "akz_invitations", id=1, organization_id=3, email="c@example.com", invited_by=12)
    insert(db, "jwt_tokens", id=1, user_id=7, token_jti="j1", token_family="f1")
    insert(
        db, "akz_login_events", id=1, project="acme", env="development", kind="sign_in", user_id=12
    )
    insert(db, "permissions", id=4, name="acme/development/edit")
    insert(
        db, "user_permissions", id=1, user_id="7", permission_id=4
    )  # Sillo stores the identity as text
    insert(db, "perm_groups", id=2, name="acme/development/admin")
    insert(db, "perm_user_groups", id=1, user_id="12", group_id=2)
    db.commit()
    db.close()

    manager, _ = manager_for(tmp_path, "legacy.db")
    async with manager:
        await migrate(manager, target=ULID)

    db = sqlite3.connect(path)
    users = {u["email"]: u["id"] for u in rows(db, "SELECT id, email FROM akz_users")}
    assert users == {"ada@example.com": legacy_ulid(7), "bob@example.com": legacy_ulid(12)}
    assert all(is_ulid(i) for i in users.values())

    org = rows(db, "SELECT * FROM akz_organizations")[0]
    assert is_ulid(org["id"]) and org["created_by"] == legacy_ulid(7)
    members = {m["user_id"]: m for m in rows(db, "SELECT * FROM akz_memberships")}
    assert (
        set(members) == {legacy_ulid(7), legacy_ulid(12)}
        and members[legacy_ulid(7)]["organization_id"] == org["id"]
    )
    assert rows(db, "SELECT invited_by FROM akz_invitations")[0]["invited_by"] == legacy_ulid(12)
    assert rows(db, "SELECT user_id FROM jwt_tokens")[0]["user_id"] == legacy_ulid(7)
    assert rows(db, "SELECT user_id FROM akz_login_events")[0]["user_id"] == legacy_ulid(12)
    grant = rows(db, "SELECT * FROM user_permissions")[0]
    assert grant["user_id"] == legacy_ulid(7) and is_ulid(grant["permission_id"])
    assert rows(db, "SELECT user_id FROM perm_user_groups")[0]["user_id"] == legacy_ulid(12)
    db.close()


async def test_the_single_runtime_migration_refuses_users_of_the_old_layout(tmp_path):
    manager, path = manager_for(tmp_path, "old.db")
    async with manager:
        await migrate(manager, target=ULID)
    db = sqlite3.connect(path)
    insert(
        db,
        "akz_users",
        id=legacy_ulid(7),
        project="acme",
        env="development",
        email="ada@example.com",
        username="ada",
        password="x",
        is_active=1,
    )
    db.commit()
    db.close()
    manager, _ = manager_for(tmp_path, "old.db")
    async with manager:
        with pytest.raises(RuntimeError, match="one project"):
            await migrate(manager)
    db = sqlite3.connect(path)  # refused atomically: the user is still there
    assert [r["email"] for r in rows(db, "SELECT email FROM akz_users")] == ["ada@example.com"]
    db.close()


async def test_postgres_drops_the_studio_operators_and_keeps_an_empty_runtime_usable():
    """Opt-in: PAWABASE_TEST_POSTGRES_URL=postgres://user:pass@host:5432/postgres (a scratch database is made and dropped).

    SQLite cannot drop the unique indexes migration 0004 removes, and Akountz runs on PostgreSQL, so the chain is checked there.
    """
    import os
    import uuid

    import asyncpg

    server = os.environ.get("PAWABASE_TEST_POSTGRES_URL")
    if not server:
        pytest.skip(
            "set PAWABASE_TEST_POSTGRES_URL to run the migrations against a real PostgreSQL"
        )
    name = f"akz_test_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(server)
    await admin.execute(f'CREATE DATABASE "{name}"')
    url = server.rsplit("/", 1)[0] + f"/{name}"
    try:
        manager = database(AkountzSettings(_env_file=None, app_env="testing", database_url=url))
        async with manager:
            await migrate(manager, target=ULID)
        db = await asyncpg.connect(url)
        operator = legacy_ulid(1)
        await db.execute(
            "insert into akz_users (id, project, env, email, username, password, is_active, is_staff, is_superuser, mfa_enabled, failed_logins, "
            "user_metadata, app_metadata, name, created_at, updated_at) values ($1,'_platform','main','root@x.io','root','x',true,false,false,false,0,'{}','{}','',now(),now())",
            operator,
        )
        await db.close()
        manager = database(AkountzSettings(_env_file=None, app_env="testing", database_url=url))
        async with manager:
            await migrate(manager)  # the operator is dropped, nothing else was there: allowed
        manager = database(AkountzSettings(_env_file=None, app_env="testing", database_url=url))
        async with manager:
            from database.models import AuthUser

            assert await AuthUser.all().count() == 0
            user = await AuthUser.create(
                env="development", email="a@b.co", username="a", password="x"
            )
            assert is_ulid(user.id)
        db = await asyncpg.connect(url)
        columns = {
            r["column_name"]
            for r in await db.fetch(
                "select column_name from information_schema.columns where table_name='akz_users'"
            )
        }
        await db.close()
        assert "env" in columns and "project" not in columns
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()
