"""Migration 0003: Akountz moves to ULID keys, and user ids map to fixed ULIDs the API can compute too."""

import sqlite3

from pawabase_core.ids import is_ulid, legacy_ulid
from sillo.record.commands import migrate

from app.config import AkountzSettings
from database.config import database

LEGACY = "0002_session_org"


def manager_for(tmp_path, name):
    path = tmp_path / name
    return database(AkountzSettings(_env_file=None, app_env="testing", database_url=f"sqlite://{path}")), path


def insert(db, table, **values):
    row = dict(values)
    for _, name, kind, notnull, default, pk in db.execute(f'PRAGMA table_info("{table}")').fetchall():
        if name in row or (pk and name == "id"):
            continue
        if notnull and default is None:
            kind = (kind or "").upper()
            row[name] = (
                0 if "INT" in kind else 0.0 if "REAL" in kind or "FLOA" in kind else
                "2026-01-01T00:00:00+00:00" if name.endswith("_at") else "{}" if "JSON" in kind else "x"
            )  # fmt: skip
    columns = ", ".join(f'"{c}"' for c in row)
    db.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({", ".join("?" for _ in row)})', list(row.values()))


def rows(db, sql):
    cursor = db.execute(sql)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, record, strict=True)) for record in cursor.fetchall()]


async def test_users_get_fixed_ulids_and_everything_that_points_at_them_follows(tmp_path):
    manager, path = manager_for(tmp_path, "legacy.db")
    async with manager:
        await migrate(manager, target=LEGACY)
    db = sqlite3.connect(path)
    insert(db, "akz_users", id=7, project="acme", env="development", email="ada@example.com", username="ada", password="x", is_active=1)
    insert(db, "akz_users", id=12, project="acme", env="development", email="bob@example.com", username="bob", password="x")
    insert(db, "akz_organizations", id=3, project="acme", env="development", slug="acme", name="Acme", created_by=7)
    insert(db, "akz_memberships", id=1, organization_id=3, user_id=7, role="owner")
    insert(db, "akz_memberships", id=2, organization_id=3, user_id=12, role="member")
    insert(db, "akz_invitations", id=1, organization_id=3, email="c@example.com", invited_by=12)
    insert(db, "jwt_tokens", id=1, user_id=7, token_jti="j1", token_family="f1")
    insert(db, "akz_login_events", id=1, project="acme", env="development", kind="sign_in", user_id=12)
    insert(db, "permissions", id=4, name="acme/development/edit")
    insert(db, "user_permissions", id=1, user_id="7", permission_id=4)  # Sillo stores the identity as text
    insert(db, "perm_groups", id=2, name="acme/development/admin")
    insert(db, "perm_user_groups", id=1, user_id="12", group_id=2)
    db.commit()
    db.close()

    manager, _ = manager_for(tmp_path, "legacy.db")
    async with manager:
        await migrate(manager)

    db = sqlite3.connect(path)
    users = {u["email"]: u["id"] for u in rows(db, "SELECT id, email FROM akz_users")}
    assert users == {"ada@example.com": legacy_ulid(7), "bob@example.com": legacy_ulid(12)}
    assert all(is_ulid(i) for i in users.values())

    org = rows(db, "SELECT * FROM akz_organizations")[0]
    assert is_ulid(org["id"]) and org["created_by"] == legacy_ulid(7)
    members = {m["user_id"]: m for m in rows(db, "SELECT * FROM akz_memberships")}
    assert set(members) == {legacy_ulid(7), legacy_ulid(12)} and members[legacy_ulid(7)]["organization_id"] == org["id"]
    assert rows(db, "SELECT invited_by FROM akz_invitations")[0]["invited_by"] == legacy_ulid(12)
    assert rows(db, "SELECT user_id FROM jwt_tokens")[0]["user_id"] == legacy_ulid(7)
    assert rows(db, "SELECT user_id FROM akz_login_events")[0]["user_id"] == legacy_ulid(12)
    grant = rows(db, "SELECT * FROM user_permissions")[0]
    assert grant["user_id"] == legacy_ulid(7) and is_ulid(grant["permission_id"])
    assert rows(db, "SELECT user_id FROM perm_user_groups")[0]["user_id"] == legacy_ulid(12)
    db.close()

    # The tables are the models now: a user can be loaded by the migrated id and gets tokens.
    manager, _ = manager_for(tmp_path, "legacy.db")
    async with manager:
        from database.models import AuthUser

        ada = await AuthUser.load_user(legacy_ulid(7))
        assert ada is not None and ada.email == "ada@example.com"
        pair = await ada.issue_token_pair("s" * 32)
        assert pair["access_token"] and await ada.active_token_count() == 2  # the new pair; the migrated token had expired
        assert await ada.revoke_all_tokens() == 3
        fresh = await AuthUser.create(project="acme", env="development", email="new@example.com", username="new", password="x")
        assert is_ulid(fresh.id) and fresh.id > legacy_ulid(12)


async def test_a_fresh_akountz_database_goes_straight_through(tmp_path):
    manager, _ = manager_for(tmp_path, "fresh.db")
    async with manager:
        await migrate(manager)
    manager, _ = manager_for(tmp_path, "fresh.db")
    async with manager:
        from database.models import AuthUser, Permission

        user = await AuthUser.create(project="p", env="e", email="a@b.co", username="a", password="x")
        await Permission.define("p/e/read")
        await Permission.assign(user, "p/e/read")
        assert is_ulid(user.id) and "p/e/read" in await Permission.of(user)
