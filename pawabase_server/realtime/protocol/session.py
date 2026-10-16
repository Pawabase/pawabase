"""What each client message does.

A :class:`Session` is the conversation with one connection. It receives parsed :class:`~pawabase_server.realtime.protocol.messages.Message` objects, applies
the connection's rate budget, and routes each to its handler. Every request that carries a ``ref`` is answered with an ``ack`` of the same
``ref``; failures are acks with ``ok: false`` and an ``error`` name from :mod:`pawabase_server.realtime.protocol.codes`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sillo.exceptions import HTTPException

from pawabase_server.realtime import (
    CapacityError,
    Connection,
    PayloadTooLarge,
    Realtime,
    TokenInvalid,
    check_event,
)
from pawabase_server.realtime.protocol import codes
from pawabase_server.realtime.protocol.messages import Message, ProtocolError, parse

logger = logging.getLogger("pawabase.realtime.session")

Handler = Callable[["Session", Message], Awaitable[None]]


class Closing(Exception):
    """Raised to end the connection from inside a handler. ``code`` is the WebSocket close code."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


class Session:
    def __init__(self, realtime: Realtime, connection: Connection) -> None:
        self.realtime = realtime
        self.connection = connection
        self.peer = connection.peer

    # ── entry ────────────────────────────────────────────────────────────

    async def receive(self, raw: str) -> None:
        """Handle one text frame. Raises :class:`Closing` when the connection must end."""
        connection = self.connection
        connection.received += 1
        try:
            message = parse(raw, max_bytes=self.realtime.settings.max_message_bytes)
        except ProtocolError as exc:
            if exc.code == codes.MESSAGE_TOO_LARGE:
                self.realtime.stats.bump("oversized", env=connection.env)
            await self.peer.send({"type": "error", "error": exc.code})
            return
        if not connection.allow_message():
            await self._limited(message)
            return
        if connection.strikes is not None:
            connection.strikes.clear()
        handler = HANDLERS.get(message.kind)
        if handler is None:
            await self._ack(message, False, error=codes.UNKNOWN_TYPE)
            return
        await self._run(handler, message)

    async def _limited(self, message: Message) -> None:
        connection = self.connection
        self.realtime.stats.bump("rate_limited", env=connection.env)
        await self._ack(message, False, error=codes.RATE_LIMITED)
        if connection.strikes is not None and connection.strikes.hit():
            self.realtime.stats.bump("closed_for_abuse", env=connection.env)
            raise Closing(codes.CLOSE_ABUSE, "sending too fast")

    async def _run(self, handler: Handler, message: Message) -> None:
        env = self.connection.env
        try:
            await handler(self, message)
        except PermissionError as exc:
            self.realtime.stats.error(env, message.channel, message.kind, str(exc))
            await self._ack(message, False, error=codes.FORBIDDEN, reason=str(exc))
        except PayloadTooLarge as exc:
            self.realtime.stats.bump("oversized", env=env)
            await self._ack(
                message, False, error=codes.PAYLOAD_TOO_LARGE, reason=str(exc), limit=exc.limit
            )
        except HTTPException as exc:
            await self._ack(message, False, error=codes.UNAVAILABLE, reason=str(exc.detail))
        except ProtocolError as exc:
            await self._ack(message, False, error=exc.code, reason=exc.detail)
        except Closing:
            raise
        except Exception:
            logger.exception("handler for %r failed", message.kind)
            await self._ack(message, False, error=codes.UNAVAILABLE)

    async def _ack(self, message: Message, ok: bool = True, **extra: Any) -> None:
        """Acknowledge *message*. A success is only sent when the client asked for one (it sent a ``ref``); a failure is always sent."""
        if message.ref is not None or not ok:
            await self.peer.send({"type": "ack", "ref": message.ref, "ok": ok, **extra})

    # ── handlers ─────────────────────────────────────────────────────────

    async def ping(self, message: Message) -> None:
        await self.peer.send({"type": "pong"})

    async def auth(self, message: Message) -> None:
        try:
            who = await self.realtime.reauthenticate(self.connection, message.token)
        except TokenInvalid:
            await self._ack(message, False, error=codes.INVALID_TOKEN)
            return
        except CapacityError as exc:
            await self._ack(message, False, error=codes.CAPACITY, reason=str(exc))
            return
        await self._ack(message, user_id=who.user_id)
        # What this person may use may differ from what the previous caller could: drop whatever no longer applies.
        await self.realtime.revalidate(self.connection)

    async def subscribe(self, message: Message) -> None:
        connection, channel = self.connection, message.channel
        rule = await self.realtime.authorize(
            connection.env,
            channel,
            "subscribe",
            auth=connection.auth,
            credential=connection.credential,
        )
        if message.presence is not None:
            self.realtime.authorizer.check_payload(
                message.presence,
                what="presence meta",
                limit=self.realtime.settings.max_presence_bytes,
            )
        await self.realtime.join(connection, channel)
        await self._ack(message, channel=channel)
        if message.since is not None:
            for item in await self.realtime.history(
                connection.env, channel, limit=max(1, rule.history),
                auth=connection.auth, credential=connection.credential,
            ):
                if item["seq"] > message.since:
                    await self.peer.send(item)
        if rule.presence:
            await self.peer.send(
                {
                    "type": "presence_state",
                    "channel": channel,
                    "members": self.realtime.presence_members(connection.env, channel),
                }
            )
            if message.presence is not None:
                await self.realtime.track(connection, channel, message.presence)

    async def unsubscribe(self, message: Message) -> None:
        await self.realtime.leave(self.connection, message.channel)
        await self._ack(message, channel=message.channel)

    async def publish(self, message: Message) -> None:
        connection, channel = self.connection, message.channel
        service = connection.credential.get("is_service")
        if channel not in connection.channels and not service:
            await self.realtime.authorize(
                connection.env,
                channel,
                "subscribe",
                auth=connection.auth,
                credential=connection.credential,
            )
        await self.realtime.authorize(
            connection.env,
            channel,
            "publish",
            auth=connection.auth,
            credential=connection.credential,
            payload=message.payload,
        )
        event = check_event(message.event)
        result = await self.realtime.publish(
            connection.env, channel, event, message.payload, sender=connection.user_id
        )
        await self._ack(message, id=result["id"])
        self.realtime.forward(connection, channel, event, message.payload)

    async def presence(self, message: Message) -> None:
        connection, channel = self.connection, message.channel
        if channel not in connection.channels:
            raise PermissionError("subscribe to the channel first")
        await self.realtime.authorize(
            connection.env,
            channel,
            "presence",
            auth=connection.auth,
            credential=connection.credential,
        )
        await self.realtime.track(connection, channel, message.meta)
        await self._ack(message)

    async def history(self, message: Message) -> None:
        connection, channel = self.connection, message.channel
        rule = await self.realtime.authorize(
            connection.env,
            channel,
            "subscribe",
            auth=connection.auth,
            credential=connection.credential,
        )
        limit = max(1, min(message.limit or 50, rule.history or 1))
        await self.peer.send(
            {
                "type": "history",
                "ref": message.ref,
                "channel": channel,
                "messages": await self.realtime.history(connection.env, channel, limit, auth=connection.auth, credential=connection.credential),
            }
        )


HANDLERS: dict[str, Handler] = {
    "ping": Session.ping,
    "auth": Session.auth,
    "subscribe": Session.subscribe,
    "unsubscribe": Session.unsubscribe,
    "publish": Session.publish,
    "presence": Session.presence,
    "history": Session.history,
}
