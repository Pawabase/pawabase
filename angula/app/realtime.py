"""Channels, publication, presence and fan-out.

Angula's rooms are ``sillo-wire`` rooms. A :class:`~sillo_wire.Hub` owns the
connections on this instance: bounded per-peer queues (a slow client never
stalls a channel), replayable history, and presence callbacks. Room names carry
the environment (``acme/production/chat:lobby``), so environments never share
traffic.

With several Angula instances, a publication must reach peers on every one of
them. Publications therefore go through Sillo's Redis ``EventEmitter`` (pub/sub):
every instance receives each one and delivers it to its own peers. Without
Redis the emitter is in-process, which is right for a single instance.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sillo.cache import MemoryCache
from sillo.cache import base as cache_base
from sillo.events import EventEmitter
from sillo.exceptions import HTTPException
from sillo_wire import Hub, MemoryBacklog, Peer

from app.config import AngulaSettings
from pawabase_core.clients import ServiceClient, ServiceError
from pawabase_core.policies import Policy, PolicyEngine
from pawabase_core.policies.engine import validate_condition
from pawabase_core.templating import render

logger = logging.getLogger("pawabase.angula")

FANOUT_CHANNEL = "angula.fanout"
INSTANCE = uuid.uuid4().hex[:12]


def room_name(project: str, env: str, channel: str) -> str:
    return f"{project}/{env}/{channel}"


def split_room(room: str) -> tuple[str, str, str]:
    project, env, channel = room.split("/", 2)
    return project, env, channel


@dataclass
class ChannelRule:
    """Who may do what on channels matching a pattern.

    Patterns may use ``*`` and templates: ``user:{{ auth.user_id }}`` gives
    every signed-in user a private channel only they can join.
    """

    pattern: str
    subscribe: Any = "authenticated"
    publish: Any = "authenticated"
    presence: bool = True
    history: int = 50

    @property
    def templated(self) -> bool:
        return "{{" in self.pattern

    def claims(self, channel: str) -> bool:
        """Whether this rule governs *channel*: templates count as wildcards.

        ``user:{{ auth.user_id }}`` governs every ``user:*`` channel, so a
        private channel of someone else is refused by this rule rather than
        falling through to the default policy.
        """
        shape = re.sub(r"\{\{.*?\}\}", "*", self.pattern)
        return fnmatch.fnmatchcase(channel, shape)

    def permits(self, channel: str, auth: Mapping[str, Any]) -> bool:
        """For templated rules, whether the rendered pattern names *channel*."""
        if not self.templated:
            return True
        rendered = str(render(self.pattern, {"auth": auth}))
        return fnmatch.fnmatchcase(channel, rendered) and "None" not in rendered


@dataclass
class ChannelConfig:
    version: int
    rules: list[ChannelRule]
    engine: PolicyEngine
    allow_client_publish: bool = True
    default_policy: Any = "authenticated"
    loaded_at: float = field(default_factory=time.monotonic)

    def rule_for(self, channel: str, auth: Mapping[str, Any]) -> ChannelRule:
        for rule in self.rules:
            if rule.claims(channel):
                return rule
        return ChannelRule(
            pattern=channel, subscribe=self.default_policy, publish=self.default_policy
        )


@dataclass
class Connection:
    """One client connection, for inspection and presence."""

    peer: Peer
    project: str
    env: str
    user_id: str | None
    role: str
    auth: dict[str, Any]
    ip: str | None = None
    user_agent: str | None = None
    connected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    received: int = 0

    def describe(self) -> dict[str, Any]:
        return {
            "id": str(self.peer.id),
            "project": self.project,
            "env": self.env,
            "user_id": self.user_id,
            "role": self.role,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "connected_at": self.connected_at,
            "channels": sorted(self.channels),
            "pending": self.peer.pending,
            "received": self.received,
        }


class Realtime:
    """Angula's shared state for one process."""

    def __init__(self, settings: AngulaSettings, *, api: ServiceClient | None = None) -> None:
        self.settings = settings
        self.hub = Hub(backlog=MemoryBacklog(capacity_bytes=settings.history_bytes))
        self.api = api or ServiceClient(
            settings.api_url, secret=settings.internal_secret, issuer="angula", audience="api"
        )
        backend = "redis" if settings.redis_url else "memory"
        options = {"url": settings.redis_url} if settings.redis_url else {}
        self.emitter = EventEmitter(backend, **options)
        self.emitter.on(FANOUT_CHANNEL, self._deliver)
        self.cache = MemoryCache(namespace="angula")
        self.connections: dict[str, Connection] = {}
        self.presence: dict[str, dict[str, dict[str, Any]]] = {}
        self.stats = {
            "published": 0,
            "delivered": 0,
            "dropped": 0,
            "failed": 0,
            "denied": 0,
            "connections_total": 0,
        }
        self.recent: deque[dict[str, Any]] = deque(maxlen=500)
        self.errors: deque[dict[str, Any]] = deque(maxlen=200)
        self.started_at = time.time()

    async def start(self) -> None:
        await self.emitter.start()

    async def stop(self) -> None:
        await self.hub.close()
        await self.emitter.stop()
        await self.api.close()

    # ── configuration ────────────────────────────────────────────────────

    async def config(self, project: str, env: str) -> ChannelConfig:
        key = f"channels:{project}:{env}"
        cached = await self.cache.get(key)
        if cached is not getattr(cache_base, "_MISSING", None) and cached is not None:
            return self._build_config(cached)
        try:
            payload = await self.api.get(f"/internal/v1/environments/{project}/{env}/realtime")
        except ServiceError as exc:
            if exc.status == 404:
                raise HTTPException(
                    status_code=404, detail=f"no environment {project}/{env}"
                ) from exc
            raise
        await self.cache.set(key, payload, ttl=self.settings.config_ttl)
        return self._build_config(payload)

    @staticmethod
    def _build_config(payload: Mapping[str, Any]) -> ChannelConfig:
        policies = {}
        for name, definition in (payload.get("policies") or {}).items():
            try:
                validate_condition(definition.get("condition"))
                policies[name] = Policy(
                    name, definition.get("condition"), definition.get("description", "")
                )
            except Exception:
                continue
        rules = [
            ChannelRule(
                pattern=rule["pattern"],
                # `or` rather than `.get(key, default)`: a rule stored with an
                # explicit null (Studio's "Default" option, or a hand-edited
                # settings JSON) must fall back too, not carry a null policy.
                subscribe=rule.get("subscribe") or "authenticated",
                publish=rule.get("publish") or "authenticated",
                presence=bool(rule.get("presence", True)),
                history=int(rule.get("history", 50)),
            )
            for rule in payload.get("channels") or []
            if rule.get("pattern")
        ]
        return ChannelConfig(
            version=int(payload.get("version", 0)),
            rules=rules,
            engine=PolicyEngine(policies),
            allow_client_publish=bool(payload.get("allow_client_publish", True)),
            default_policy=payload.get("default_policy") or "authenticated",
        )

    async def authorize(
        self,
        project: str,
        env: str,
        channel: str,
        action: str,
        *,
        auth: Mapping[str, Any],
        credential: Mapping[str, Any],
        payload: Any = None,
    ) -> ChannelRule:
        """Decide *action* (``subscribe``, ``publish``, ``presence``) on *channel*."""
        if not channel or len(channel) > 200 or "/" in channel:
            raise PermissionError("invalid channel name")
        config = await self.config(project, env)
        rule = config.rule_for(channel, auth)
        if credential.get("is_service"):
            return rule
        if not rule.permits(channel, auth):
            self.stats["denied"] += 1
            raise PermissionError("this channel belongs to someone else")
        if action == "publish" and not config.allow_client_publish:
            raise PermissionError("clients may not publish in this environment")
        if action == "presence" and not rule.presence:
            raise PermissionError("presence is off for this channel")
        policy = rule.publish if action == "publish" else rule.subscribe
        context = {
            "auth": dict(auth),
            "credential": dict(credential),
            "project": project,
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
            self.stats["denied"] += 1
            raise PermissionError(decision.reason)
        return rule

    # ── publication ──────────────────────────────────────────────────────

    async def publish(
        self,
        project: str,
        env: str,
        channel: str,
        event: str,
        payload: Any,
        *,
        sender: str | None = None,
    ) -> dict[str, Any]:
        """Deliver to every subscriber on every instance."""
        message = {
            "type": "message",
            "id": uuid.uuid4().hex,
            "channel": channel,
            "event": event,
            "payload": payload,
            "from": sender,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        self.stats["published"] += 1
        await self.emitter.emit_async(
            FANOUT_CHANNEL,
            {"room": room_name(project, env, channel), "message": message, "origin": INSTANCE},
        )
        return {"id": message["id"], "channel": channel, "event": event}

    async def _deliver(self, envelope: Mapping[str, Any]) -> None:
        room = envelope["room"]
        message = envelope["message"]
        report = await self.hub.broadcast(room, message)
        self.stats["delivered"] += report.delivered
        self.stats["dropped"] += report.dropped
        self.stats["failed"] += report.failed
        project, env, channel = split_room(room)
        self.recent.append(
            {
                "project": project,
                "env": env,
                "channel": channel,
                "event": message.get("event"),
                "delivered": report.delivered,
                "dropped": report.dropped,
                "failed": report.failed,
                "at": message.get("sent_at"),
            }
        )

    async def send_presence(
        self,
        project: str,
        env: str,
        channel: str,
        *,
        joins: list[dict[str, Any]] | None = None,
        leaves: list[dict[str, Any]] | None = None,
    ) -> None:
        room = room_name(project, env, channel)
        message = {
            "type": "presence_diff",
            "channel": channel,
            "joins": joins or [],
            "leaves": leaves or [],
        }
        await self.emitter.emit_async(
            FANOUT_CHANNEL, {"room": room, "message": message, "origin": INSTANCE}
        )

    # ── presence ─────────────────────────────────────────────────────────

    def presence_members(self, project: str, env: str, channel: str) -> list[dict[str, Any]]:
        return list(self.presence.get(room_name(project, env, channel), {}).values())

    async def track(
        self, connection: Connection, channel: str, meta: dict[str, Any]
    ) -> dict[str, Any]:
        room = room_name(connection.project, connection.env, channel)
        entry = {
            "presence_ref": str(connection.peer.id),
            "user_id": connection.user_id,
            "meta": meta,
            "online_at": datetime.now(UTC).isoformat(),
        }
        self.presence.setdefault(room, {})[str(connection.peer.id)] = entry
        await self.send_presence(connection.project, connection.env, channel, joins=[entry])
        return entry

    async def untrack(self, connection: Connection, channel: str) -> None:
        room = room_name(connection.project, connection.env, channel)
        entry = self.presence.get(room, {}).pop(str(connection.peer.id), None)
        if room in self.presence and not self.presence[room]:
            del self.presence[room]
        if entry is not None:
            await self.send_presence(connection.project, connection.env, channel, leaves=[entry])

    # ── connections ──────────────────────────────────────────────────────

    def register(self, connection: Connection) -> None:
        self.connections[str(connection.peer.id)] = connection
        self.stats["connections_total"] += 1

    async def join(self, connection: Connection, channel: str) -> None:
        if (
            len(connection.channels) >= self.settings.max_channels
            and channel not in connection.channels
        ):
            raise PermissionError(
                f"a connection may join at most {self.settings.max_channels} channels"
            )
        await self.hub.join(connection.peer, room_name(connection.project, connection.env, channel))
        connection.channels.setdefault(channel, {})

    async def leave(self, connection: Connection, channel: str) -> None:
        await self.hub.leave(
            connection.peer, room_name(connection.project, connection.env, channel)
        )
        if connection.channels.pop(channel, None) is not None:
            await self.untrack(connection, channel)

    async def disconnect(self, connection: Connection) -> None:
        for channel in list(connection.channels):
            await self.leave(connection, channel)
        self.connections.pop(str(connection.peer.id), None)
        await self.hub.disconnect(connection.peer)

    async def history(
        self, project: str, env: str, channel: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        envelopes = await self.hub.history(room_name(project, env, channel), limit=limit)
        return [
            {**envelope.payload, "seq": envelope.seq}
            for envelope in envelopes
            if isinstance(envelope.payload, dict) and envelope.payload.get("type") == "message"
        ]

    # ── inspection ───────────────────────────────────────────────────────

    def channels(self, project: str | None = None, env: str | None = None) -> list[dict[str, Any]]:
        result = []
        for room in self.hub.rooms():
            p, e, channel = split_room(room)
            if (project and p != project) or (env and e != env):
                continue
            result.append(
                {
                    "project": p,
                    "env": e,
                    "channel": channel,
                    "subscribers": self.hub.count(room),
                    "presence": len(self.presence.get(room, {})),
                }
            )
        return sorted(result, key=lambda item: (item["project"], item["env"], item["channel"]))

    def summary(self) -> dict[str, Any]:
        return {
            "instance": INSTANCE,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "connections": len(self.connections),
            "channels": len(self.hub.rooms()),
            "subscriptions": self.hub.count(),
            "fanout": self.emitter.transport.__class__.__name__,
            **self.stats,
        }
