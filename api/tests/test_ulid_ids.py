"""Resources keyed by ULID: generated ids, sortable, case-insensitive, 404 for malformed ones."""

import pytest

from pawabase_core.clients import ServiceError
from pawabase_core.ids import is_ulid

ENV = "/platform/v1/envs/development"
ALL = {
    op: {"enabled": True, "policy": "public"}
    for op in ("list", "get", "create", "update", "delete")
}


async def seed(api, id_type="ulid"):
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
    return api.context_headers("development")


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

    patched = await api.http.patch(
        f"/rest/v1/notes/{key.lower()}", json={"text": "y"}, headers=anon
    )
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


async def test_new_resources_default_to_ulid_and_integer_keys_are_refused(api):
    made = await api.studio.post(f"{ENV}/resources", json={"name": "plain", "fields": []})
    assert made["id_type"] == "ulid"  # nothing said, so ULID
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(
            f"{ENV}/resources", json={"name": "legacy", "id_type": "integer", "fields": []}
        )
    assert refused.value.status == 422 and "ulid" in str(refused.value)
    assert (
        await api.studio.post(
            f"{ENV}/resources", json={"name": "ids", "id_type": "uuid", "fields": []}
        )
    )["id_type"] == "uuid"


async def test_an_existing_integer_resource_keeps_working_and_cannot_be_retyped(api):
    from database.models import Environment, Resource

    environment = await Environment.get(name="development")
    await Resource.create(
        environment=environment, name="old", table="old", id_type="integer",
        fields_=[{"name": "text", "type": "string"}],
        operations={"list": {"enabled": True, "policy": "public"}, "create": {"enabled": True, "policy": "public"}},
    )  # fmt: skip
    await api.studio.post(f"{ENV}/resources/old/migrate")
    anon = api.context_headers("development")
    first = (await api.http.post("/rest/v1/old", json={"text": "a"}, headers=anon)).json()
    assert first["id"] == 1  # still auto-increment: records already carry those keys

    body = {
        "name": "old",
        "fields": [{"name": "text", "type": "string"}],
        "operations": {"create": {"enabled": True, "policy": "public"}},
        "description": "edited",
    }
    resaved = await api.studio.put(f"{ENV}/resources/old", json=body)  # id_type not named: kept
    assert resaved["id_type"] == "integer" and resaved["description"] == "edited"
    assert (await api.studio.put(f"{ENV}/resources/old", json={**body, "id_type": "integer"}))[
        "id_type"
    ] == "integer"
    with pytest.raises(ServiceError) as retyped:
        await api.studio.put(f"{ENV}/resources/old", json={**body, "id_type": "ulid"})
    assert retyped.value.status == 409
    assert (await api.http.post("/rest/v1/old", json={"text": "b"}, headers=anon)).json()["id"] == 2
