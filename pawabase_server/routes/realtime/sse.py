"""Server-Sent Events: the one-way counterpart of the WebSocket endpoint, on the same channel/message core.

A client subscribes to one or more channels at connect time (``?channels=a,b``) and receives everything they'd
get over the socket — messages and presence diffs — as SSE events, in the same payload shape. There is no
client-to-server direction here: publishing still goes through ``POST /realtime/v1/publish``, same as any other
HTTP caller. This is deliberately a thin adapter, not a parallel implementation: it reuses ``Peer``,
``Connection``, ``Realtime.join``/``admit``/``disconnect`` exactly as the WebSocket route does, swapping only the
socket a ``Peer`` writes to (see ``sillo_wire.Peer``'s docstring: "anything with the send_json/send_text/
send_bytes trio works").
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from sillo import HttpContext, SilloApp
from sillo.exceptions import HTTPException
from sillo.responses import sse
from sillo_wire import Peer

from pawabase_server.core.context import SCOPE_KEY
from pawabase_server.realtime import CapacityError, Connection, Realtime, TokenInvalid

logger = logging.getLogger("pawabase.realtime.sse")
MAX_CHANNELS_PER_STREAM = 20


class _QueueSocket:
    """The ``send_json``/``send_text``/``send_bytes``/``close`` trio ``Peer`` needs, backed by a queue an SSE
    generator reads from — the only thing that differs from the WebSocket transport."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        await self.queue.put(payload)

    async def send_text(
        self, payload: str
    ) -> None:  # pragma: no cover - Peer's default encoding is JSON
        await self.queue.put({"type": "text", "payload": payload})

    async def send_bytes(
        self, payload: bytes
    ) -> None:  # pragma: no cover - never sent as bytes here
        await self.queue.put({"type": "bytes"})

    async def close(self) -> None:
        self.closed = True
        await self.queue.put(None)  # wakes the generator so it can stop


def client_address(ctx: HttpContext) -> str | None:
    forwarded = (ctx.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (ctx.scope.get("client") or [None])[0]


def register(app: SilloApp, realtime: Realtime) -> None:
    @app.get(
        "/realtime/v1/sse", summary="Subscribe to one or more channels over Server-Sent Events"
    )
    async def stream(ctx: HttpContext):
        context = ctx.scope.get(SCOPE_KEY)
        if context is None:
            raise HTTPException(status_code=401, detail="an API key is required")
        if realtime.draining:
            raise HTTPException(status_code=503, detail="this instance is shutting down")
        channels = [
            c.strip() for c in (ctx.query_params.get("channels") or "").split(",") if c.strip()
        ]
        if not channels:
            raise HTTPException(status_code=422, detail="pass at least one channel in ?channels=")
        if len(channels) > MAX_CHANNELS_PER_STREAM:
            raise HTTPException(
                status_code=422, detail=f"at most {MAX_CHANNELS_PER_STREAM} channels per stream"
            )
        try:
            who = realtime.identify(ctx.query_params.get("token"), context.env)
        except TokenInvalid as exc:
            raise HTTPException(status_code=401, detail="invalid access token") from exc

        socket = _QueueSocket()
        peer = Peer(socket, identity=who.user_id, idle_timeout=realtime.settings.idle_timeout)
        connection = realtime.new_connection(
            Connection(
                peer=peer,
                env=context.env,
                user_id=who.user_id,
                role=context.role,
                auth=who.auth,
                credential={
                    "is_service": context.is_service,
                    "role": context.role,
                    "scopes": list(context.scopes),
                },
                ip=client_address(ctx),
                user_agent=ctx.headers.get("user-agent"),
                token_exp=who.expires_at,
            )
        )
        try:
            realtime.admit(connection)
        except CapacityError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        joined: list[str] = []
        try:
            for channel in channels:
                try:
                    await realtime.authorize(
                        context.env,
                        channel,
                        "subscribe",
                        auth=connection.auth,
                        credential=connection.credential,
                    )
                except PermissionError as exc:
                    raise HTTPException(status_code=403, detail=str(exc)) from exc
                await realtime.join(connection, channel)
                joined.append(channel)
        except Exception:
            await realtime.disconnect(connection)
            raise
        peer.start()

        async def source():
            try:
                yield {"type": "welcome", "connection_id": connection.id, "channels": joined}
                while True:
                    item = await socket.queue.get()
                    if item is None or socket.closed:
                        return
                    yield item
            finally:
                with contextlib.suppress(Exception):
                    await realtime.disconnect(connection)

        return sse(source())

    return stream
