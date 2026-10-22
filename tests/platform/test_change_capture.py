"""Change capture: writes made outside the API are announced, exactly once, and never when rolled back."""

from datetime import UTC, datetime, timedelta

import pytest

from pawabase_server.core.clients import ServiceError
from pawabase_server.data import cdc

ENV = "/platform/v1/envs/development"

ORDERS = {
    "name": "orders",
    "fields": [
        {"name": "customer", "type": "string"},
        {"name": "paid", "type": "boolean", "default": False},
        {"name": "meta", "type": "json"},
    ],
    "operations": {
        op: {"enabled": True, "policy": "public"}
        for op in ("list", "get", "create", "update", "delete")
    },
    "events": True,
    "realtime": True,
    "capture": True,
}


@pytest.fixture
async def orders(api, monkeypatch):
    await api.studio.post(f"{ENV}/resources", json=ORDERS)
    await api.studio.post(f"{ENV}/resources/orders/migrate")
    api.sent = {"events": [], "realtime": []}

    async def emit(state, name, payload, **kwargs):
        api.sent["events"].append((name, payload))
        return "evt"

    async def publish(state, channel, event, payload):
        api.sent["realtime"].append((channel, event, payload))
        return {}

    monkeypatch.setattr(api.platform, "emit", emit)
    monkeypatch.setattr(api.platform, "publish", publish)
    api.source = await (await api.platform.state("development")).source()
    api.capture = cdc.ChangeCapture(api.platform)
    return api


async def insert(api, id_, customer="Ada", paid=0, meta='{"tier": "gold"}'):
    await api.source.execute(
        'INSERT INTO "orders" (id, customer, paid, meta, created_at, updated_at) '
        "VALUES (?, ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        [id_, customer, paid, meta],
    )


async def test_enabling_capture_lays_triggers_down(orders):
    assert await cdc.is_installed(orders.source, "orders")


async def test_a_write_outside_the_api_is_announced(orders):
    await insert(orders, "01HAAAAAAAAAAAAAAAAAAAAAAA")
    assert await orders.capture.tick() == 1
    ((name, payload),) = orders.sent["events"]
    assert name == "orders.created"
    assert payload["record"]["customer"] == "Ada"
    assert payload["source"] == "database"
    ((channel, event, message),) = orders.sent["realtime"]
    assert (channel, event) == ("resource:orders", "created")
    # Columns come back as their types, not as the database stored them.
    assert message["record"]["meta"] == {"tier": "gold"}
    assert message["record"]["paid"] is False


async def test_updates_say_what_changed_and_deletes_carry_the_old_row(orders):
    await insert(orders, "01HAAAAAAAAAAAAAAAAAAAAAAA")
    await orders.capture.tick()
    orders.sent["events"].clear()
    await orders.source.execute(
        "UPDATE \"orders\" SET customer = 'Grace', paid = 1 WHERE id = '01HAAAAAAAAAAAAAAAAAAAAAAA'"
    )
    await orders.capture.tick()
    ((name, payload),) = orders.sent["events"]
    assert name == "orders.updated"
    assert sorted(payload["changed"]) == ["customer", "paid"]
    assert payload["record"]["paid"] is True

    orders.sent["events"].clear()
    await orders.source.execute("DELETE FROM \"orders\" WHERE id = '01HAAAAAAAAAAAAAAAAAAAAAAA'")
    await orders.capture.tick()
    ((name, payload),) = orders.sent["events"]
    assert name == "orders.deleted" and payload["record"]["customer"] == "Grace"


async def test_a_change_is_delivered_once(orders):
    await insert(orders, "01HAAAAAAAAAAAAAAAAAAAAAAA")
    assert await orders.capture.tick() == 1
    assert await orders.capture.tick() == 0
    assert len(orders.sent["events"]) == 1


async def test_an_api_write_is_announced_by_capture_not_twice(orders):
    anon = orders.context_headers("development")
    response = await orders.http.post("/rest/v1/orders", json={"customer": "Lin"}, headers=anon)
    assert response.status_code == 201
    assert orders.sent["events"] == []  # the API leaves it to capture
    await orders.capture.tick()
    assert [name for name, _ in orders.sent["events"]] == ["orders.created"]
    assert len(orders.sent["realtime"]) == 1


async def test_a_rolled_back_change_is_never_announced(orders):
    with pytest.raises(RuntimeError):
        async with orders.source.transaction() as tx:
            await tx.execute_query(
                'INSERT INTO "orders" (id, customer, paid, created_at, updated_at) '
                "VALUES ('01HBBBBBBBBBBBBBBBBBBBBBBB', 'Ghost', 0, 'x', 'x')"
            )
            raise RuntimeError("abort")
    assert await orders.capture.tick() == 0
    assert orders.sent["events"] == []


async def test_turning_capture_off_removes_the_triggers(orders):
    off = {**ORDERS, "capture": False}
    await orders.studio.put(f"{ENV}/resources/orders", json=off)
    assert not await cdc.is_installed(orders.source, "orders")
    before = await cdc.pending(orders.source)
    await insert(orders, "01HCCCCCCCCCCCCCCCCCCCCCCC")
    assert await cdc.pending(orders.source) == before


async def test_deleting_the_resource_removes_the_triggers(orders):
    await orders.studio.delete(f"{ENV}/resources/orders")
    rows = await orders.source.fetch("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    assert rows == []


async def test_a_change_not_finished_is_taken_again_then_given_up_on(orders):
    await insert(orders, "01HAAAAAAAAAAAAAAAAAAAAAAA")
    first = await cdc.claim(orders.source)
    assert len(first) == 1 and first[0].attempts == 1
    assert await cdc.claim(orders.source) == []  # held by the first claim
    old = (datetime.now(UTC) - timedelta(seconds=cdc.CLAIM_SECONDS + 5)).isoformat()
    for attempt in range(2, cdc.MAX_ATTEMPTS + 1):
        await orders.source.execute('UPDATE "pb_changes" SET claimed_at = ?', [old])
        again = await cdc.claim(orders.source)
        assert len(again) == 1 and again[0].attempts == attempt
    await orders.source.execute('UPDATE "pb_changes" SET claimed_at = ?', [old])
    assert await cdc.claim(orders.source) == []  # given up on


async def test_delivered_changes_are_pruned_after_a_while(orders):
    await insert(orders, "01HAAAAAAAAAAAAAAAAAAAAAAA")
    await orders.capture.tick()
    assert await cdc.prune(orders.source) == 0
    long_ago = (datetime.now(UTC) - cdc.RETENTION - timedelta(minutes=1)).isoformat()
    await orders.source.execute('UPDATE "pb_changes" SET done_at = ?', [long_ago])
    assert await cdc.prune(orders.source) == 1


async def test_the_outbox_cannot_be_dropped_from_studio(orders):
    with pytest.raises(ServiceError) as caught:
        await orders.studio.delete(f"{ENV}/database/tables/pb_changes")
    assert caught.value.status == 403
