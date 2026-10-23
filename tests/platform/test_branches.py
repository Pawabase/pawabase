"""Branches as Studio uses them: main is always there to switch back to, and a branch can be thrown away."""

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"
FLOW = {
    "name": "notify",
    "description": "live",
    "definition": {
        "nodes": [{"id": "start", "data": {"block": "trigger.manual", "config": {}}}],
        "edges": [],
    },
}


async def _names(api):
    return [b["name"] for b in (await api.studio.get(f"{ENV}/branches"))["data"]]


async def test_main_is_listed_first_even_before_it_has_a_revision(api):
    assert await _names(api) == ["main"]
    await api.studio.post(f"{ENV}/branches", json={"name": "feature-a"})
    await api.studio.post(f"{ENV}/branches", json={"name": "alpha"})
    names = await _names(api)
    assert names[0] == "main" and sorted(names[1:]) == ["alpha", "feature-a"]
    main = (await api.studio.get(f"{ENV}/branches"))["data"][0]
    assert main["protected"] is True


async def test_main_is_not_listed_twice_once_it_exists(api):
    await api.studio.post(f"{ENV}/branches/main/revisions", json={"message": "first"})
    await api.studio.post(f"{ENV}/branches", json={"name": "feature-a"})
    assert await _names(api) == ["main", "feature-a"]


async def test_a_branch_can_be_deleted_and_main_is_untouched(api):
    await api.studio.post(f"{ENV}/flows", json=FLOW)
    await api.studio.post(f"{ENV}/branches", json={"name": "feature-a"})
    await api.studio.put(
        f"{ENV}/flows/notify", json={**FLOW, "description": "draft"}, params={"branch": "feature-a"}
    )
    await api.studio.delete(f"{ENV}/branches/feature-a")
    assert await _names(api) == ["main"]
    assert (await api.studio.get(f"{ENV}/flows/notify"))["description"] == "live"
    with pytest.raises(ServiceError) as gone:
        await api.studio.get(f"{ENV}/flows/notify", params={"branch": "feature-a"})
    assert gone.value.status == 404


async def test_main_cannot_be_deleted_and_a_missing_branch_is_a_404(api):
    with pytest.raises(ServiceError) as refused:
        await api.studio.delete(f"{ENV}/branches/main")
    assert refused.value.status == 400
    with pytest.raises(ServiceError) as missing:
        await api.studio.delete(f"{ENV}/branches/nope")
    assert missing.value.status == 404


async def test_a_merged_branch_records_that_it_was_merged(api):
    await api.studio.post(f"{ENV}/flows", json=FLOW)
    await api.studio.post(f"{ENV}/branches", json={"name": "feature-a"})
    await api.studio.put(
        f"{ENV}/flows/notify", json={**FLOW, "description": "draft"}, params={"branch": "feature-a"}
    )
    await api.studio.post(f"{ENV}/branches/feature-a/merge", json={"target": "main"})
    branch = next(
        b for b in (await api.studio.get(f"{ENV}/branches"))["data"] if b["name"] == "feature-a"
    )
    assert branch["changes"][-1]["action"] == "merged"
    assert (await api.studio.get(f"{ENV}/flows/notify"))["description"] == "draft"
