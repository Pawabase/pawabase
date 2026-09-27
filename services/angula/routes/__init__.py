"""Angula's WebSocket protocol, HTTP publishing, and inspection endpoints.

The socket speaks JSON. The client sends::

    {"type": "subscribe",   "channel": "chat:lobby", "ref": "1", "presence": {"name": "Ada"}, "since": 0}
    {"type": "unsubscribe", "channel": "chat:lobby", "ref": "2"}
    {"type": "publish",     "channel": "chat:lobby", "event": "message", "payload": {...}, "ref": "3"}
    {"type": "presence",    "channel": "chat:lobby", "meta": {"typing": true}}
    {"type": "history",     "channel": "chat:lobby", "limit": 50, "ref": "4"}
    {"type": "auth",        "token": "<access token>"}
    {"type": "ping"}

and receives ``ack``, ``message``, ``presence_state``, ``presence_diff``,
``history`` and ``pong`` messages. Every request carrying a ``ref`` gets an
``ack`` with the same ``ref``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, SilloApp, WebSocketContext
from sillo.exceptions import HTTPException
from sillo_wire import Peer

from app.realtime import Connection, Realtime
from pawabase_kit.context import SCOPE_KEY, require_context
from pawabase_kit.policies import credential_context
from pawabase_kit.principal import ANONYMOUS_POLICY_CONTEXT, policy_auth
from pawabase_kit.service import SERVICE_ONLY
from pawabase_kit.tokens import TokenInvalid, verify_user_token


class PublishBody(BaseModel):
    channel: str = Field(min_length=1, max_length=200)
    event: str = Field(default="message", min_length=1, max_length=100)
    payload: Any = None


def _auth_from_token(
    realtime: Realtime, token: str | None, project: str, env: str
) -> tuple[dict[str, Any], str | None]:
    if not token:
        return dict(ANONYMOUS_POLICY_CONTEXT), None
    claims = verify_user_token(token, realtime.settings.jwt_master_secret, project=project, env=env)
    from pawabase_kit.principal import Principal

    principal = Principal("user", claims)
    return principal.as_policy_context(), principal.identity


def register_routes(app: SilloApp, realtime: Realtime) -> None:
    @app.ws_route("/realtime/v1/socket")
    async def socket(ws: WebSocketContext):
        context = ws.scope.get(SCOPE_KEY)
        if context is None:
            await ws.close(code=4001, reason="an API key is required")
            return
        try:
            auth, user_id = _auth_from_token(
                realtime, ws.query_params.get("token"), context.project, context.env
            )
        except TokenInvalid:
            await ws.close(code=4003, reason="invalid access token")
            return
        credential = {
            "is_service": context.is_service,
            "role": context.role,
            "scopes": list(context.scopes),
        }
        await ws.accept()
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in ws.scope.get("headers", [])}
        client = ws.scope.get("client")
        peer = Peer(ws, identity=user_id, idle_timeout=realtime.settings.idle_timeout)
        peer.start()
        connection = Connection(
            peer=peer,
            project=context.project,
            env=context.env,
            user_id=user_id,
            role=context.role,
            auth=auth,
            ip=headers.get("x-forwarded-for", client[0] if client else None),
            user_agent=headers.get("user-agent"),
        )
        realtime.register(connection)
        await peer.send({"type": "welcome", "connection_id": str(peer.id), "user_id": user_id})
        try:
            while True:
                raw = await ws.receive_text()
                connection.received += 1
                if len(raw.encode()) > realtime.settings.max_message_bytes:
                    await peer.send({"type": "error", "error": "message_too_large"})
                    continue
                try:
                    message = json.loads(raw)
                except ValueError:
                    await peer.send({"type": "error", "error": "invalid_json"})
                    continue
                if not isinstance(message, dict):
                    await peer.send({"type": "error", "error": "invalid_message"})
                    continue
                await handle(connection, message, credential)
        except Exception:
            pass
        finally:
            await realtime.disconnect(connection)

    async def handle(
        connection: Connection, message: dict[str, Any], credential: dict[str, Any]
    ) -> None:
        peer = connection.peer
        kind = message.get("type")
        ref = message.get("ref")
        channel = str(message.get("channel") or "")

        async def ack(ok: bool = True, **extra: Any) -> None:
            if ref is not None or not ok:
                await peer.send({"type": "ack", "ref": ref, "ok": ok, **extra})

        try:
            if kind == "ping":
                await peer.send({"type": "pong"})
            elif kind == "auth":
                try:
                    connection.auth, connection.user_id = _auth_from_token(
                        realtime, message.get("token"), connection.project, connection.env
                    )
                    peer.identity = connection.user_id
                    await ack(user_id=connection.user_id)
                except TokenInvalid:
                    await ack(False, error="invalid_token")
            elif kind == "subscribe":
                rule = await realtime.authorize(
                    connection.project,
                    connection.env,
                    channel,
                    "subscribe",
                    auth=connection.auth,
                    credential=credential,
                )
                await realtime.join(connection, channel)
                await ack(channel=channel)
                if message.get("since") is not None:
                    missed = await realtime.history(
                        connection.project, connection.env, channel, limit=rule.history
                    )
                    for item in missed:
                        if item["seq"] > int(message["since"]):
                            await peer.send(item)
                if rule.presence:
                    await peer.send(
                        {
                            "type": "presence_state",
                            "channel": channel,
                            "members": realtime.presence_members(
                                connection.project, connection.env, channel
                            ),
                        }
                    )
                    if isinstance(message.get("presence"), dict):
                        await realtime.track(connection, channel, message["presence"])
            elif kind == "unsubscribe":
                await realtime.leave(connection, channel)
                await ack(channel=channel)
            elif kind == "publish":
                if channel not in connection.channels and not credential.get("is_service"):
                    await realtime.authorize(
                        connection.project,
                        connection.env,
                        channel,
                        "subscribe",
                        auth=connection.auth,
                        credential=credential,
                    )
                payload = message.get("payload")
                await realtime.authorize(
                    connection.project,
                    connection.env,
                    channel,
                    "publish",
                    auth=connection.auth,
                    credential=credential,
                    payload=payload,
                )
                event = str(message.get("event") or "message")[:100]
                result = await realtime.publish(
                    connection.project,
                    connection.env,
                    channel,
                    event,
                    payload,
                    sender=connection.user_id,
                )
                await ack(id=result["id"])
                await forward_to_flows(connection, channel, event, payload)
            elif kind == "presence":
                if channel not in connection.channels:
                    raise PermissionError("subscribe to the channel first")
                await realtime.authorize(
                    connection.project,
                    connection.env,
                    channel,
                    "presence",
                    auth=connection.auth,
                    credential=credential,
                )
                await realtime.track(connection, channel, dict(message.get("meta") or {}))
                await ack()
            elif kind == "history":
                rule = await realtime.authorize(
                    connection.project,
                    connection.env,
                    channel,
                    "subscribe",
                    auth=connection.auth,
                    credential=credential,
                )
                limit = max(1, min(int(message.get("limit") or 50), rule.history))
                await peer.send(
                    {
                        "type": "history",
                        "ref": ref,
                        "channel": channel,
                        "messages": await realtime.history(
                            connection.project, connection.env, channel, limit
                        ),
                    }
                )
            else:
                await ack(False, error="unknown_type")
        except PermissionError as exc:
            realtime.errors.append(
                {
                    "project": connection.project,
                    "env": connection.env,
                    "channel": channel,
                    "type": kind,
                    "error": str(exc),
                }
            )
            await ack(False, error="forbidden", reason=str(exc))
        except HTTPException as exc:
            await ack(False, error="unavailable", reason=str(exc.detail))

    async def forward_to_flows(
        connection: Connection, channel: str, event: str, payload: Any
    ) -> None:
        """Client publications become ``realtime.message`` events flows can react to."""
        try:
            await realtime.api.post(
                "/internal/v1/events",
                json={
                    "project": connection.project,
                    "env": connection.env,
                    "name": "realtime.message",
                    "payload": {
                        "channel": channel,
                        "event": event,
                        "payload": payload,
                        "user_id": connection.user_id,
                    },
                    "actor": connection.user_id,
                },
            )
        except Exception as exc:
            realtime.errors.append(
                {
                    "project": connection.project,
                    "env": connection.env,
                    "channel": channel,
                    "type": "forward",
                    "error": str(exc),
                }
            )

    # ── HTTP ─────────────────────────────────────────────────────────────

    r = Router(prefix="/realtime/v1", tags=["realtime"])

    @r.post("/publish", request_model=PublishBody, summary="Publish to a channel")
    async def publish(ctx: HttpContext, body: PublishBody):
        context = require_context(ctx)
        credential = credential_context(ctx)
        auth = policy_auth(ctx.scope.get("user"))
        try:
            await realtime.authorize(
                context.project,
                context.env,
                body.channel,
                "publish",
                auth=auth,
                credential=credential,
                payload=body.payload,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return await realtime.publish(
            context.project,
            context.env,
            body.channel,
            body.event,
            body.payload,
            sender=auth.get("user_id"),
        )

    @r.get("/channels/{channel}/presence", summary="Who is present on a channel")
    async def presence(ctx: HttpContext, channel: str):
        context = require_context(ctx)
        try:
            await realtime.authorize(
                context.project,
                context.env,
                channel,
                "subscribe",
                auth=policy_auth(ctx.scope.get("user")),
                credential=credential_context(ctx),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "channel": channel,
            "members": realtime.presence_members(context.project, context.env, channel),
        }

    @r.get("/channels/{channel}/history", summary="Recent messages on a channel")
    async def history(ctx: HttpContext, channel: str):
        context = require_context(ctx)
        try:
            rule = await realtime.authorize(
                context.project,
                context.env,
                channel,
                "subscribe",
                auth=policy_auth(ctx.scope.get("user")),
                credential=credential_context(ctx),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        limit = max(1, min(int(ctx.query_params.get("limit", 50)), rule.history))
        return {
            "channel": channel,
            "messages": await realtime.history(context.project, context.env, channel, limit),
        }

    app.mount_router(r)

    # ── internal ─────────────────────────────────────────────────────────

    i = Router(prefix="/internal/v1", tags=["internal"], exclude_from_schema=True)

    @i.post("/publish", auth=SERVICE_ONLY, request_model=PublishBody)
    async def internal_publish(ctx: HttpContext, body: PublishBody):
        context = require_context(ctx)
        return await realtime.publish(
            context.project, context.env, body.channel, body.event, body.payload, sender="server"
        )

    @i.get("/realtime/summary", auth=SERVICE_ONLY)
    async def summary(ctx: HttpContext):
        return realtime.summary()

    @i.get("/realtime/{project}/{env}/channels", auth=SERVICE_ONLY)
    async def channels(ctx: HttpContext, project: str, env: str):
        return {"data": realtime.channels(project, env)}

    @i.get("/realtime/{project}/{env}/channels/{channel}", auth=SERVICE_ONLY)
    async def channel_detail(ctx: HttpContext, project: str, env: str, channel: str):
        from app.realtime import room_name

        room = room_name(project, env, channel)
        subscribers = [
            realtime.connections[str(peer.id)].describe()
            for peer in realtime.hub.members(room)
            if str(peer.id) in realtime.connections
        ]
        return {
            "channel": channel,
            "subscribers": subscribers,
            "presence": realtime.presence_members(project, env, channel),
            "history": await realtime.history(project, env, channel, 20),
        }

    @i.get("/realtime/{project}/{env}/connections", auth=SERVICE_ONLY)
    async def connections(ctx: HttpContext, project: str, env: str):
        return {
            "data": [
                c.describe()
                for c in realtime.connections.values()
                if c.project == project and c.env == env
            ]
        }

    @i.get("/realtime/{project}/{env}/activity", auth=SERVICE_ONLY)
    async def activity(ctx: HttpContext, project: str, env: str):
        return {
            "deliveries": [
                item
                for item in reversed(realtime.recent)
                if item["project"] == project and item["env"] == env
            ][:100],
            "errors": [
                item
                for item in reversed(realtime.errors)
                if item["project"] == project and item["env"] == env
            ][:100],
        }

    app.mount_router(i)
