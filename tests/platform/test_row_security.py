"""Row-level security of the generated API: what one user can do to, and learn about, records that are not theirs."""

import pytest

ENV = "/platform/v1/envs/development"

OWN = "owner:author_id"
NOTES = {
    "name": "notes",
    "fields": [
        {"name": "title", "type": "string", "required": True, "max_length": 120},
        {"name": "author_id", "type": "string", "required": True, "indexed": True},
        {"name": "pin", "type": "string", "write_only": True},
    ],
    "operations": {op: {"enabled": True, "policy": OWN} for op in ("list", "get", "create", "update", "delete")},
    "cache_ttl": 30,
}
# The platform fills the owner in (and refuses to let it change) when a resource names its owner column.
POSTS = {
    "name": "posts",
    "fields": [{"name": "title", "type": "string", "required": True}],
    "owner_field": "owner_id",
    "operations": {
        "list": {"enabled": True, "policy": "authenticated"},
        "get": {"enabled": True, "policy": "authenticated"},
        "create": {"enabled": True, "policy": "authenticated"},
        "update": {"enabled": True, "policy": "owner"},
        "delete": {"enabled": True, "policy": "owner"},
    },
}
# Anyone may read a comment, but a comment points at a note that only its author may read.
COMMENTS = {
    "name": "comments",
    "fields": [{"name": "note_id", "type": "ulid", "required": True}, {"name": "text", "type": "string"}],
    "operations": {"list": {"enabled": True, "policy": "public"}},
    "relations": [{"name": "note", "resource": "notes", "field": "note_id"}],
}


@pytest.fixture
async def shop(api):
    for resource in (NOTES, POSTS, COMMENTS):
        await api.studio.post(f"{ENV}/resources", json=resource)
        await api.studio.post(f"{ENV}/resources/{resource['name']}/migrate")
    return api


def people(api):
    return (
        api.user_headers("development", user_id="1"),
        api.user_headers("development", user_id="2"),
        api.context_headers("development"),
        api.context_headers("development", role="service"),
    )


async def note(api, headers, **fields):
    made = await api.http.post("/rest/v1/notes", json={"title": "t", "author_id": "1", **fields}, headers=headers)
    assert made.status_code == 201, made.text
    return made.json()


async def test_a_user_cannot_create_a_record_in_someone_elses_name(shop):
    ada, bob, anon, service = people(shop)
    refused = await shop.http.post("/rest/v1/notes", json={"title": "x", "author_id": "2"}, headers=ada)
    assert refused.status_code == 403
    assert (await shop.http.get("/rest/v1/notes", headers=service)).json()["total"] == 0


async def test_a_user_cannot_hand_their_record_to_someone_else_on_update(shop):
    ada, bob, anon, service = people(shop)
    mine = await note(shop, ada)
    handed = await shop.http.patch(f"/rest/v1/notes/{mine['id']}", json={"author_id": "2"}, headers=ada)
    assert handed.status_code == 403
    still = (await shop.http.get(f"/rest/v1/notes/{mine['id']}", headers=service)).json()
    assert still["author_id"] == "1"


async def test_the_owner_column_of_a_resource_cannot_be_rewritten(shop):
    ada, bob, anon, service = people(shop)
    made = (await shop.http.post("/rest/v1/posts", json={"title": "mine"}, headers=ada)).json()
    assert made["owner_id"] == "1"
    spoofed = await shop.http.post("/rest/v1/posts", json={"title": "spoof", "owner_id": "2"}, headers=ada)
    assert spoofed.json()["owner_id"] == "1"
    moved = await shop.http.patch(f"/rest/v1/posts/{made['id']}", json={"owner_id": "2"}, headers=ada)
    assert moved.status_code == 200 and moved.json()["owner_id"] == "1"


async def test_put_is_as_guarded_as_patch(shop):
    ada, bob, anon, service = people(shop)
    mine = await note(shop, ada)
    assert (await shop.http.put(f"/rest/v1/notes/{mine['id']}", json={"title": "x"}, headers=bob)).status_code in (403, 404)
    assert (await shop.http.get(f"/rest/v1/notes/{mine['id']}", headers=service)).json()["title"] == "t"


async def test_a_record_with_no_owner_belongs_to_nobody(shop):
    ada, bob, anon, service = people(shop)
    orphan = (await shop.http.post("/rest/v1/notes", json={"title": "o", "author_id": "1"}, headers=ada)).json()
    await shop.studio.patch(f"{ENV}/resources/notes/records/{orphan['id']}", json={"author_id": ""})
    for who in (ada, bob, anon):
        assert (await shop.http.patch(f"/rest/v1/notes/{orphan['id']}", json={"title": "x"}, headers=who)).status_code in (401, 403, 404)
        assert (await shop.http.delete(f"/rest/v1/notes/{orphan['id']}", headers=who)).status_code in (401, 403, 404)


async def test_system_columns_cannot_be_written(shop):
    ada, bob, anon, service = people(shop)
    mine = await note(shop, ada)
    other = await note(shop, bob, author_id="2")
    moved = await shop.http.patch(f"/rest/v1/notes/{mine['id']}", json={"id": other["id"], "title": "t2"}, headers=ada)
    assert moved.status_code in (200, 422)
    ids = {n["id"] for n in (await shop.http.get("/rest/v1/notes", headers=service)).json()["data"]}
    assert ids == {mine["id"], other["id"]}
    assert (await shop.http.get(f"/rest/v1/notes/{other['id']}", headers=service)).json()["title"] == "t"


async def test_lists_and_counts_never_include_other_peoples_records(shop):
    ada, bob, anon, service = people(shop)
    await note(shop, ada, title="ada-1")
    await note(shop, ada, title="ada-2")
    await note(shop, bob, title="bob-1", author_id="2")
    mine = (await shop.http.get("/rest/v1/notes", headers=bob)).json()
    assert [n["title"] for n in mine["data"]] == ["bob-1"] and mine["total"] == 1
    # Asking for someone else's records by filter, or by a wide page, gets nothing more.
    nosy = (await shop.http.get("/rest/v1/notes?filter[author_id]=1&per_page=200", headers=bob)).json()
    assert nosy["data"] == [] and nosy["total"] == 0
    assert (await shop.http.get("/rest/v1/notes", headers=anon)).status_code in (401, 403)


async def test_a_cached_read_is_not_handed_to_the_next_caller(shop):
    ada, bob, anon, service = people(shop)
    mine = await note(shop, ada)
    assert (await shop.http.get(f"/rest/v1/notes/{mine['id']}", headers=ada)).status_code == 200
    assert (await shop.http.get("/rest/v1/notes", headers=ada)).json()["total"] == 1
    assert (await shop.http.get(f"/rest/v1/notes/{mine['id']}", headers=bob)).status_code == 404
    assert (await shop.http.get("/rest/v1/notes", headers=bob)).json()["total"] == 0


async def test_a_relation_does_not_open_a_door_to_records_the_caller_cannot_read(shop):
    ada, bob, anon, service = people(shop)
    secret = await note(shop, ada, title="private")
    await shop.studio.post(f"{ENV}/resources/comments/records", json={"note_id": secret["id"], "text": "hi"})
    seen = (await shop.http.get("/rest/v1/comments?expand=note", headers=bob)).json()
    assert seen["data"][0]["note"] is None
    owner = (await shop.http.get("/rest/v1/comments?expand=note", headers=ada)).json()
    assert owner["data"][0]["note"]["title"] == "private"


async def test_write_only_columns_cannot_be_probed_with_filters_or_sorting(shop):
    ada, bob, anon, service = people(shop)
    mine = await note(shop, ada, pin="1234")
    assert "pin" not in mine
    for probe in ("filter[pin]=like.12%25", "filter[pin]=1234", "sort=pin", "sort=-pin"):
        refused = await shop.http.get(f"/rest/v1/notes?{probe}", headers=ada)
        assert refused.status_code == 400, probe
    # Studio and secret keys see the column as it is.
    assert (await shop.http.get("/rest/v1/notes?filter[pin]=1234", headers=service)).json()["total"] == 1


async def test_columns_a_transformer_removes_cannot_be_probed_either(shop):
    ada, bob, anon, service = people(shop)
    await shop.studio.post(f"{ENV}/resources", json={
        "name": "accounts", "fields": [{"name": "name", "type": "string"}, {"name": "internal", "type": "string"}],
        "transformer": {"omit": ["internal"]},
        "operations": {"list": {"enabled": True, "policy": "public"}},
    })
    await shop.studio.post(f"{ENV}/resources/accounts/migrate")
    await shop.studio.post(f"{ENV}/resources/accounts/records", json={"name": "a", "internal": "token-abc"})
    page = (await shop.http.get("/rest/v1/accounts", headers=anon)).json()
    assert "internal" not in page["data"][0]
    assert (await shop.http.get("/rest/v1/accounts?filter[internal]=like.token%25", headers=anon)).status_code == 400
    assert (await shop.http.get("/rest/v1/accounts?sort=internal", headers=anon)).status_code == 400


async def test_a_policy_that_no_longer_exists_closes_the_door(shop):
    ada, bob, anon, service = people(shop)
    await shop.studio.post(f"{ENV}/policies", json={"name": "temp_gate", "condition": {"authenticated": True}})
    await shop.studio.post(f"{ENV}/resources", json={
        "name": "gated", "fields": [{"name": "label", "type": "string"}],
        "operations": {"list": {"enabled": True, "policy": "temp_gate"}},
    })
    await shop.studio.post(f"{ENV}/resources/gated/migrate")
    assert (await shop.http.get("/rest/v1/gated", headers=ada)).status_code == 200
    gone = await shop.studio.delete(f"{ENV}/policies/temp_gate")
    after = await shop.http.get("/rest/v1/gated", headers=ada)
    # Either the policy could not be removed while it was in use, or the endpoint refuses; never a 5xx and never open.
    assert (gone.status_code == 409 and after.status_code == 200) or after.status_code in (401, 403, 404)
