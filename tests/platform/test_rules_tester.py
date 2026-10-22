"""The rules tester: decisions, status codes, and previews of what a caller would see, for real resources and users."""

import pytest

from pawabase_server.db.models import AuthUser, Environment

ENV = "/platform/v1/envs/development"

NOTES = {
    "name": "notes",
    "fields": [
        {"name": "owner_id", "type": "string"},
        {"name": "body", "type": "string"},
        {"name": "shared", "type": "boolean", "default": False},
    ],
    "operations": {
        "list": {
            "enabled": True,
            "policy": {"any": [{"eq": ["$record.shared", True]}, {"owner": "owner_id"}]},
        },
        "get": {"enabled": True, "policy": "owner"},
        "create": {"enabled": True, "policy": "authenticated"},
        "update": {"enabled": True, "policy": {"all": [{"owner": "owner_id"}, {"role": "editor"}]}},
        "delete": {"enabled": False},
    },
}


@pytest.fixture
async def notes(api):
    await api.studio.post(f"{ENV}/resources", json=NOTES)
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    rows = [
        {"owner_id": "ada", "body": "mine", "shared": False},
        {"owner_id": "ada", "body": "mine too", "shared": True},
        {"owner_id": "bob", "body": "bob's private", "shared": False},
        {"owner_id": "bob", "body": "bob's shared", "shared": True},
    ]
    ids = []
    for row in rows:
        ids.append((await api.studio.post(f"{ENV}/resources/notes/records", json=row))["id"])
    api.note_ids = ids
    return api


async def ask(api, **body):
    return await api.studio.post(f"{ENV}/policies/test", json=body)


async def test_a_list_is_previewed_against_the_table(notes):
    out = await ask(
        notes, resource="notes", operation="list", caller={"kind": "user", "user_id": "ada"}
    )
    assert out["allowed"] is True and out["status"] == 200
    preview = out["preview"]
    assert preview["scanned"] == 4
    assert preview["visible"] == 3  # ada's two, and bob's shared one
    assert preview["hidden"] == 1
    assert preview["hidden_example"]["row"]["body"] == "bob's private"
    assert preview["hidden_example"]["trace"]["result"] is False


async def test_anonymous_is_refused_with_401_and_a_reason(notes):
    out = await ask(notes, resource="notes", operation="create", caller={"kind": "anonymous"})
    assert out["allowed"] is False and out["status"] == 401
    assert out["trace"]["result"] is False


async def test_a_signed_in_user_who_fails_the_rule_gets_403(notes):
    out = await ask(
        notes,
        resource="notes",
        operation="update",
        caller={"kind": "user", "user_id": "ada", "roles": ["viewer"]},
        record_id=str(notes.note_ids[0]),
        input={"body": "changed"},
    )
    assert out["allowed"] is False and out["status"] == 403
    results = {c["op"]: c["result"] for c in out["trace"]["children"]}
    assert results == {"owner": True, "role": False}


async def test_the_same_update_passes_with_the_role(notes):
    out = await ask(
        notes,
        resource="notes",
        operation="update",
        caller={"kind": "user", "user_id": "ada", "roles": ["editor"]},
        record_id=str(notes.note_ids[0]),
    )
    assert out["allowed"] is True


async def test_a_record_that_is_not_visible_answers_404(notes):
    out = await ask(
        notes,
        resource="notes",
        operation="get",
        caller={"kind": "user", "user_id": "ada"},
        record_id=str(notes.note_ids[2]),
    )
    assert out["allowed"] is False and out["status"] == 404
    own = await ask(
        notes,
        resource="notes",
        operation="get",
        caller={"kind": "user", "user_id": "ada"},
        record_id=str(notes.note_ids[0]),
    )
    assert own["allowed"] is True


async def test_a_disabled_operation_has_no_route(notes):
    out = await ask(notes, resource="notes", operation="delete")
    assert out["enabled"] is False and out["status"] == 404


async def test_service_credentials_bypass_policies(notes):
    out = await ask(
        notes,
        resource="notes",
        operation="update",
        caller={"kind": "service"},
        record_id=str(notes.note_ids[2]),
    )
    assert out["allowed"] is True and out["trace"]["op"] == "service"


async def test_a_real_user_is_loaded_with_their_roles(api):
    environment = await Environment.get(name="development")
    user = await AuthUser.create(
        env=environment.name, email="eve@example.com", username="eve", password="x" * 12
    )
    out = await ask(
        api,
        policy={"eq": ["$auth.email", "eve@example.com"]},
        caller={"kind": "user", "user_id": str(user.id)},
    )
    assert out["caller"]["source"] == "user" and out["allowed"] is True


async def test_an_unknown_user_id_is_flagged(api):
    out = await ask(api, policy="authenticated", caller={"kind": "user", "user_id": "nobody"})
    assert out["caller"]["source"] == "manual"
    assert any("nobody" in w for w in out["warnings"])


async def test_a_policy_can_be_tried_without_a_resource(api):
    out = await ask(api, policy="role:admin", caller={"kind": "user", "roles": ["admin"]})
    assert out["allowed"] is True and out["policy"] == "role:admin"
    refused = await ask(api, policy="role:admin", caller={"kind": "user", "roles": ["member"]})
    assert refused["allowed"] is False and refused["status"] == 403
