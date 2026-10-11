"""What keeps a busy or hostile client from hurting others, over real sockets."""

import json

from tests.realtime.helpers import connect, context_query, subscribe, token


def drain_errors(ws, count):
    return [ws.receive_json() for _ in range(count)]


def test_invalid_channel_and_event_names_are_refused(make_app):
    client, settings, _, _ = make_app()
    ws = connect(client, settings, token(settings))
    for channel in ("a/b", "has space", "*", "x" * 201):
        ws.send_json({"type": "subscribe", "channel": channel, "ref": "1"})
        ack = ws.receive_json()
        assert ack["ok"] is False and ack["error"] == "forbidden"
    subscribe(ws, "public:ok")
    ws.send_json(
        {"type": "publish", "channel": "public:ok", "event": "bad event!", "payload": 1, "ref": "2"}
    )
    assert ws.receive_json()["error"] == "forbidden"
    ws.close()


def test_a_payload_over_the_limit_is_refused_but_the_connection_lives(make_app):
    client, settings, _, realtime = make_app(max_payload_bytes=100)
    ws = connect(client, settings, token(settings))
    subscribe(ws, "public:big")
    ws.send_json(
        {"type": "publish", "channel": "public:big", "payload": {"x": "y" * 500}, "ref": "1"}
    )
    refusal = ws.receive_json()
    assert (
        refusal["ok"] is False
        and refusal["error"] == "payload_too_large"
        and refusal["limit"] == 100
    )
    ws.send_json({"type": "publish", "channel": "public:big", "payload": {"x": 1}, "ref": "2"})
    assert ws.receive_json()["ok"] is True
    assert realtime.stats.counts["oversized"] == 1
    ws.close()


def test_a_frame_over_the_limit_is_an_error_not_a_disconnect(make_app):
    client, settings, _, _ = make_app(max_message_bytes=200)
    ws = connect(client, settings, token(settings))
    ws.send_text(json.dumps({"type": "ping", "pad": "z" * 400}))
    assert ws.receive_json() == {"type": "error", "error": "message_too_large"}
    ws.send_json({"type": "ping"})
    assert ws.receive_json() == {"type": "pong"}
    ws.close()


def test_malformed_frames_get_named_errors(make_app):
    client, settings, _, _ = make_app()
    ws = connect(client, settings, token(settings))
    for raw, error in (
        ("nope", "invalid_json"),
        ("[]", "invalid_message"),
        ('{"type": 5}', "invalid_message"),
    ):
        ws.send_text(raw)
        assert ws.receive_json() == {"type": "error", "error": error}
    ws.send_json({"type": "teleport", "ref": "9"})
    assert ws.receive_json() == {"type": "ack", "ref": "9", "ok": False, "error": "unknown_type"}
    ws.close()


def test_a_flooding_client_is_limited_then_closed(make_app):
    client, settings, _, realtime = make_app(rate_per_second=1, rate_burst=3, abuse_strikes=3)
    ws = connect(client, settings, token(settings))
    for _ in range(3):
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
    ws.send_json({"type": "ping", "ref": "x"})
    assert ws.receive_json()["error"] == "rate_limited"
    assert realtime.stats.counts["rate_limited"] >= 1
    # Keep flooding until the strike limit closes the socket.
    closed = False
    try:
        for _ in range(10):
            ws.send_json({"type": "ping"})
            ws.receive_json()
    except Exception:
        closed = True
    assert closed and realtime.stats.counts["closed_for_abuse"] == 1


def test_a_person_may_hold_only_so_many_connections(make_app):
    client, settings, _, realtime = make_app(max_connections_per_identity=2)
    mine = token(settings, "7")
    first, second = connect(client, settings, mine), connect(client, settings, mine)
    refused = client.websocket_connect(
        "/realtime/v1/socket?token=" + mine, headers=context_query(settings)
    )
    try:
        refused.__enter__()
        refused.receive_json()
        opened = True
    except Exception:
        opened = False
    assert not opened
    other = connect(client, settings, token(settings, "8"))  # someone else is unaffected
    first.close()
    assert len(realtime.connections) >= 2
    for ws in (second, other):
        ws.close()


def test_an_environment_connection_ceiling_applies(make_app):
    client, settings, _, _ = make_app(max_connections_per_env=1)
    first = connect(client, settings)
    try:
        second = client.websocket_connect("/realtime/v1/socket", headers=context_query(settings))
        second.__enter__()
        second.receive_json()
        opened = True
    except Exception:
        opened = False
    assert not opened
    first.close()


def test_presence_meta_has_a_size_limit(make_app):
    client, settings, _, _ = make_app(max_presence_bytes=50)
    ws = connect(client, settings, token(settings))
    ws.send_json(
        {"type": "subscribe", "channel": "public:p", "ref": "1", "presence": {"bio": "x" * 400}}
    )
    ack = ws.receive_json()
    assert ack["ok"] is False and ack["error"] == "payload_too_large"
    ws.close()
