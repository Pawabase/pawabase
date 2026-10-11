"""Shared fakes and helpers for Angula's tests."""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("SILLO_ENV_FILE", "")


class FakeApi:
    """Stands in for the API: serves one set of realtime rules and records the events it is sent."""

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

    async def get(self, path: str, **kwargs: Any) -> Any:
        from pawabase_core.clients import ServiceError

        self.calls += 1
        if self.missing:
            raise ServiceError(404, {"detail": "no such environment"})
        if self.fail:
            raise ServiceError(503, {"detail": "unavailable"})
        return self.realtime

    async def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        self.events.append(json)
        return {}

    async def close(self) -> None:
        pass


def context_query(settings, env="dev", role="anon") -> dict[str, str]:
    from pawabase_core.context import CONTEXT_HEADER, PlatformContext
    from pawabase_core.tokens import issue_context_token

    return {
        CONTEXT_HEADER: issue_context_token(
            settings.internal_secret, PlatformContext(env=env, role=role)
        )
    }


def token(settings, user_id="1", roles=(), env="dev", ttl=None) -> str:
    from pawabase_core.tokens import issue_user_token

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
