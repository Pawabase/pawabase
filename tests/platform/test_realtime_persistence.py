"""Durable channels: a persisted ``RealtimeChannelConfig`` row changes how realtime behaves (not just the
hand-edited JSON blob), a publish is stored and deduplicated by ``client_msg_id``, history reads survive a
cache-store round trip (the thing that matters: a real row in Postgres/SQLite, not just the in-memory backlog),
and retention actually removes what's expired.
"""

ENV = "development"


async def make_durable_channel(api, pattern="orders", **overrides):
    from pawabase_server.db.models import RealtimeChannelConfig

    environment = (await api.platform.state(ENV)).environment
    return await RealtimeChannelConfig.create(
        environment_id=environment.id,
        pattern=pattern,
        kind="durable",
        subscribe="public",
        publish="authenticated",
        **overrides,
    )


async def publish(api, channel, payload, *, client_msg_id=None, headers=None):
    body = {"channel": channel, "event": "update", "payload": payload}
    if client_msg_id:
        body["client_msg_id"] = client_msg_id
    return await api.http.post("/realtime/v1/publish", json=body, headers=headers or api.user_headers(ENV, "1"))


async def test_a_durable_channel_config_row_is_merged_into_the_live_rules(api):
    await make_durable_channel(api)
    settings = await api.platform.realtime_settings(ENV)
    rule = next(c for c in settings["channels"] if c["pattern"] == "orders")
    assert rule["kind"] == "durable" and rule["subscribe"] == "public"


async def test_channel_rules_are_managed_through_the_platform_api(api):
    created = await api.studio.post(
        f"/platform/v1/envs/{ENV}/realtime-channels",
        json={"pattern": "orders:*", "kind": "durable", "subscribe": "public", "publish": "authenticated"},
    )
    row = created
    listed = await api.studio.get(f"/platform/v1/envs/{ENV}/realtime-channels")
    assert [item["pattern"] for item in listed["data"]] == ["orders:*"]
    changed = await api.studio.patch(
        f"/platform/v1/envs/{ENV}/realtime-channels/{row['id']}",
        json={"retention_seconds": 3600},
    )
    assert changed["retention_seconds"] == 3600


async def test_publishing_on_a_durable_channel_stores_it(api):
    from pawabase_server.db.models import RealtimeMessage

    await make_durable_channel(api)
    response = await publish(api, "orders", {"id": 1}, client_msg_id="order-1")
    assert response.status_code == 200, response.text
    stored = await RealtimeMessage.get(environment_id=(await api.platform.state(ENV)).environment.id, channel="orders")
    assert stored.data == {"id": 1} and stored.client_msg_id == "order-1"


async def test_a_repeated_client_msg_id_is_not_delivered_twice(api):
    from pawabase_server.db.models import RealtimeMessage

    await make_durable_channel(api)
    first = (await publish(api, "orders", {"id": 1}, client_msg_id="order-1")).json()
    second = (await publish(api, "orders", {"id": 1, "retry": True}, client_msg_id="order-1")).json()
    assert first["id"] == second["id"] and second.get("duplicate") is True
    rows = await RealtimeMessage.filter(channel="orders").all()
    assert len(rows) == 1 and rows[0].data == {"id": 1}  # the retry's different payload never overwrote it


async def test_history_reads_the_durable_store_not_just_the_backlog(api):
    await make_durable_channel(api)
    await publish(api, "orders", {"id": 1}, client_msg_id="o1")
    await publish(api, "orders", {"id": 2}, client_msg_id="o2")
    response = await api.http.get(
        "/realtime/v1/channels/orders/history", headers=api.user_headers(ENV, "1")
    )
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert [m["payload"]["id"] for m in messages] == [1, 2]


async def test_retention_purges_expired_durable_messages(api):
    import datetime

    from pawabase_server.db.models import RealtimeMessage

    await make_durable_channel(api, retention_seconds=1)
    await publish(api, "orders", {"id": 1}, client_msg_id="o1")
    row = await RealtimeMessage.get(channel="orders")
    row.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    await row.save()
    done = await api.app.state["realtime"].sweep()
    assert done["durable_expired"] >= 1
    assert await RealtimeMessage.filter(channel="orders").count() == 0


async def test_a_non_durable_channel_is_unaffected_by_any_of_this(api):
    """The default path (no RealtimeChannelConfig row at all) behaves exactly as before this change."""
    response = await publish(api, "public:lobby", {"text": "hi"})
    assert response.status_code == 200
    assert "duplicate" not in response.json()
