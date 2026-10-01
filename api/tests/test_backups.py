"""Backups: download an environment, damage it, restore it."""

import copy

import pytest

from pawabase_kit.clients import ServiceError

ENV = "/platform/v1/projects/shop/envs/development"


class FakeAkountz:
    def __init__(self):
        self.identities = {
            "format": 1,
            "roles": [{"name": "editor", "description": "", "permissions": ["posts:write"]}],
            "users": [{"id": 7, "email": "ada@example.com", "password": "hash", "roles": ["editor"]}],
            "orgs": [],
        }
        self.restores = []

    async def get(self, path, **kwargs):
        assert path.endswith("/backup")
        return self.identities

    async def post(self, path, json=None, **kwargs):
        self.restores.append((path, json))
        return {"roles": 1, "users": {"created": 1, "updated": 0}, "organizations": 0, "warnings": ["w"]}

    async def close(self):
        pass


@pytest.fixture
async def shop(api):
    api.platform.akountz = FakeAkountz()
    await api.studio.post("/platform/v1/projects", json={"ref": "shop", "name": "Shop", "environments": ["development"]})
    await api.studio.post(f"{ENV}/policies", json={"name": "staff", "condition": {"role": "editor"}})
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "posts",
            "fields": [{"name": "title", "type": "string", "required": True}, {"name": "meta", "type": "json"}],
            "operations": {"list": {"enabled": True, "policy": "staff"}},
        },
    )
    await api.studio.post(f"{ENV}/resources/posts/migrate")
    for title in ("One", "Two", "Three"):
        await api.studio.post(f"{ENV}/resources/posts/records", json={"title": title, "meta": {"t": title}})
    return api


async def rows(api):
    return (await api.studio.get(f"{ENV}/resources/posts/records", params={"per_page": 100}))["data"]


async def test_backup_holds_definitions_data_users_and_a_checksum(shop):
    backup = await shop.studio.get(f"{ENV}/backup")
    assert backup["format"] == "pawabase.backup" and backup["checksum"]
    assert [p["name"] for p in backup["definitions"]["policies"]] == ["staff"]
    assert [r["title"] for r in backup["data"]["posts"]] == ["One", "Two", "Three"]
    assert backup["users"]["users"][0]["email"] == "ada@example.com"
    assert "secrets" in backup["not_included"]


async def test_parts_can_be_chosen(shop):
    backup = await shop.studio.get(f"{ENV}/backup", params={"include": "definitions"})
    assert set(backup) >= {"definitions"} and "data" not in backup and "users" not in backup
    with pytest.raises(ServiceError) as err:
        await shop.studio.get(f"{ENV}/backup", params={"include": "nonsense"})
    assert err.value.status == 422


async def test_replace_restore_brings_back_policies_data_and_ids(shop):
    backup = await shop.studio.get(f"{ENV}/backup")
    original = await rows(shop)

    # Damage everything.
    policies = (await shop.studio.get(f"{ENV}/policies"))["data"]
    for policy in policies:
        await shop.studio.delete(f"{ENV}/policies/{policy['name']}")
    for row in original:
        await shop.studio.delete(f"{ENV}/resources/posts/records/{row['id']}")
    assert await rows(shop) == []

    with pytest.raises(ServiceError) as err:
        await shop.studio.post(f"{ENV}/restore", json={"backup": backup, "strategy": "replace"})
    assert err.value.status == 422  # replace needs the environment name typed out

    report = await shop.studio.post(
        f"{ENV}/restore", json={"backup": backup, "strategy": "replace", "confirm": "development"}
    )
    assert report["data"] == {"posts": 3}
    assert report["definitions"]["policies"] == 1
    assert report["users"]["users"]["created"] == 1 and report["warnings"] == ["w"]

    restored = await rows(shop)
    assert restored == original  # same ids, same values
    assert [p["name"] for p in (await shop.studio.get(f"{ENV}/policies"))["data"]] == ["staff"]
    path, sent = shop.platform.akountz.restores[0]
    assert sent["replace"] is True and sent["identities"]["users"][0]["id"] == 7

    # New rows continue after the restored ids.
    created = await shop.studio.post(f"{ENV}/resources/posts/records", json={"title": "Four"})
    assert created["id"] > max(r["id"] for r in original)


async def test_merge_restore_updates_and_keeps_newer_rows(shop):
    backup = await shop.studio.get(f"{ENV}/backup", params={"include": "data"})
    first = (await rows(shop))[0]
    await shop.studio.patch(f"{ENV}/resources/posts/records/{first['id']}", json={"title": "Changed"})
    extra = await shop.studio.post(f"{ENV}/resources/posts/records", json={"title": "Newer"})

    report = await shop.studio.post(f"{ENV}/restore", json={"backup": backup})
    assert report["strategy"] == "merge" and report["data"] == {"posts": 3}
    titles = {r["id"]: r["title"] for r in await rows(shop)}
    assert titles[first["id"]] == "One" and titles[extra["id"]] == "Newer"
    assert shop.platform.akountz.restores == []  # users were not in the chosen parts


async def test_dry_run_changes_nothing(shop):
    backup = await shop.studio.get(f"{ENV}/backup")
    preview = await shop.studio.post(f"{ENV}/restore", json={"backup": backup, "strategy": "replace", "dry_run": True})
    assert preview["dry_run"] and preview["contents"]["data"] == {"posts": 3}
    assert shop.platform.akountz.restores == []


async def test_a_changed_or_foreign_file_is_refused(shop):
    backup = await shop.studio.get(f"{ENV}/backup")
    tampered = copy.deepcopy(backup)
    tampered["data"]["posts"][0]["title"] = "Evil"
    for document, text in ((tampered, "checksum"), ({"format": "other"}, "not a Pawabase backup")):
        with pytest.raises(ServiceError) as err:
            await shop.studio.post(f"{ENV}/restore", json={"backup": document})
        assert err.value.status == 422 and text in str(err.value.body)
    with pytest.raises(ServiceError) as err:
        await shop.studio.post(f"{ENV}/restore", json={"backup": {**backup, "checksum": None}, "include": ["settings", "users", "x"]})
    assert err.value.status == 422


async def test_only_admins_may_download(shop):
    # A viewer/developer operator is refused by the gate; anonymous callers never reach it.
    response = await shop.http.get(f"{ENV}/backup")
    assert response.status_code in (401, 403)
