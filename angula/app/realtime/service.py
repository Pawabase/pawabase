"""Angula's shared state for one process: the pieces in this package, composed.

``Realtime`` owns no logic of its own that a smaller piece could hold. It wires :class:`ConfigStore` (rules), :class:`Authorizer` (decisions),
:class:`Fanout` (delivery across instances), :class:`PresenceRegistry` (who is here), :class:`FlowForwarder` (publications to flows) and the
connection table together, and runs the housekeeping that needs all of them at once.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from typing import Any

from sillo.events import EventEmitter
from sillo.exceptions import HTTPException
from sillo_wire import Hub, MemoryBacklog

from app.config import AngulaSettings
from app.realtime.authorizer import Authorizer
from app.realtime.connection import EXPIRY_WARNING, Connection
from app.realtime.errors import CapacityError, PayloadTooLarge
from app.realtime.fanout import FANOUT_CHANNEL, INSTANCE, Fanout
from app.realtime.flows import FlowForwarder, Forwarded
from app.realtime.identity import Identity, TokenInvalid, anonymous, identify
from app.realtime.limits import Quota, Strikes, TokenBucket
from app.realtime.names import check_event, room_name, split_room
from app.realtime.presence import Member, PresenceRegistry
from app.realtime.rules import ChannelConfig, ChannelRule, build_config
from app.realtime.stats import Stats
from app.realtime.store import ConfigStore
from pawabase_core.clients import ServiceClient

logger = logging.getLogger("pawabase.angula")

__all__ = [
    "FANOUT_CHANNEL",
    "INSTANCE",
    "CapacityError",
    "Connection",
    "PayloadTooLarge",
    "Realtime",
    "TokenInvalid",
    "room_name",
    "split_room",
]


class Realtime:
    """Everything one Angula process knows and does."""

    def __init__(self, settings: AngulaSettings, *, api: ServiceClient | None = None) -> None:
        self.settings = settings
        self.api = api or ServiceClient(
            settings.api_url, secret=settings.internal_secret, issuer="angula", audience="api"
        )
        self.stats = Stats()
        self.hub = Hub(backlog=MemoryBacklog(capacity_bytes=settings.history_bytes))
        backend = "redis" if settings.redis_url else "memory"
        self.emitter = EventEmitter(
            backend, **({"url": settings.redis_url} if settings.redis_url else {})
        )
        self.presence = PresenceRegistry(INSTANCE, ttl=settings.presence_ttl)
        self.fanout = Fanout(
            self.hub,
            self.emitter,
            self.presence,
            self.stats,
            heartbeat_interval=settings.heartbeat_interval,
        )
        self.store = ConfigStore(
            self.api, ttl=settings.config_ttl, stale_ttl=settings.stale_config_ttl
        )
        self.authorizer = Authorizer(self.store, settings, self.stats)
        self.flows = FlowForwarder(self.api, self.stats, size=settings.forward_queue_size)
        self.connections: dict[str, Connection] = {}
        self._env_quota = Quota(settings.max_connections_per_env)
        self._identity_quota = Quota(settings.max_connections_per_identity)
        self._versions: dict[str, int] = {}
        self._draining = False

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self.fanout.start()
        self.flows.start()

    async def stop(self) -> None:
        await self.drain()
        await self.flows.stop(self.settings.drain_grace)
        await self.hub.close()
        await self.fanout.stop()
        await self.api.close()

    @property
    def draining(self) -> bool:
        return self._draining

    async def drain(self) -> None:
        """Tell every client to reconnect elsewhere, and refuse new connections. Used on shutdown."""
        self._draining = True
        for connection in list(self.connections.values()):
            with contextlib.suppress(Exception):
                await connection.peer.send({"type": "going_away", "retry_after": 1})

    # ── read-only views kept for callers that inspect the service ───────

    @property
    def errors(self):
        return self.stats.errors

    @property
    def recent(self):
        return self.stats.recent

    # ── configuration and decisions ──────────────────────────────────────

    _build_config = staticmethod(build_config)

    async def config(self, env: str) -> ChannelConfig:
        return await self.store.get(env)

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
        """Decide *action* (``subscribe``, ``publish``, ``presence``) on *channel*. See :meth:`Authorizer.authorize`."""
        return await self.authorizer.authorize(
            env, channel, action, auth=auth, credential=credential, payload=payload
        )

    def identify(self, token: str | None, env: str) -> Identity:
        return identify(token, self.settings.jwt_master_secret, env)

    # ── publication ──────────────────────────────────────────────────────

    async def publish(
        self, env: str, channel: str, event: str, payload: Any, *, sender: str | None = None
    ) -> dict[str, Any]:
        """Deliver to every subscriber on every instance."""
        self.authorizer.check_payload(payload)
        return await self.fanout.publish(env, channel, check_event(event), payload, sender=sender)

    def forward(self, connection: Connection, channel: str, event: str, payload: Any) -> bool:
        """Offer a client publication to flows. Never waits; see :class:`FlowForwarder`."""
        return self.flows.submit(
            Forwarded(connection.env, channel, event, payload, connection.user_id)
        )

    # ── presence ─────────────────────────────────────────────────────────

    def presence_members(self, env: str, channel: str) -> list[dict[str, Any]]:
        return self.presence.members(room_name(env, channel))

    async def track(
        self, connection: Connection, channel: str, meta: dict[str, Any]
    ) -> dict[str, Any]:
        self.authorizer.check_payload(
            meta, what="presence meta", limit=self.settings.max_presence_bytes
        )
        room = room_name(connection.env, channel)
        member = Member(connection.id, connection.user_id, meta, instance=INSTANCE)
        self.presence.join(room, member)
        entry = member.public()
        await self.fanout.presence_diff(connection.env, channel, joins=[entry])
        return entry

    async def untrack(self, connection: Connection, channel: str) -> None:
        member = self.presence.leave(room_name(connection.env, channel), connection.id)
        if member is not None:
            await self.fanout.presence_diff(connection.env, channel, leaves=[member.public()])

    # ── connections ──────────────────────────────────────────────────────

    def new_connection(self, connection: Connection) -> Connection:
        """Give a connection its rate budget. Called before :meth:`admit`."""
        if self.settings.rate_per_second > 0:
            connection.bucket = TokenBucket(self.settings.rate_per_second, self.settings.rate_burst)
            connection.strikes = Strikes(self.settings.abuse_strikes)
        return connection

    def admit(self, connection: Connection) -> None:
        """Count a connection against its environment's and its person's ceilings. Raises :class:`CapacityError`."""
        if not self._env_quota.acquire(connection.env):
            raise CapacityError("environment", self.settings.max_connections_per_env)
        if connection.user_id:
            key = f"{connection.env}:{connection.user_id}"
            if not self._identity_quota.acquire(key):
                self._env_quota.release(connection.env)
                raise CapacityError("identity", self.settings.max_connections_per_identity)
            connection.held_identity = key
        self.connections[connection.id] = connection
        self.stats.bump("connections_total", env=connection.env)

    def register(self, connection: Connection) -> None:
        """Admit a connection, ignoring ceilings. For callers that have already decided to accept it."""
        self.connections[connection.id] = connection
        self.stats.bump("connections_total", env=connection.env)

    async def reauthenticate(self, connection: Connection, token: str | None) -> Identity:
        """Switch a connection to the caller *token* proves. Raises :class:`TokenInvalid` or :class:`CapacityError`; on either the
        connection keeps acting as before."""
        who = self.identify(token, connection.env)
        new_key = f"{connection.env}:{who.user_id}" if who.user_id else None
        if new_key != connection.held_identity:
            if new_key and not self._identity_quota.acquire(new_key):
                raise CapacityError("identity", self.settings.max_connections_per_identity)
            if connection.held_identity:
                self._identity_quota.release(connection.held_identity)
            connection.held_identity = new_key
        connection.auth, connection.user_id, connection.token_exp = (
            who.auth,
            who.user_id,
            who.expires_at,
        )
        connection.warned_expiry = False
        connection.peer.identity = who.user_id
        return who

    async def join(self, connection: Connection, channel: str) -> None:
        if (
            len(connection.channels) >= self.settings.max_channels
            and channel not in connection.channels
        ):
            raise PermissionError(
                f"a connection may join at most {self.settings.max_channels} channels"
            )
        await self.hub.join(connection.peer, room_name(connection.env, channel))
        connection.channels.setdefault(channel, {})

    async def leave(self, connection: Connection, channel: str) -> None:
        await self.hub.leave(connection.peer, room_name(connection.env, channel))
        if connection.channels.pop(channel, None) is not None:
            await self.untrack(connection, channel)

    async def disconnect(self, connection: Connection) -> None:
        # The socket loop and the pruner can both end a connection, so this is safe to run twice: quotas are released once.
        if self.connections.pop(connection.id, None) is not None:
            self._env_quota.release(connection.env)
            if connection.held_identity:
                self._identity_quota.release(connection.held_identity)
                connection.held_identity = None
        for channel in list(connection.channels):
            with contextlib.suppress(Exception):
                await self.leave(connection, channel)
        with contextlib.suppress(Exception):
            await self.hub.disconnect(connection.peer)

    async def history(self, env: str, channel: str, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, self.settings.max_history))
        envelopes = await self.hub.history(room_name(env, channel), limit=limit)
        return [
            {**envelope.payload, "seq": envelope.seq}
            for envelope in envelopes
            if isinstance(envelope.payload, dict) and envelope.payload.get("type") == "message"
        ]

    # ── housekeeping ─────────────────────────────────────────────────────

    async def sweep(self) -> dict[str, int]:
        """One housekeeping pass: idle peers, stale presence, expiring tokens, changed rules. Returns what it did, for tests and logs."""
        done = {"pruned": 0, "presence_expired": 0, "tokens_expired": 0, "revalidated": 0}
        for peer in await self.hub.prune():
            connection = self.connections.get(str(peer.id))
            if connection is not None:
                await self.disconnect(connection)
                done["pruned"] += 1
        for room, members in self.presence.expire().items():
            env, channel = split_room(room)
            done["presence_expired"] += len(members)
            await self.hub.broadcast(
                room, {"type": "presence_diff", "channel": channel, "joins": [], "leaves": members}
            )
        for connection in list(self.connections.values()):
            remaining = connection.expires_in()
            if remaining is None:
                continue
            if remaining <= 0:
                await self._demote(connection)
                done["tokens_expired"] += 1
            elif remaining <= EXPIRY_WARNING and not connection.warned_expiry:
                connection.warned_expiry = True
                with contextlib.suppress(Exception):
                    await connection.peer.send(
                        {"type": "token_expiring", "expires_in": max(1, round(remaining))}
                    )
        for env in {c.env for c in self.connections.values()}:
            done["revalidated"] += await self._check_rules(env)
        return done

    async def _check_rules(self, env: str) -> int:
        """If the environment's rules changed since last looked at, re-check every subscription in it."""
        try:
            version = (await self.store.get(env)).version
        except Exception:
            return 0
        previous = self._versions.get(env)
        self._versions[env] = version
        if previous is None or previous == version:
            return 0
        revoked = 0
        for connection in [c for c in self.connections.values() if c.env == env]:
            revoked += await self.revalidate(connection)
        return revoked

    async def revalidate(self, connection: Connection) -> int:
        """Re-check what *connection* is subscribed to against the current rules, and close the channels it may no longer use."""
        revoked = 0
        for channel in list(connection.channels):
            try:
                await self.authorize(
                    connection.env,
                    channel,
                    "subscribe",
                    auth=connection.auth,
                    credential=connection.credential,
                )
            except (PermissionError, HTTPException) as exc:
                reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
                await self.leave(connection, channel)
                revoked += 1
                self.stats.bump("channels_revoked", env=connection.env)
                with contextlib.suppress(Exception):
                    await connection.peer.send(
                        {"type": "channel_closed", "channel": channel, "reason": str(reason)}
                    )
        return revoked

    async def _demote(self, connection: Connection) -> None:
        """A token ran out: the connection carries on as an anonymous caller, losing what only the person could use."""
        who = anonymous()
        if connection.held_identity:
            self._identity_quota.release(connection.held_identity)
            connection.held_identity = None
        connection.auth, connection.user_id, connection.token_exp = who.auth, None, None
        connection.peer.identity = None
        with contextlib.suppress(Exception):
            await connection.peer.send({"type": "token_expired"})
        await self.revalidate(connection)

    # ── inspection ───────────────────────────────────────────────────────

    def channels(self, env: str | None = None) -> list[dict[str, Any]]:
        result = []
        for room in self.hub.rooms():
            e, channel = split_room(room)
            if env and e != env:
                continue
            result.append(
                {
                    "env": e,
                    "channel": channel,
                    "subscribers": self.hub.count(room),
                    "presence": self.presence.count(room),
                }
            )
        return sorted(result, key=lambda item: (item["env"], item["channel"]))

    def environment_summary(self, env: str) -> dict[str, Any]:
        return {
            "env": env,
            "connections": sum(1 for c in self.connections.values() if c.env == env),
            "channels": len(self.channels(env)),
            "counters": self.stats.for_env(env),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "instance": INSTANCE,
            "instances": self.presence.instances(),
            "uptime_seconds": self.stats.uptime(),
            "draining": self._draining,
            "connections": len(self.connections),
            "channels": len(self.hub.rooms()),
            "subscriptions": self.hub.count(),
            "fanout": self.emitter.transport.__class__.__name__,
            "flow_queue": self.flows.pending,
            "rules_cached": self.store.known(),
            "rules_refreshes": self.store.refreshes,
            "rules_stale_served": self.store.stale_served,
            **self.stats.counts,
        }
