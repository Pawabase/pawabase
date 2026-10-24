"""The gateway instance registry: an instance registers and heartbeats itself, Studio reads the fleet, draining is visible fast."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from pawabase_server.db.models import GatewayInstance
from pawabase_server.gateway.registry import GatewayRegistry, default_instance_id

ENV = "/platform/v1/envs/development"


def test_a_default_instance_id_is_distinct_each_time():
    assert default_instance_id() != default_instance_id()


async def test_an_instance_registers_itself_and_heartbeats(api):
    registry = GatewayRegistry(
        api.settings.model_copy(update={"gateway_instance_id": "g1", "gateway_plane_id": "plane-a"})
    )
    await registry.register()
    row = await GatewayInstance.get(id="g1")
    assert row.plane_id == "plane-a" and row.draining is False
    first = row.heartbeat_at
    await asyncio.sleep(0.01)
    await registry.heartbeat()
    await row.refresh_from_db()
    assert row.heartbeat_at > first


async def test_registering_again_under_the_same_id_updates_the_row_not_a_duplicate(api):
    registry = GatewayRegistry(api.settings.model_copy(update={"gateway_instance_id": "g2"}))
    await registry.register()
    registry.version = "v2"
    await registry.register()
    assert await GatewayInstance.filter(id="g2").count() == 1
    assert (await GatewayInstance.get(id="g2")).version == "v2"


async def test_draining_is_visible_immediately_and_this_instances_readiness_reflects_it(api):
    registry = GatewayRegistry(api.settings.model_copy(update={"gateway_instance_id": "g3"}))
    await registry.register()
    assert await registry.is_ready() is True
    await registry.drain()
    assert await registry.is_ready() is False
    assert (await GatewayInstance.get(id="g3")).draining is True


async def test_is_healthy_excludes_draining_and_stale_rows(api):
    await GatewayInstance.create(id="fresh", host="a:8080", internal_url="http://a:8001")
    stale = await GatewayInstance.create(id="stale", host="b:8080", internal_url="http://b:8001")
    await GatewayInstance.filter(id="stale").update(
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=10)
    )
    await stale.refresh_from_db()
    draining = await GatewayInstance.create(
        id="draining-one", host="c:8080", internal_url="http://c:8001", draining=True
    )
    fresh = await GatewayInstance.get(id="fresh")
    assert fresh.is_healthy(stale_after=30) is True
    assert stale.is_healthy(stale_after=30) is False
    assert draining.is_healthy(stale_after=30) is False


async def test_stop_marks_draining_and_cancels_the_heartbeat_loop(api):
    registry = GatewayRegistry(
        api.settings.model_copy(
            update={"gateway_instance_id": "g4", "gateway_heartbeat_seconds": 0.01}
        )
    )
    registry.start()
    await asyncio.sleep(0.05)
    await registry.stop()
    row = await GatewayInstance.get(id="g4")
    assert row.draining is True
    assert registry._task is None


async def test_prune_stale_removes_only_rows_older_than_the_window(api):
    await GatewayInstance.create(id="recent", host="a", internal_url="http://a")
    await GatewayInstance.create(id="old", host="b", internal_url="http://b")
    await GatewayInstance.filter(id="old").update(
        heartbeat_at=datetime.now(UTC) - timedelta(hours=2)
    )
    removed = await GatewayRegistry(api.settings).prune_stale(older_than_seconds=3600)
    assert removed == 1
    assert await GatewayInstance.filter(id="recent").exists()
    assert not await GatewayInstance.filter(id="old").exists()


# ── through the API ──────────────────────────────────────────────────────────


async def test_health_ready_reflects_this_process_without_touching_the_database(api):
    got = await api.http.get("/health/ready")
    assert got.status_code == 200 and got.json()["status"] == "ready"
    await api.platform.app.state["gateway_registry"].drain()
    draining = await api.http.get("/health/ready")
    assert draining.json()["status"] == "draining"


async def test_health_ready_is_reachable_with_no_api_key(api):
    # /health/ready must work for a load balancer that never sends an apikey header.
    got = await api.http.get("/health/ready", headers={})
    assert got.status_code == 200


async def test_studio_lists_every_registered_instance_and_whether_each_is_healthy(api):
    registry = api.platform.app.state["gateway_registry"]
    await registry.register()
    await GatewayInstance.create(
        id="sibling", host="sibling:8080", internal_url="http://sibling:8001", plane_id="plane-b"
    )
    listing = await api.studio.get("/internal/v1/gateway/instances")
    ids = {row["id"]: row for row in listing["data"]}
    assert registry.id in ids and "sibling" in ids
    assert ids["sibling"]["plane_id"] == "plane-b" and ids["sibling"]["healthy"] is True
    assert ids[registry.id]["healthy"] is True


async def test_studio_can_drain_any_instance_by_id(api):
    await GatewayInstance.create(id="far-away", host="far:8080", internal_url="http://far:8001")
    result = await api.studio.post("/internal/v1/gateway/drain", json={"id": "far-away"})
    assert result == {"drained": "far-away"}
    assert (await GatewayInstance.get(id="far-away")).draining is True
    from pawabase_server.core.clients import ServiceError

    with pytest.raises(ServiceError) as missing:
        await api.studio.post("/internal/v1/gateway/drain", json={"id": "does-not-exist"})
    assert missing.value.status == 404


async def test_studio_can_remove_a_confirmed_gone_instance(api):
    await GatewayInstance.create(id="gone", host="gone:8080", internal_url="http://gone:8001")
    result = await api.studio.delete("/internal/v1/gateway/instances/gone")
    assert result == {"removed": True}
    assert not await GatewayInstance.filter(id="gone").exists()
    again = await api.studio.delete("/internal/v1/gateway/instances/gone")
    assert again == {"removed": False}


async def test_a_second_boot_role_never_registers_a_gateway_instance(settings):
    from pawabase_server.app import create_app

    worker_app = create_app(settings, role="worker")
    assert worker_app.state["gateway_registry"] is None
