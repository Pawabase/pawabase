"""Blueprints: export an environment, create a new project from it."""

import json

import pytest

from pawabase_core.clients import ServiceError

SRC = "/platform/v1/projects/src/envs/development"


class FakeAkountz:
    """Roles live in Akountz; the API only reads and writes them over HTTP."""

    def __init__(self):
        self.roles = {("src", "development"): [{"name": "editor", "description": "Edits", "permissions": ["posts.write"]}]}
        self.puts = []

    async def get(self, path, **kwargs):
        _, _, _, _, project, _, env, _ = path.split("/")
        return {"data": self.roles.get((project, env), []), "permissions": []}

    async def put(self, path, json=None, **kwargs):
        self.puts.append((path, json))
        return json

    async def close(self):
        pass


@pytest.fixture
async def source(api):
    api.platform.akountz = FakeAkountz()
    await api.studio.post("/platform/v1/projects", json={"ref": "src", "name": "Source", "environments": ["development"]})
    definitions = [
        ("schemas", {"name": "PostInput", "fields": [{"name": "title", "type": "string", "required": True}]}),
        ("policies", {"name": "editors", "condition": {"role": "editor"}}),
        ("resources", {
            "name": "posts",
            "fields": [{"name": "title", "type": "string", "required": True}],
            "operations": {"list": {"enabled": True, "policy": "public"}, "create": {"enabled": True, "policy": "editors"}},
            "relations": [{"name": "comments", "type": "has_many", "resource": "comments", "field": "post_id"}],
        }),
        ("resources", {
            "name": "comments",
            "fields": [{"name": "post_id", "type": "ulid", "required": True}, {"name": "text", "type": "string"}],
            "operations": {"list": {"enabled": True, "policy": "public"}},
            "relations": [{"name": "post", "resource": "posts", "field": "post_id"}],
        }),
        ("mail-templates", {"name": "hello", "subject": "Hi {{ name }}", "text": "Hello"}),
        ("flows", {"name": "echo", "definition": {
            "nodes": [
                {"id": "in", "data": {"block": "trigger.http", "config": {}}},
                {"id": "out", "data": {"block": "response.return", "config": {"body": "{{ input.body }}"}}},
            ],
            "edges": [{"source": "in", "target": "out"}],
        }}),
        ("routes", {"method": "POST", "path": "/echo", "handler_type": "flow", "handler": "echo", "input_schema": "PostInput", "policy": "public"}),
        ("buckets", {"name": "images", "public": True, "write_policy": "editors", "accepts": ["image/*"]}),
        ("subscriptions", {"name": "on_post", "event": "posts.created", "target_type": "flow", "target": "echo"}),
        ("webhooks", {"name": "crm", "url": "https://crm.example/hook", "events": ["posts.*"], "enabled": True}),
        ("inbound-hooks", {"slug": "stripe", "target_type": "event", "target": "stripe.event", "verification": "hmac-sha256"}),
        ("schedules", {"name": "nightly", "cron": "0 3 * * *", "target_type": "event", "target": "nightly.tick"}),
    ]
    for kind, body in definitions:
        await api.studio.post(f"{SRC}/{kind}", json=body)
    for name in ("posts", "comments"):
        await api.studio.post(f"{SRC}/resources/{name}/migrate")
    first = await api.studio.post(f"{SRC}/resources/posts/records", json={"title": "Hello"})
    await api.studio.post(f"{SRC}/resources/posts/records", json={"title": "World"})
    await api.studio.post(f"{SRC}/resources/comments/records", json={"post_id": first["id"], "text": "Nice"})
    return api


async def test_export_and_create_from_blueprint(source):
    api = source
    blueprint = await api.studio.get(f"{SRC}/blueprint", params={"data": "true"})
    assert blueprint["format"] == "pawabase.blueprint" and blueprint["version"] == 1
    assert {k: len(v) for k, v in blueprint["definitions"].items()} == {
        "schemas": 1, "policies": 1, "resources": 2, "mail-templates": 1, "flows": 1, "routes": 1,
        "buckets": 1, "subscriptions": 1, "webhooks": 1, "inbound-hooks": 1, "schedules": 1,
    }
    assert [r["title"] for r in blueprint["data"]["posts"]] == ["Hello", "World"]
    assert blueprint["roles"] == [{"name": "editor", "description": "Edits", "permissions": ["posts.write"]}]
    # Signing secrets never leave the environment that made them.
    assert "secret_ciphertext" not in json.dumps(blueprint)
    assert "secret" not in blueprint["definitions"]["webhooks"][0]
    assert "secret" not in blueprint["definitions"]["inbound-hooks"][0]

    created = await api.studio.post(
        "/platform/v1/projects",
        json={"ref": "copy", "name": "Copy", "environments": ["development", "production"], "blueprint": blueprint},
    )
    report = created["blueprint"]
    assert report["definitions"]["resources"] == 2 and report["data_environment"] == "development"
    assert "inbound-hooks:stripe" in report["secrets"]["development"]  # a fresh secret, shown once
    assert any("Webhooks were imported switched off" in w for w in report["warnings"])

    for env in ("development", "production"):
        base = f"/platform/v1/projects/copy/envs/{env}"
        posts = await api.studio.get(f"{base}/resources/posts")
        assert posts["relations"][0]["resource"] == "comments"
        assert (await api.studio.get(f"{base}/webhooks/crm"))["enabled"] is False
        assert (await api.studio.get(f"{base}/routes"))["data"][0]["handler"] == "echo"
    roles_written = {path.split("/")[6] for path, body in api.platform.akountz.puts if body["name"] == "editor"}
    assert roles_written == {"development", "production"}

    # Data went into the first environment only, with ids preserved, and relations resolve.
    dev = api.context_headers("copy", "development")
    rows = (await api.http.get("/rest/v1/posts?expand=comments&sort=id", headers=dev)).json()["data"]
    assert [p["title"] for p in rows] == ["Hello", "World"]
    assert rows[0]["id"] == blueprint["data"]["posts"][0]["id"] and rows[0]["comments"][0]["text"] == "Nice"
    prod = api.context_headers("copy", "production")
    assert (await api.http.get("/rest/v1/posts", headers=prod)).json()["data"] == []


async def test_bad_blueprints_leave_nothing_behind(source):
    api = source
    blueprint = await api.studio.get(f"{SRC}/blueprint")
    blueprint["definitions"]["resources"][0]["operations"]["create"]["policy"] = "no_such_policy"
    with pytest.raises(ServiceError) as refused:
        await api.studio.post(
            "/platform/v1/projects", json={"ref": "broken", "name": "Broken", "blueprint": blueprint}
        )
    assert refused.value.status == 422
    detail = refused.value.body
    assert detail["where"].startswith("definitions.resources[0] (posts)") and "no_such_policy" in detail["problem"]
    assert "broken" not in [p["ref"] for p in (await api.studio.get("/platform/v1/projects"))["data"]]

    with pytest.raises(ServiceError) as invalid:
        await api.studio.post(
            "/platform/v1/projects",
            json={"ref": "nope", "name": "Nope", "blueprint": {"format": "something-else"}},
        )
    assert invalid.value.status == 422
    assert invalid.value.body["message"] == "this is not a valid blueprint"


async def test_blueprints_only_create_new_projects(source):
    api = source
    blueprint = await api.studio.get(f"{SRC}/blueprint")
    with pytest.raises(ServiceError) as again:
        await api.studio.post(
            "/platform/v1/projects", json={"ref": "src", "name": "Overwrite", "blueprint": blueprint}
        )
    assert again.value.status == 409  # an existing project is never touched
