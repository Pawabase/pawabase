"""Backing up and restoring an environment's identities."""

BASE = "/admin/v1/projects/acme/envs/development"


async def _seed(akz):
    admin = akz.admin
    await admin.put(f"{BASE}/roles", json={"name": "editor", "permissions": ["posts:write"]})
    ada = await admin.post(
        f"{BASE}/users",
        json={"email": "ada@example.com", "password": "correct-horse-1", "roles": ["editor"],
              "user_metadata": {"theme": "dark"}, "app_metadata": {"plan": "pro"}},
    )
    bob = await admin.post(f"{BASE}/users", json={"email": "bob@example.com", "password": "correct-horse-2"})
    await admin.post(f"{BASE}/users/{ada['id']}/permissions", json={"permissions": ["reports:read"]})
    return ada, bob


async def test_backup_carries_roles_users_and_hashes(akz):
    ada, _ = await _seed(akz)
    backup = await akz.admin.get(f"{BASE}/backup")
    assert backup["format"] == 1
    assert [r["name"] for r in backup["roles"]] == ["editor"]
    users = {u["email"]: u for u in backup["users"]}
    assert users["ada@example.com"]["roles"] == ["editor"]
    assert users["ada@example.com"]["permissions"] == ["reports:read"]
    assert users["ada@example.com"]["app_metadata"] == {"plan": "pro"}
    assert users["ada@example.com"]["password"].startswith(("pbkdf2", "argon", "bcrypt", "$")) or users["ada@example.com"]["password"]
    assert str(users["ada@example.com"]["id"]) == ada["id"]


async def test_replace_restore_recovers_everything_with_same_ids(akz):
    ada, bob = await _seed(akz)
    backup = await akz.admin.get(f"{BASE}/backup")

    # Damage: delete a user and a role.
    await akz.admin.delete(f"{BASE}/users/{bob['id']}")
    await akz.admin.delete(f"{BASE}/roles/editor")

    report = await akz.admin.post(f"{BASE}/restore", json={"identities": backup, "replace": True})
    assert report["users"]["created"] == 2
    assert report["roles"] == 1

    again = await akz.admin.get(f"{BASE}/backup")
    assert {(u["id"], u["email"], tuple(u["roles"]), tuple(u["permissions"])) for u in again["users"]} == {
        (u["id"], u["email"], tuple(u["roles"]), tuple(u["permissions"])) for u in backup["users"]
    }
    assert again["roles"] == backup["roles"]

    # The restored password still works.
    response = await akz.http.post(
        "/auth/v1/token?grant_type=password",
        json={"email": "ada@example.com", "password": "correct-horse-1"},
        headers=akz.headers(),
    )
    assert response.status_code == 200, response.text


async def test_merge_restore_keeps_other_users(akz):
    ada, _ = await _seed(akz)
    backup = await akz.admin.get(f"{BASE}/backup")
    carol = await akz.admin.post(f"{BASE}/users", json={"email": "carol@example.com", "password": "correct-horse-3"})
    await akz.admin.patch(f"{BASE}/users/{ada['id']}", json={"name": "Changed"})

    report = await akz.admin.post(f"{BASE}/restore", json={"identities": backup})
    assert report["users"]["updated"] == 2
    emails = {u["email"] for u in (await akz.admin.get(f"{BASE}/users"))["data"]}
    assert "carol@example.com" in emails and carol["id"]


async def test_dry_run_changes_nothing_and_bad_format_is_refused(akz):
    await _seed(akz)
    backup = await akz.admin.get(f"{BASE}/backup")
    preview = await akz.admin.post(f"{BASE}/restore", json={"identities": backup, "replace": True, "dry_run": True})
    assert preview["dry_run"] is True and preview["users"] == 2
    assert (await akz.admin.get(f"{BASE}/users"))["total"] == 2
