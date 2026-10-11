"""Deciding whether a caller may subscribe, publish or be present on a channel."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pawabase_server.realtime.errors import PayloadTooLarge
from pawabase_server.realtime.names import check_channel
from pawabase_server.realtime.rules import ChannelRule
from pawabase_server.realtime.stats import Stats
from pawabase_server.realtime.store import ConfigStore
from pawabase_server.settings import Settings

ACTIONS = ("subscribe", "publish", "presence")


def payload_size(payload: Any) -> int:
    """The bytes a payload takes once serialized, which is what travels and what is retained."""
    if payload is None:
        return 0
    try:
        return len(json.dumps(payload, separators=(",", ":"), default=str).encode())
    except (TypeError, ValueError) as exc:
        raise PayloadTooLarge(0, 0, "payload") from exc


class Authorizer:
    def __init__(self, store: ConfigStore, settings: Settings, stats: Stats) -> None:
        self.store = store
        self.settings = settings
        self.stats = stats

    def check_payload(
        self, payload: Any, *, what: str = "payload", limit: int | None = None
    ) -> None:
        ceiling = limit if limit is not None else self.settings.max_payload_bytes
        size = payload_size(payload)
        if ceiling and size > ceiling:
            raise PayloadTooLarge(size, ceiling, what)

    async def authorize(
        self,
        env: str,
        channel: str,
        action: str,
        *,
        auth: Mapping[str, Any],
        credential: Mapping[str, Any],
        payload: Any = None,
    ) -> ChannelRule:
        """Decide *action* on *channel*. Returns the rule that governs it; raises :class:`PermissionError` when refused."""
        if action not in ACTIONS:
            raise PermissionError("unknown action")
        check_channel(channel)
        if action == "publish":
            self.check_payload(payload)
        config = await self.store.get(env)
        rule = config.rule_for(channel)
        if credential.get("is_service"):
            return rule
        if not rule.permits(channel, auth):
            self.stats.bump("denied", env=env)
            raise PermissionError("this channel belongs to someone else")
        if action == "publish" and not config.allow_client_publish:
            raise PermissionError("clients may not publish in this environment")
        if action == "presence" and not rule.presence:
            raise PermissionError("presence is off for this channel")
        policy = rule.publish if action == "publish" else rule.subscribe
        context = {
            "auth": dict(auth),
            "credential": dict(credential),
            "env": env,
            "channel": {
                "name": channel,
                "segments": channel.replace(":", "/").split("/"),
                "action": action,
            },
            "input": payload,
            "record": None,
        }
        decision = await config.engine.check(policy, context)
        if not decision:
            self.stats.bump("denied", env=env)
            raise PermissionError(decision.reason)
        return rule
