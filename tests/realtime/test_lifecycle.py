"""Things that change while a connection is open: tokens run out, rules change, instances drain, publications reach flows."""

import time

from tests.realtime.helpers import connect, context_query, subscribe, token


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def only_connection(realtime):
    assert wait_for(lambda: len(realtime.connections) == 1)
    return next(iter(realtime.connections.values()))


def test_an_expired_token_demotes_the_connection_and_closes_what_only_the_person_could_use(
    make_app,
):
    client, settings, _, realtime = make_app(sweep_interval=0.05)
    ws = connect(client, settings, token(settings, "7"))
    assert subscribe(ws, "user:7")["ok"]
    connection = only_connection(realtime)
    connection.token_exp = time.time() - 1
    assert ws.receive_json() == {"type": "token_expired"}
    closed = ws.receive_json()
    assert closed["type"] == "channel_closed" and closed["channel"] == "user:7"
    assert connection.user_id is None and "user:7" not in connection.channels
    ws.close()


def test_a_token_about_to_expire_warns_the_client_once(make_app):
    client, settings, _, realtime = make_app(sweep_interval=0.05)
    ws = connect(client, settings, token(settings, "7"))
    only_connection(realtime).token_exp = time.time() + 10
    warning = ws.receive_json()
    assert warning["type"] == "token_expiring" and 1 <= warning["expires_in"] <= 10
    ws.send_json({"type": "ping"})
    assert ws.receive_json() == {"type": "pong"}  # no second warning queued ahead of the reply
    ws.close()


def test_re_authenticating_with_a_fresh_token_restores_access(make_app):
    client, settings, _, realtime = make_app(sweep_interval=0.05)
    ws = connect(client, settings, token(settings, "7"))
    only_connection(realtime).token_exp = time.time() - 1
    assert ws.receive_json() == {"type": "token_expired"}
    ws.send_json({"type": "auth", "token": token(settings, "7"), "ref": "a"})
    assert ws.receive_json() == {"type": "ack", "ref": "a", "ok": True, "user_id": "7"}
    assert subscribe(ws, "user:7")["ok"]
    ws.send_json({"type": "auth", "token": "garbage", "ref": "b"})
    assert ws.receive_json()["error"] == "invalid_token"
    ws.close()


def test_a_rule_change_closes_subscriptions_that_are_no_longer_allowed(make_app):
    client, settings, fake, realtime = make_app(sweep_interval=0.05, config_ttl=0)
    ws = connect(client, settings, token(settings, "1", roles=["admin"]))
    assert subscribe(ws, "admin:ops")["ok"]
    time.sleep(0.2)  # a pass records the version in force
    fake.realtime["version"] = 2
    fake.realtime["channels"][2] = {
        "pattern": "admin:*",
        "subscribe": "service",
        "publish": "service",
    }
    closed = ws.receive_json()
    assert closed["type"] == "channel_closed" and closed["channel"] == "admin:ops"
    assert realtime.stats.counts["channels_revoked"] == 1
    ws.close()


def test_a_draining_instance_refuses_new_connections(make_app):
    client, settings, _, realtime = make_app()
    realtime._draining = True
    try:
        ws = client.websocket_connect("/realtime/v1/socket", headers=context_query(settings))
        ws.__enter__()
        ws.receive_json()
        opened = True
    except Exception:
        opened = False
    assert not opened


def test_client_publications_reach_flows_without_the_publisher_waiting(make_app):
    client, settings, fake, _ = make_app()
    ws = connect(client, settings, token(settings, "3"))
    subscribe(ws, "public:news")
    ws.send_json(
        {
            "type": "publish",
            "channel": "public:news",
            "event": "post",
            "payload": {"t": 1},
            "ref": "1",
        }
    )
    assert ws.receive_json()["ok"] is True
    assert wait_for(lambda: any(e.get("name") == "realtime.message" for e in fake.events))
    sent = next(e for e in fake.events if e.get("name") == "realtime.message")
    assert sent["payload"]["channel"] == "public:news" and sent["payload"]["user_id"] == "3"
    ws.close()


def test_the_flow_queue_drops_the_newest_when_full_and_counts_it():
    from pawabase_server.realtime.flows import FlowForwarder, Forwarded
    from pawabase_server.realtime.stats import Stats
    from tests.realtime.helpers import FakeApi

    stats = Stats()
    forwarder = FlowForwarder(FakeApi().emit_event, stats, size=1)
    item = Forwarded("dev", "c", "e", {}, None)
    assert forwarder.submit(item) is True
    assert forwarder.submit(item) is False
    assert stats.counts["forward_dropped"] == 1 and forwarder.pending == 1


def test_a_connection_that_ends_twice_frees_its_quota_once(make_app):
    client, settings, _, realtime = make_app(max_connections_per_identity=1)
    ws = connect(client, settings, token(settings, "5"))
    connection = only_connection(realtime)
    ws.close()
    assert wait_for(lambda: not realtime.connections)
    assert realtime._identity_quota.held("dev:5") == 0
    # With the one slot freed, the same person can connect again.
    again = connect(client, settings, token(settings, "5"))
    assert connection.id != next(iter(realtime.connections.values())).id
    again.close()
