"""Saved SQL queries are shared by the environment's managers, unique by name, and survive being edited."""

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"


async def test_a_query_is_saved_listed_edited_and_deleted(api):
    saved = await api.studio.post(
        f"{ENV}/database/queries", json={"name": "Recent orders", "sql": "SELECT 1"}
    )
    assert saved["name"] == "Recent orders"
    listed = (await api.studio.get(f"{ENV}/database/queries"))["data"]
    assert [q["name"] for q in listed] == ["Recent orders"]
    changed = await api.studio.put(
        f"{ENV}/database/queries/{saved['id']}",
        json={"name": "Recent orders", "description": "Last 50", "sql": "SELECT 2"},
    )
    assert changed["sql"] == "SELECT 2" and changed["description"] == "Last 50"
    await api.studio.delete(f"{ENV}/database/queries/{saved['id']}")
    assert (await api.studio.get(f"{ENV}/database/queries"))["data"] == []


async def test_names_are_unique_per_environment(api):
    await api.studio.post(f"{ENV}/database/queries", json={"name": "q", "sql": "SELECT 1"})
    with pytest.raises(ServiceError) as caught:
        await api.studio.post(f"{ENV}/database/queries", json={"name": "q", "sql": "SELECT 2"})
    assert caught.value.status == 409
    other = await api.studio.post(f"{ENV}/database/queries", json={"name": "r", "sql": "SELECT 3"})
    with pytest.raises(ServiceError) as caught:
        await api.studio.put(
            f"{ENV}/database/queries/{other['id']}", json={"name": "q", "sql": "SELECT 3"}
        )
    assert caught.value.status == 409


async def test_a_missing_query_is_a_404(api):
    with pytest.raises(ServiceError) as caught:
        await api.studio.delete(f"{ENV}/database/queries/01HZZZZZZZZZZZZZZZZZZZZZZZ")
    assert caught.value.status == 404
