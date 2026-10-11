"""Shared fakes and helpers for Angula's tests."""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("SILLO_ENV_FILE", "")


class Unavailable(RuntimeError):
    """The platform could not answer."""


class FakePlatform:
    """Stands in for the platform: serves one set of realtime rules and records the events it is sent."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.calls = 0
        self.fail = False
        self.missing = False
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

    async def realtime_settings(self, env: str) -> Any:
        from sillo.exceptions import HTTPException

        self.calls += 1
        if self.missing:
            raise HTTPException(status_code=404, detail=f"no environment {env!r}")
        if self.fail:
            raise Unavailable("unavailable")
        return self.realtime

    async def emit_event(self, env: str, name: str, payload: Any, *, actor: Any = None) -> str:
        self.events.append({"env": env, "name": name, "payload": payload, "actor": actor})
        return "e"


FakeApi = FakePlatform


def context_query(settings, env="dev", role="anon") -> dict[str, str]:
    from pawabase_server.core.context import CONTEXT_HEADER, PlatformContext
    from pawabase_server.core.tokens import issue_context_token

    return {
        CONTEXT_HEADER: issue_context_token(
            settings.internal_secret, PlatformContext(env=env, role=role)
        )
    }


def token(settings, user_id="1", roles=(), env="dev", ttl=None) -> str:
    from pawabase_server.core.tokens import issue_user_token

    kwargs = {} if ttl is None else {"ttl": ttl}
    return issue_user_token(
        settings.jwt_master_secret,
        env=env,
        user_id=user_id,
        jti="j",
        session_id="s",
        roles=list(roles),
        **kwargs,
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
