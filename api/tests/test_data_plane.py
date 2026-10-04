"""Resources end to end: definition, migration, the compiled API, policies."""

import pytest

ENV = "/platform/v1/envs/development"

POSTS = {
    "name": "posts",
    "fields": [
        {"name": "title", "type": "string", "required": True, "max_length": 120},
        {"name": "body", "type": "text"},
        {"name": "status", "type": "string", "enum": ["draft", "live"], "default": "draft"},
        {"name": "tags", "type": "array", "items": {"type": "string"}},
        {"name": "views", "type": "integer", "minimum": 0, "default": 0},
    ],
    "owner_field": "owner_id",
    "operations": {
        "list": {
            "enabled": True,
            "policy": {"any": [{"eq": ["$record.status", "live"]}, {"owner": "owner_id"}]},
        },
        "get": {
            "enabled": True,
            "policy": {"any": [{"eq": ["$record.status", "live"]}, {"owner": "owner_id"}]},
        },
        "create": {"enabled": True, "policy": "authenticated"},
        "update": {"enabled": True, "policy": "owner"},
        "delete": {"enabled": True, "policy": "owner"},
    },
    "relations": [
        {"name": "comments", "resource": "comments", "field": "post_id", "type": "has_many"}
    ],
    "cache_ttl": 30,
}

COMMENTS = {
    "name": "comments",
    "fields": [
        {"name": "post_id", "type": "ulid", "required": True, "indexed": True},
        {"name": "text", "type": "string", "required": True},
    ],
    "operations": {"list": {"enabled": True, "policy": "public"}},
    "relations": [{"name": "post", "resource": "posts", "field": "post_id"}],
}


@pytest.fixture
async def acme(api):
    for resource in (POSTS, COMMENTS):
        await api.studio.post(f"{ENV}/resources", json=resource)
        migrated = await api.studio.post(f"{ENV}/resources/{resource['name']}/migrate")
        assert migrated["statements"]
    return api


async def test_crud_with_owner_policies(acme):
    api = acme
    ada = api.user_headers("development", user_id="1")
    bob = api.user_headers("development", user_id="2")
    anon = api.context_headers("development")

    assert (await api.http.get("/rest/v1/posts")).status_code == 401  # no key at all
    refused = await api.http.post("/rest/v1/posts", json={"title": "x"}, headers=anon)
    assert refused.status_code == 401

    invalid = await api.http.post("/rest/v1/posts", json={"body": "no title"}, headers=ada)
    assert invalid.status_code == 422

    draft = await api.http.post(
        "/rest/v1/posts", json={"title": "Ada's draft", "tags": ["a"]}, headers=ada
    )
    assert draft.status_code == 201, draft.text
    draft = draft.json()
    assert draft["owner_id"] == "1" and draft["status"] == "draft" and draft["tags"] == ["a"]

    live = (
        await api.http.post(
            "/rest/v1/posts", json={"title": "Bob live", "status": "live"}, headers=bob
        )
    ).json()

    # Anonymous callers see only live posts; Ada sees live posts and her own drafts.
    anon_list = (await api.http.get("/rest/v1/posts", headers=anon)).json()
    assert [p["title"] for p in anon_list["data"]] == ["Bob live"]
    ada_list = (await api.http.get("/rest/v1/posts?sort=-id", headers=ada)).json()
    assert [p["title"] for p in ada_list["data"]] == ["Bob live", "Ada's draft"]

    # Bob cannot read, edit or delete Ada's draft; it is simply not there for him.
    assert (await api.http.get(f"/rest/v1/posts/{draft['id']}", headers=bob)).status_code == 404
    assert (
        await api.http.patch(f"/rest/v1/posts/{draft['id']}", json={"title": "hacked"}, headers=bob)
    ).status_code == 403
    assert (await api.http.delete(f"/rest/v1/posts/{draft['id']}", headers=bob)).status_code == 403

    updated = await api.http.patch(
        f"/rest/v1/posts/{draft['id']}", json={"status": "live", "views": 3}, headers=ada
    )
    assert updated.status_code == 200 and updated.json()["status"] == "live"

    filtered = (await api.http.get("/rest/v1/posts?filter[views]=gte.1", headers=anon)).json()
    assert [p["id"] for p in filtered["data"]] == [draft["id"]]
    bad_filter = await api.http.get("/rest/v1/posts?filter[nope]=1", headers=anon)
    assert bad_filter.status_code == 400

    # A secret key (service role) bypasses policies.
    service = api.context_headers("development", role="service")
    everything = (await api.http.get("/rest/v1/posts", headers=service)).json()
    assert everything["total"] == 2

    await api.studio.post(
        f"{ENV}/resources/comments/records", json={"post_id": live["id"], "text": "nice"}
    )
    expanded = (
        await api.http.get(f"/rest/v1/posts/{live['id']}?expand=comments", headers=anon)
    ).json()
    assert [c["text"] for c in expanded["comments"]] == ["nice"]
    comments = (await api.http.get("/rest/v1/comments?expand=post", headers=anon)).json()
    assert comments["data"][0]["post"]["title"] == "Bob live"

    assert (await api.http.delete(f"/rest/v1/posts/{draft['id']}", headers=ada)).status_code == 204


async def test_events_and_cache_invalidation(acme):
    api = acme
    from database.models import EventLog

    ada = api.user_headers("development", user_id="1")
    anon = api.context_headers("development")
    await api.http.post("/rest/v1/posts", json={"title": "one", "status": "live"}, headers=ada)
    first = (await api.http.get("/rest/v1/posts", headers=anon)).json()
    await api.http.post("/rest/v1/posts", json={"title": "two", "status": "live"}, headers=ada)
    second = (await api.http.get("/rest/v1/posts", headers=anon)).json()
    assert (
        len(first["data"]) == 1 and len(second["data"]) == 2
    )  # the write invalidated the cached list
    await api.drain()
    names = [e.name for e in await EventLog.all()]
    assert names.count("posts.created") == 2


async def test_openapi_and_docs(acme):
    api = acme
    anon = api.context_headers("development")
    document = (await api.http.get("/rest/v1/openapi.json", headers=anon)).json()
    assert "/rest/v1/posts" in document["paths"] and "/rest/v1/posts/{id}" in document["paths"]
    create_schema = document["paths"]["/rest/v1/posts"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    assert create_schema["title"] == "PostsCreate" and create_schema["required"] == ["title"]
    docs = await api.http.get("/rest/v1/docs", headers=anon)
    assert docs.status_code == 200 and "atlas" in docs.text.lower()


async def test_scoped_keys(acme):
    api = acme
    read_only = api.context_headers("development", role="service", scopes=["resource:read"])
    assert (await api.http.get("/rest/v1/posts", headers=read_only)).status_code == 200
    assert (
        await api.http.post("/rest/v1/posts", json={"title": "x"}, headers=read_only)
    ).status_code == 403


async def test_requests_outside_the_startup_context(acme):
    """A server handles each request in its own task, without the startup
    task's database context. Every middleware that queries the database,
    the data-plane dispatcher included, must still find it."""
    import asyncio
    import contextvars

    api = acme
    headers = api.context_headers("development")

    async def request():
        return await api.http.get("/rest/v1/comments", headers=headers)

    task = asyncio.get_running_loop().create_task(request(), context=contextvars.Context())
    response = await task
    assert response.status_code == 200, response.text


async def test_store_applies_declared_defaults(acme):
    """Flows and functions write through the store, not the compiled model,
    and still get the resource's defaults."""
    api = acme
    state = await api.platform.state("development")
    store = await state.store("posts")
    row = await store.create({"title": "from a flow", "owner_id": "1"})
    assert (row["status"], row["views"]) == ("draft", 0)


async def test_each_write_checks_its_own_policy(acme):
    # Regression: create, update and delete once shared one closure variable,
    # so every write was judged by the *delete* policy.
    api = acme
    ledger = {
        "name": "ledger",
        "fields": [{"name": "note", "type": "string", "required": True}],
        "operations": {
            "list": {"enabled": True, "policy": "authenticated"},
            "get": {"enabled": True, "policy": "authenticated"},
            "create": {"enabled": True, "policy": "authenticated"},
            "update": {"enabled": True, "policy": "authenticated"},
            "delete": {"enabled": True, "policy": "deny"},
        },
    }
    await api.studio.post(f"{ENV}/resources", json=ledger)
    await api.studio.post(f"{ENV}/resources/ledger/migrate")
    ada = api.user_headers("development", user_id="1")

    created = await api.http.post("/rest/v1/ledger", json={"note": "paid"}, headers=ada)
    assert created.status_code == 201, created.text
    record = created.json()
    updated = await api.http.patch(f"/rest/v1/ledger/{record['id']}", json={"note": "paid in full"}, headers=ada)
    assert updated.status_code == 200, updated.text
    assert (await api.http.delete(f"/rest/v1/ledger/{record['id']}", headers=ada)).status_code == 403


async def test_operators_read_the_openapi_document_without_public_docs(acme):
    # Studio's "API docs" button relies on this; /docs/v1 stays 404 until
    # the environment turns public_docs on.
    api = acme
    document = await api.studio.get(f"{ENV}/openapi")
    assert "/rest/v1/posts" in document["paths"]
    assert (await api.http.get("/docs/v1/acme/development")).status_code == 404


async def test_expand_respects_the_related_resources_read_policy(acme):
    # Regression: ?expand= read related rows straight from the store, so a
    # relation leaked rows the related resource's own policy refuses.
    api = acme
    ledger = {
        "name": "receipts",
        "fields": [
            {"name": "post_id", "type": "ulid", "required": True},
            {"name": "amount", "type": "number", "required": True},
        ],
        "operations": {
            "list": {"enabled": True, "policy": {"role": "bursar"}},
            "get": {"enabled": True, "policy": {"role": "bursar"}},
        },
    }
    await api.studio.post(f"{ENV}/resources", json=ledger)
    await api.studio.post(f"{ENV}/resources/receipts/migrate")
    posts = await api.studio.get(f"{ENV}/resources/posts")
    posts = {k: v for k, v in posts.items() if k not in ("id", "environment", "environment_id", "created_at", "updated_at", "version")}
    posts["relations"] = [*posts["relations"], {"name": "receipts", "type": "has_many", "resource": "receipts", "field": "post_id"}]
    await api.studio.put(f"{ENV}/resources/posts", json=posts)

    ada = api.user_headers("development", user_id="1")
    post = (await api.http.post("/rest/v1/posts", json={"title": "t", "status": "live"}, headers=ada)).json()
    await api.studio.post(f"{ENV}/resources/receipts/records", json={"post_id": post["id"], "amount": 9})

    assert (await api.http.get("/rest/v1/receipts", headers=ada)).status_code == 403
    expanded = (await api.http.get(f"/rest/v1/posts/{post['id']}?expand=receipts", headers=ada)).json()
    assert expanded["receipts"] == []
    service = api.context_headers("development", role="service")
    as_service = (await api.http.get(f"/rest/v1/posts/{post['id']}?expand=receipts", headers=service)).json()
    assert [r["amount"] for r in as_service["receipts"]] == [9]
