"""The realtime hub's shared state for one process: the pieces in this package, composed.

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
from sillo_wire.envelope import DeliveryReport, Envelope

from pawabase_server.data.presentation import present_record
from pawabase_server.projects import quotas
from pawabase_server.realtime.authorizer import Authorizer
from pawabase_server.realtime.connection import EXPIRY_WARNING, Connection
from pawabase_server.realtime.errors import CapacityError, PayloadTooLarge
from pawabase_server.realtime.fanout import FANOUT_CHANNEL, INSTANCE, Fanout
from pawabase_server.realtime.flows import FlowForwarder, Forwarded
from pawabase_server.realtime.identity import Identity, TokenInvalid, anonymous, identify
from pawabase_server.realtime.limits import Quota, Strikes, TokenBucket
from pawabase_server.realtime.names import check_event, room_name, split_room
from pawabase_server.realtime.presence import Member, PresenceRegistry
from pawabase_server.realtime.rules import ChannelConfig, ChannelRule, build_config
from pawabase_server.realtime.stats import Stats
from pawabase_server.realtime.store import ConfigStore
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.realtime")

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
    """Everything the realtime hub knows and does."""

    def __init__(self, settings: Settings, *, platform: Any) -> None:
        self.settings = settings
        self.platform = platform
        self.stats = Stats()
        self.backlog = MemoryBacklog(capacity_bytes=settings.history_bytes)
        self.hub = Hub(backlog=self.backlog)
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
        self.fanout.resource_delivery = self._deliver_resource
        self.store = ConfigStore(
            platform.realtime_settings, ttl=settings.config_ttl, stale_ttl=settings.stale_config_ttl
        )
        self.authorizer = Authorizer(self.store, settings, self.stats)
        self.flows = FlowForwarder(
            platform.emit_event, self.stats, size=settings.forward_queue_size
        )
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
        self,
        env: str,
        channel: str,
        event: str,
        payload: Any,
        *,
        sender: str | None = None,
        client_msg_id: str | None = None,
    ) -> dict[str, Any]:
        """Deliver to every subscriber on every instance.

        A durable channel's publish is written to :class:`RealtimeMessage` first. When *client_msg_id* names a
        publish already stored for this channel, nothing is delivered again — the caller's retry gets back the
        same stored message, not a second delivery (idempotent publish, guaranteed only for durable channels;
        an ephemeral or plain channel has no durable store to dedupe against, so a retry there is a second
        delivery, same as it always was — see the module docstring's note on documented delivery scopes).
        """
        self.authorizer.check_payload(payload)
        event = check_event(event)
        await self.platform.usage.consume(
            await self.platform.state(env), "max_realtime_messages_per_month"
        )
        config = await self.store.get(env)
        rule = config.rule_for(channel)
        if rule.durable:
            from pawabase_server.realtime.persistence import store_durable

            row, created = await store_durable(
                self.platform,
                env,
                channel,
                event,
                payload,
                client_msg_id=client_msg_id,
                sender=sender,
                retention_seconds=rule.retention_seconds or self.settings.realtime_retention_seconds,
            )
            if not created:
                return {"id": row.id, "channel": channel, "event": event, "duplicate": True}
            await self.fanout.deliver_stored(env, channel, row)
            return {"id": row.id, "channel": channel, "event": event}
        return await self.fanout.publish(env, channel, event, payload, sender=sender)

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
        if channel not in connection.channels:
            await self._check_channel_quota(connection, channel)
        await self.hub.join(connection.peer, room_name(connection.env, channel))
        connection.channels.setdefault(channel, {})

    async def _check_channel_quota(self, connection: Connection, channel: str) -> None:
        """Refuse a channel no one in the project has open yet when the plan's ``max_realtime_channels`` are all in use.

        Counted on this instance: with several realtime instances each enforces the limit for the connections it holds.
        """
        state = await self.platform.state(connection.env)
        allowed = quotas.allowance(state.project_limits, "max_realtime_channels")
        if not allowed or not state.project_id:
            return
        project_of: dict[str, str | None] = {connection.env: state.project_id}
        in_use: set[tuple[str, str]] = set()
        for other in self.connections.values():
            if other.env not in project_of:
                project_of[other.env] = (await self.platform.state(other.env)).project_id
            if project_of[other.env] == state.project_id:
                in_use.update((other.env, name) for name in other.channels)
        if (connection.env, channel) not in in_use and len(in_use) >= allowed:
            raise PermissionError(
                quotas.QuotaExceeded("max_realtime_channels", allowed, len(in_use)).message
            )

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

    async def _resource_message(self, env, channel, message, auth, credential, rows=None):
        """The message as *auth* may see it, or ``None``: the read policy must pass on both the changed row and the row as it is now.

        *rows* caches the current row across the subscribers of one message, so a change costs one read, not one per subscriber.
        """
        state = await self.platform.state(env)
        name = channel.removeprefix("resource:")
        resource = state.resources.get(name)
        payload = message.get("payload")
        if resource is None or not resource.realtime or not isinstance(payload, dict):
            return None
        record = payload.get("record")
        if payload.get("resource") != name or not isinstance(record, dict):
            return None
        options = resource.operations or {}
        read = next(
            (options[k] for k in ("get", "list") if options.get(k, {}).get("enabled")), None
        )
        if not read and not credential.get("is_service"):
            return None
        context = {
            "auth": dict(auth),
            "credential": dict(credential),
            "env": env,
            "settings": state.settings,
            "record": record,
            "input": None,
        }
        if not await state.engine.check((read or {}).get("policy") or "authenticated", context):
            return None
        # History cannot resurrect access revoked by a later ownership change.
        if payload.get("change") != "deleted":
            store = await state.store(name)
            key = record.get(store.spec.primary_key)
            if rows is not None and key in rows:
                current = rows[key]
            else:
                current = await store.get(key)
                if rows is not None:
                    rows[key] = current
            if current is None or not await state.engine.check(
                (read or {}).get("policy") or "authenticated", {**context, "record": current}
            ):
                return None
        presented = await present_record(state, resource, record, {**context, "record": None})
        return {**message, "payload": {**payload, "record": presented}}

    async def _deliver_resource(self, env, channel, message):
        envelope = Envelope(payload=message, room=room_name(env, channel))
        await self.backlog.append(envelope)
        delivered = dropped = failed = 0
        rows: dict[Any, Any] = {}
        for connection in list(self.connections.values()):
            if connection.env != env or channel not in connection.channels:
                continue
            try:
                remaining = connection.expires_in()
                if remaining is not None and remaining <= 0:
                    continue
                await self.authorize(
                    env,
                    channel,
                    "subscribe",
                    auth=connection.auth,
                    credential=connection.credential,
                )
                visible = await self._resource_message(
                    env, channel, message, connection.auth, connection.credential, rows
                )
                if visible is not None:
                    if connection.peer.offer(
                        Envelope(payload=visible, room=envelope.room, seq=envelope.seq)
                    ):
                        delivered += 1
                    else:
                        dropped += 1
            except PermissionError:
                continue
            except Exception:
                failed += 1
                logger.exception("resource message authorization/delivery failed for %s", channel)
        return DeliveryReport(delivered=delivered, dropped=dropped, failed=failed)

    async def history(
        self, env: str, channel: str, limit: int = 50, *, auth=None, credential=None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, self.settings.max_history))
        config = await self.store.get(env)
        if config.rule_for(channel).durable:
            from pawabase_server.realtime.persistence import durable_history

            messages = await durable_history(self.platform, env, channel, limit)
        else:
            envelopes = await self.hub.history(room_name(env, channel), limit=limit)
            messages = [
                {**envelope.payload, "seq": envelope.seq}
                for envelope in envelopes
                if isinstance(envelope.payload, dict) and envelope.payload.get("type") == "message"
            ]
        if channel.startswith("resource:"):
            if auth is None or credential is None:
                return []  # no ambient service bypass for raw history readers
            visible = []
            rows: dict[Any, Any] = {}
            for message in messages:
                item = await self._resource_message(env, channel, message, auth, credential, rows)
                if item is not None:
                    visible.append(item)
            return visible
        return messages

    # ── housekeeping ─────────────────────────────────────────────────────

    async def sweep(self) -> dict[str, int]:
        """One housekeeping pass: idle peers, stale presence, expiring tokens, changed rules. Returns what it did, for tests and logs."""
        done = {
            "pruned": 0,
            "presence_expired": 0,
            "tokens_expired": 0,
            "revalidated": 0,
            "durable_expired": 0,
        }
        from pawabase_server.realtime.persistence import purge_expired

        with contextlib.suppress(Exception):
            done["durable_expired"] = await purge_expired()
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
