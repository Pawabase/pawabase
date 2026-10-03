"""Resources keyed by ULID: generated ids, sortable, case-insensitive, 404 for malformed ones."""

import re

from pawabase_core.ids import is_ulid

ENV = "/platform/v1/projects/acme/envs/development"
ALL = {op: {"enabled": True, "policy": "public"} for op in ("list", "get", "create", "update", "delete")}


async def seed(api, id_type="ulid"):
    await api.studio.post("/platform/v1/projects", json={"ref": "acme", "name": "Acme"})
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "notes",
            "id_type": id_type,
            "fields": [{"name": "text", "type": "string", "required": True}],
            "operations": ALL,
        },
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    return api.context_headers("acme", "development")


async def test_records_get_ulid_ids_that_sort_by_creation(api):
    anon = await seed(api)
    ids = []
    for text in ("a", "b", "c"):
        response = await api.http.post("/rest/v1/notes", json={"text": text}, headers=anon)
        assert response.status_code == 201
        ids.append(response.json()["id"])
    assert all(is_ulid(i) and i == i.upper() for i in ids)
    assert ids == sorted(ids) and len(set(ids)) == 3

    listing = (await api.http.get("/rest/v1/notes", headers=anon)).json()
    rows = listing["data"] if isinstance(listing, dict) else listing
    assert {r["id"] for r in rows} == set(ids)


async def test_a_record_is_found_by_its_ulid_in_either_case_and_malformed_ids_are_404(api):
    anon = await seed(api)
    created = (await api.http.post("/rest/v1/notes", json={"text": "x"}, headers=anon)).json()
    key = created["id"]
    assert (await api.http.get(f"/rest/v1/notes/{key}", headers=anon)).json()["text"] == "x"
    assert (await api.http.get(f"/rest/v1/notes/{key.lower()}", headers=anon)).json()["id"] == key
    for bad in ("1", "not-a-ulid", key[:-1]):
        assert (await api.http.get(f"/rest/v1/notes/{bad}", headers=anon)).status_code == 404

    patched = await api.http.patch(f"/rest/v1/notes/{key.lower()}", json={"text": "y"}, headers=anon)
    assert patched.status_code == 200 and patched.json()["text"] == "y"
    assert (await api.http.delete(f"/rest/v1/notes/{key}", headers=anon)).status_code == 204
    assert (await api.http.get(f"/rest/v1/notes/{key}", headers=anon)).status_code == 404


async def test_the_column_is_a_26_character_key_and_integer_resources_are_unchanged(api):
    await seed(api)
    from app.data.sql import primary_key_column

    assert primary_key_column("postgres", "id", "ulid") == '"id" CHAR(26) PRIMARY KEY'
    assert primary_key_column("mysql", "id", "ulid") == "`id` CHAR(26) PRIMARY KEY"
    assert primary_key_column("sqlite", "id", "ulid") == '"id" TEXT PRIMARY KEY'
    assert "AUTOINCREMENT" in primary_key_column("sqlite", "id", "integer")


async def test_integer_resources_keep_counting(api):
    anon = await seed(api, id_type="integer")
    first = (await api.http.post("/rest/v1/notes", json={"text": "a"}, headers=anon)).json()
    assert first["id"] == 1 and not re.fullmatch(r"[0-9A-Z]{26}", str(first["id"]))
