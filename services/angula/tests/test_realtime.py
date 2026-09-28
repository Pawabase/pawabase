"""Angula over real WebSocket sessions (Sillo's test client)."""

import os
from typing import Any

import pytest

os.environ.setdefault("SILLO_ENV_FILE", "")


class FakeApi:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.realtime = {
            "version": 1,
            "channels": [
                {
                    "pattern": "public:*",
                    "subscribe": "public",
                    "publish": "authenticated",
                    "presence": True,
                },
                {
                    "pattern": "user:{{ auth.user_id }}",
                    "subscribe": "authenticated",
                    "publish": "service",
                },
                {"pattern": "admin:*", "subscribe": "role:admin", "publish": "role:admin"},
            ],
            "policies": {},
            "default_policy": "authenticated",
        }

    async def get(self, path: str, **kwargs: Any) -> Any:
        return self.realtime

    async def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        self.events.append(json)
        return {}

    async def close(self) -> None:
        pass


@pytest.fixture
def realtime_app():
    from sillo.testclient import TestClient

    from app.bootstrap import create_app
    from app.config import AngulaSettings
    from app.realtime import Realtime

    settings = AngulaSettings(_env_file=None, app_env="testing")
    fake = FakeApi()
    realtime = Realtime(settings, api=fake)
    app = create_app(settings, realtime=realtime)
    with TestClient(app) as client:
        yield client, settings, fake, realtime


def context_query(settings, project="acme", env="dev", role="anon") -> dict[str, str]:
    from pawabase_kit.context import CONTEXT_HEADER, PlatformContext
    from pawabase_kit.tokens import issue_context_token

    return {
        CONTEXT_HEADER: issue_context_token(
            settings.internal_secret, PlatformContext(project=project, env=env, role=role)
        )
    }


def token(settings, user_id="1", roles=(), project="acme", env="dev") -> str:
    from pawabase_kit.tokens import issue_user_token

    return issue_user_token(
        settings.jwt_master_secret,
        project=project,
        env=env,
        user_id=user_id,
        jti="j",
        session_id="s",
        roles=list(roles),
    )


def connect(client, settings, user=None, **kwargs):
    url = "/realtime/v1/socket" + (f"?token={user}" if user else "")
    ws = client.websocket_connect(url, headers=context_query(settings, **kwargs))
    ws.__enter__()
    welcome = ws.receive_json()
    assert welcome["type"] == "welcome"
    return ws


def subscribe(ws, channel):
    """Subscribe and consume the ack, and the presence state that follows success."""
    ws.send_json({"type": "subscribe", "channel": channel, "ref": channel})
    ack = ws.receive_json()
    if ack["ok"]:
        assert ws.receive_json()["type"] == "presence_state"
    return ack


def test_pubsub_presence_and_history(realtime_app):
    client, settings, fake, realtime = realtime_app
    ada = connect(client, settings, token(settings, "1"))
    bob = connect(client, settings)

    ada.send_json(
        {"type": "subscribe", "channel": "public:lobby", "ref": "1", "presence": {"name": "Ada"}}
    )
    assert ada.receive_json() == {"type": "ack", "ref": "1", "ok": True, "channel": "public:lobby"}
    assert ada.receive_json() == {
        "type": "presence_state",
        "channel": "public:lobby",
        "members": [],
    }
    joined = ada.receive_json()
    assert joined["type"] == "presence_diff" and joined["joins"][0]["meta"] == {"name": "Ada"}

    bob.send_json({"type": "subscribe", "channel": "public:lobby", "ref": "b1"})
    assert bob.receive_json()["ok"] is True
    state = bob.receive_json()
    assert [m["user_id"] for m in state["members"]] == ["1"]

    # Anonymous Bob may read the lobby but not write to it.
    bob.send_json(
        {"type": "publish", "channel": "public:lobby", "payload": {"text": "hi"}, "ref": "b2"}
    )
    refused = bob.receive_json()
    assert refused["ok"] is False and refused["error"] == "forbidden"

    ada.send_json(
        {
            "type": "publish",
            "channel": "public:lobby",
            "event": "chat",
            "payload": {"text": "hello"},
            "ref": "2",
        }
    )
    received = [ada.receive_json() for _ in range(2)]
    message = next(m for m in received if m["type"] == "message")
    assert (
        message["payload"] == {"text": "hello"}
        and message["from"] == "1"
        and message["event"] == "chat"
    )
    at_bob = bob.receive_json()
    assert at_bob["type"] == "message" and at_bob["payload"] == {"text": "hello"}
    assert (
        fake.events[-1]["name"] == "realtime.message"
        and fake.events[-1]["payload"]["channel"] == "public:lobby"
    )

    bob.send_json({"type": "history", "channel": "public:lobby", "ref": "h"})
    history = bob.receive_json()
    assert [m["payload"] for m in history["messages"]] == [{"text": "hello"}]

    ada.close()
    left = bob.receive_json()
    assert left["type"] == "presence_diff" and left["leaves"][0]["user_id"] == "1"
    bob.close()


def test_channel_authorization(realtime_app):
    client, settings, fake, realtime = realtime_app
    ada = connect(client, settings, token(settings, "1"))
    assert subscribe(ada, "user:1")["ok"] is True
    assert subscribe(ada, "user:2")["ok"] is False  # someone else's private channel
    assert subscribe(ada, "admin:ops")["ok"] is False
    ada.close()

    root = connect(client, settings, token(settings, "9", roles=["admin"]))
    assert subscribe(root, "admin:ops")["ok"] is True
    root.close()

    # A token for another environment is refused at the handshake.
    wrong = client.websocket_connect(
        "/realtime/v1/socket?token=" + token(settings, env="prod"), headers=context_query(settings)
    )
    from sillo.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), wrong:
        wrong.receive_json()


def test_server_publish_reaches_private_channel(realtime_app):
    client, settings, fake, realtime = realtime_app
    from pawabase_kit.auth import SERVICE_HEADER
    from pawabase_kit.tokens import issue_service_token

    ada = connect(client, settings, token(settings, "1"))
    subscribe(ada, "user:1")
    headers = {
        **context_query(settings, role="service"),
        SERVICE_HEADER: issue_service_token(
            settings.internal_secret, issuer="api", audience="angula"
        ),
    }
    published = client.post(
        "/internal/v1/publish",
        json={"channel": "user:1", "event": "notification", "payload": {"n": 1}},
        headers=headers,
    )
    assert published.status_code == 200
    message = ada.receive_json()
    assert message["event"] == "notification" and message["payload"] == {"n": 1}

    # Clients cannot publish to it: the rule says service only.
    denied = client.post(
        "/realtime/v1/publish",
        json={"channel": "user:1", "payload": {}},
        headers={**context_query(settings), "Authorization": f"Bearer {token(settings, '1')}"},
    )
    assert denied.status_code == 403

    stats = client.get(
        "/internal/v1/realtime/summary",
        headers={
            SERVICE_HEADER: issue_service_token(
                settings.internal_secret, issuer="studio", audience="angula"
            )
        },
    ).json()
    assert stats["connections"] == 1 and stats["published"] >= 1
    channels = client.get(
        "/internal/v1/realtime/acme/dev/channels",
        headers={
            SERVICE_HEADER: issue_service_token(
                settings.internal_secret, issuer="studio", audience="angula"
            )
        },
    ).json()
    assert channels["data"] == [
        {"project": "acme", "env": "dev", "channel": "user:1", "subscribers": 1, "presence": 0}
    ]
    ada.close()


def test_environments_are_isolated(realtime_app):
    client, settings, fake, realtime = realtime_app
    dev = connect(client, settings, token(settings, "1"))
    prod = connect(client, settings, token(settings, "1", env="prod"), env="prod")
    for ws in (dev, prod):
        ws.send_json({"type": "subscribe", "channel": "public:lobby", "ref": "1"})
        ws.receive_json()
        ws.receive_json()
    dev.send_json(
        {"type": "publish", "channel": "public:lobby", "payload": {"only": "dev"}, "ref": "p"}
    )
    messages = [dev.receive_json() for _ in range(2)]
    assert any(m.get("payload") == {"only": "dev"} for m in messages)
    prod.send_json({"type": "ping"})
    assert prod.receive_json() == {"type": "pong"}  # nothing from dev arrived first
    dev.close()
    prod.close()


def test_build_config_treats_a_stored_null_as_not_set():
    """Studio's "Default" option (and a hand-edited settings JSON) can store an
    explicit null for subscribe/publish/default_policy. That must fall back to
    "authenticated" exactly like an absent key does, never carry a null policy
    into the engine."""
    from app.realtime import Realtime

    config = Realtime._build_config(
        {
            "version": 1,
            "default_policy": None,
            "channels": [
                {"pattern": "chat:*", "subscribe": None, "publish": None, "presence": True},
                {"pattern": "vip:*", "subscribe": "role:vip"},  # publish absent entirely
            ],
            "policies": {},
        }
    )
    assert config.default_policy == "authenticated"
    chat = next(r for r in config.rules if r.pattern == "chat:*")
    assert chat.subscribe == "authenticated" and chat.publish == "authenticated"
    vip = next(r for r in config.rules if r.pattern == "vip:*")
    assert vip.subscribe == "role:vip" and vip.publish == "authenticated"
