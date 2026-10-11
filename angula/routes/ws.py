"""The WebSocket endpoint: authenticates a connection, admits it, and runs its conversation until it ends."""

from __future__ import annotations

import contextlib
import logging

from sillo import SilloApp, WebSocketContext, WebSocketDisconnect
from sillo_wire import Peer

from app.protocol import codes
from app.protocol.session import Closing, Session
from app.realtime import CapacityError, Connection, Realtime, TokenInvalid
from pawabase_core.context import SCOPE_KEY

logger = logging.getLogger("pawabase.angula.socket")


def client_address(ws: WebSocketContext) -> str | None:
    """The caller's address: the first hop of ``X-Forwarded-For`` when behind the gateway, else the socket's own peer."""
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in ws.scope.get("headers", [])}
    forwarded = headers.get("x-forwarded-for", "").split(",")[0].strip()
    client = ws.scope.get("client")
    return forwarded or (client[0] if client else None)


def register(app: SilloApp, realtime: Realtime) -> None:
    @app.ws_route("/realtime/v1/socket")
    async def socket(ws: WebSocketContext):
        context = ws.scope.get(SCOPE_KEY)
        if context is None:
            await ws.close(code=codes.CLOSE_NO_KEY, reason="an API key is required")
            return
        if realtime.draining:
            await ws.close(code=codes.CLOSE_GOING_AWAY, reason="this instance is shutting down")
            return
        try:
            who = realtime.identify(ws.query_params.get("token"), context.env)
        except TokenInvalid:
            await ws.close(code=codes.CLOSE_BAD_TOKEN, reason="invalid access token")
            return
        await ws.accept()
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in ws.scope.get("headers", [])}
        peer = Peer(ws, identity=who.user_id, idle_timeout=realtime.settings.idle_timeout)
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
                ip=client_address(ws),
                user_agent=headers.get("user-agent"),
                token_exp=who.expires_at,
            )
        )
        try:
            realtime.admit(connection)
        except CapacityError as exc:
            await ws.close(code=codes.CLOSE_CAPACITY, reason=str(exc))
            return
        peer.start()
        session = Session(realtime, connection)
        try:
            await peer.send(
                {"type": "welcome", "connection_id": connection.id, "user_id": who.user_id}
            )
            while True:
                await session.receive(await ws.receive_text())
        except WebSocketDisconnect:
            pass
        except Closing as closing:
            with contextlib.suppress(Exception):
                await ws.close(code=closing.code, reason=closing.reason)
        except Exception:
            logger.exception("socket for %s ended with an error", connection.id)
        finally:
            await realtime.disconnect(connection)
