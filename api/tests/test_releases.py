"""Immutable revisions, independently routed API versions, and rollback."""

from pawabase_core.clients import ServiceError
from pawabase_core.ids import is_ulid

ENV = "/platform/v1/projects/releases/envs/development"
PUBLIC = {"list": {"enabled": True, "policy": "public"}}


async def test_versioned_releases_and_rollback(api):
    await api.studio.post(
        "/platform/v1/projects",
        json={"ref": "releases", "name": "Releases", "environments": ["development"]},
    )
    await api.studio.post(
        f"{ENV}/resources",
        json={"name": "posts", "fields": [{"name": "title", "type": "string"}], "operations": PUBLIC},
    )
    await api.studio.post(f"{ENV}/resources/posts/migrate")

    rev1 = await api.studio.post(
        f"{ENV}/branches/main/revisions", json={"message": "first public API"}
    )
    assert rev1["status"] == "valid" and rev1["number"] == 1
    await api.studio.post(f"{ENV}/api-versions", json={"name": "v1", "is_default": True})
    release1 = await api.studio.post(
        f"{ENV}/releases",
        json={"revision_id": rev1["id"], "api_version": "v1", "name": "v1.0.0"},
    )
    await api.studio.post(f"{ENV}/releases/{release1['id']}/activate")

    await api.studio.post(
        f"{ENV}/resources",
        json={"name": "widgets", "fields": [{"name": "label", "type": "string"}], "operations": PUBLIC},
    )
    await api.studio.post(f"{ENV}/resources/widgets/migrate")
    rev2 = await api.studio.post(f"{ENV}/branches/main/revisions", json={"message": "add widgets"})
    await api.studio.post(f"{ENV}/api-versions", json={"name": "v2"})
    release2 = await api.studio.post(
        f"{ENV}/releases",
        json={"revision_id": rev2["id"], "api_version": "v2", "name": "v2.0.0"},
    )
    await api.studio.post(f"{ENV}/releases/{release2['id']}/activate")

    headers = api.context_headers("releases", "development")
    assert (await api.http.get("/rest/v1/widgets", headers=headers)).status_code == 404
    assert (await api.http.get("/rest/v2/widgets", headers=headers)).status_code == 200
    v2_document = await api.studio.get(f"{ENV}/openapi", params={"version": "v2"})
    assert "/rest/v2/widgets" in v2_document["paths"]

    await api.studio.delete(f"{ENV}/resources/widgets")
    rev3 = await api.studio.post(f"{ENV}/branches/main/revisions", json={"message": "remove widgets"})
    try:
        await api.studio.post(
            f"{ENV}/releases",
            json={"revision_id": rev3["id"], "api_version": "v2", "name": "v2.1.0"},
        )
    except ServiceError as exc:
        assert exc.status == 409 and exc.body["compatibility"]["breaking"]
    else:
        raise AssertionError("a breaking release was accepted without acknowledgement")

    release3 = await api.studio.post(
        f"{ENV}/releases",
        json={
            "revision_id": rev3["id"],
            "api_version": "v2",
            "name": "v2.1.0",
            "allow_breaking": True,
        },
    )
    await api.studio.post(f"{ENV}/releases/{release3['id']}/activate")
    assert (await api.http.get("/rest/v2/widgets", headers=headers)).status_code == 404

    rolled_back = await api.studio.post(f"{ENV}/api-versions/v2/rollback")
    assert rolled_back["api_version"]["active_release_id"] == release2["id"]
    assert (await api.http.get("/rest/v2/widgets", headers=headers)).status_code == 200
    deployments = (await api.studio.get(f"{ENV}/deployments"))["data"]
    assert [item["action"] for item in deployments][:2] == ["rolled_back", "activated"]


async def test_feature_branch_definition_edits_are_isolated_until_merge(api):
    await api.studio.post(
        "/platform/v1/projects",
        json={"ref": "branching", "name": "Branching", "environments": ["development"]},
    )
    env = "/platform/v1/projects/branching/envs/development"
    flow = {
        "name": "notify",
        "description": "main definition",
        "definition": {"nodes": [{"id": "start", "data": {"block": "trigger.manual", "config": {}}}], "edges": []},
    }
    await api.studio.post(f"{env}/flows", json=flow)
    await api.studio.post(f"{env}/branches", json={"name": "feature-notify"})

    feature = {**flow, "description": "feature definition"}
    await api.studio.put(f"{env}/flows/notify", json=feature, params={"branch": "feature-notify"})

    assert (await api.studio.get(f"{env}/flows/notify"))["description"] == "main definition"
    assert (
        await api.studio.get(f"{env}/flows/notify", params={"branch": "feature-notify"})
    )["description"] == "feature definition"
    await api.studio.post(
        f"{env}/branches", json={"name": "feature-copy", "from_branch": "feature-notify"}
    )
    assert (
        await api.studio.get(f"{env}/flows/notify", params={"branch": "feature-copy"})
    )["description"] == "feature definition"

    route = await api.studio.post(
        f"{env}/routes",
        params={"branch": "feature-notify"},
        json={"method": "POST", "path": "/notify", "handler_type": "flow", "handler": "notify"},
    )
    assert is_ulid(route["id"])
    assert (await api.studio.get(f"{env}/routes"))["data"] == []
    assert (await api.studio.get(f"{env}/routes/{route['id']}", params={"branch": "feature-notify"}))["path"] == "/notify"

    revision = await api.studio.post(
        f"{env}/branches/feature-notify/revisions", json={"message": "feature work"}
    )
    assert revision["branch"] == "feature-notify"
    await api.studio.post(f"{env}/branches/feature-notify/merge", json={"target": "main"})
    assert (await api.studio.get(f"{env}/flows/notify"))["description"] == "feature definition"
    assert (await api.studio.get(f"{env}/routes"))["data"][0]["path"] == "/notify"
