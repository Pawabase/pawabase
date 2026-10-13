"""Delivering a message to every subscriber on every instance.

Realtime rooms are ``sillo-wire`` rooms: a :class:`~sillo_wire.Hub` owns the connections of this instance, with bounded per-peer queues
(a slow client never stalls a channel) and replayable history. With several instances a publication must reach peers on all of them, so
everything goes through Sillo's ``EventEmitter``: Redis pub/sub when ``redis_url`` is set (every instance receives each message and
delivers it to its own peers), in-process otherwise, which is right for a single instance.

Three kinds of envelope travel: ``message`` (a publication), ``presence`` (a join/leave diff that each instance also applies to its
presence replica) and ``heartbeat`` (so other instances can tell this one is alive).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sillo.events import EventEmitter
from sillo_wire import Hub

from pawabase_server.core.namespace import scoped
from pawabase_server.realtime.names import room_name, split_room
from pawabase_server.realtime.presence import PresenceRegistry
from pawabase_server.realtime.stats import Stats

logger = logging.getLogger("pawabase.realtime.fanout")

FANOUT_CHANNEL = "angula.fanout"
INSTANCE = uuid.uuid4().hex[:12]


class Fanout:
    def __init__(
        self,
        hub: Hub,
        emitter: EventEmitter,
        presence: PresenceRegistry,
        stats: Stats,
        *,
        heartbeat_interval: float,
        instance: str = INSTANCE,
    ) -> None:
        self.hub = hub
        self.emitter = emitter
        self.presence = presence
        self.stats = stats
        self.instance = instance
        self.heartbeat_interval = heartbeat_interval
        self._beat: asyncio.Task[None] | None = None
        self.channel = scoped(FANOUT_CHANNEL)
        self.emitter.on(self.channel, self._receive)

    async def start(self) -> None:
        await self.emitter.start()
        if self.heartbeat_interval > 0 and self._beat is None:
            self._beat = asyncio.create_task(self._heartbeat(), name="realtime-heartbeat")

    async def stop(self) -> None:
        if self._beat is not None:
            self._beat.cancel()
            with contextlib.suppress(BaseException):
                await self._beat
            self._beat = None
        await self.emitter.stop()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self.emitter.emit_async(
                    self.channel, {"kind": "heartbeat", "origin": self.instance}
                )
            except Exception:
                logger.warning("heartbeat was not sent", exc_info=True)

    # ── sending ──────────────────────────────────────────────────────────

    async def publish(
        self, env: str, channel: str, event: str, payload: Any, *, sender: str | None
    ) -> dict[str, Any]:
        message = {
            "type": "message",
            "id": uuid.uuid4().hex,
            "channel": channel,
            "event": event,
            "payload": payload,
            "from": sender,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        self.stats.bump("published", env=env)
        await self._send({"kind": "message", "room": room_name(env, channel), "message": message})
        return {"id": message["id"], "channel": channel, "event": event}

    async def presence_diff(
        self,
        env: str,
        channel: str,
        *,
        joins: list[dict[str, Any]] | None = None,
        leaves: list[dict[str, Any]] | None = None,
    ) -> None:
        message = {
            "type": "presence_diff",
            "channel": channel,
            "joins": joins or [],
            "leaves": leaves or [],
        }
        await self._send({"kind": "presence", "room": room_name(env, channel), "message": message})

    async def _send(self, envelope: dict[str, Any]) -> None:
        await self.emitter.emit_async(self.channel, {**envelope, "origin": self.instance})

    # ── receiving ────────────────────────────────────────────────────────

    async def _receive(self, envelope: Mapping[str, Any]) -> None:
        origin = str(envelope.get("origin") or "")
        self.presence.heard_from(origin)
        kind = envelope.get("kind") or "message"
        if kind == "heartbeat":
            return
        room = envelope.get("room")
        message = envelope.get("message")
        if not room or not isinstance(message, dict):
            return
        env, channel = split_room(room)
        if kind == "presence":
            # This instance already applied its own change when it made it; a replay of it would be a no-op anyway.
            if origin != self.instance:
                self.presence.apply(
                    room, message.get("joins") or [], message.get("leaves") or [], origin
                )
            self.stats.bump("presence_diffs", env=env)
            await self.hub.broadcast(room, message)
            return
        report = await self.hub.broadcast(room, message)
        self.stats.delivery(
            env,
            channel,
            message.get("event"),
            report.delivered,
            report.dropped,
            report.failed,
            message.get("sent_at"),
        )
