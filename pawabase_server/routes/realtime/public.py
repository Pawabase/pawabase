"""Realtime over HTTP: publish from a server, and read a channel's presence and history."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, SilloApp
from sillo.exceptions import HTTPException

from pawabase_server.core.context import require_context
from pawabase_server.core.principal import policy_auth
from pawabase_server.policies import credential_context
from pawabase_server.realtime import PayloadTooLarge, Realtime, check_event


class PublishBody(BaseModel):
    channel: str = Field(min_length=1, max_length=200)
    event: str = Field(default="message", min_length=1, max_length=100)
    payload: Any = None


def refusal(exc: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(exc))


def too_large(exc: PayloadTooLarge) -> HTTPException:
    return HTTPException(status_code=413, detail=str(exc))


def limit_from(ctx: HttpContext, default: int = 50) -> int:
    """The ``limit`` query parameter, as a positive number; anything else is the default rather than an error."""
    try:
        return max(1, int(ctx.query_params.get("limit", default)))
    except (TypeError, ValueError):
        return default


def register(app: SilloApp, realtime: Realtime) -> None:
    router = Router(prefix="/realtime/v1", tags=["realtime"])

    @router.post("/publish", request_model=PublishBody, summary="Publish to a channel")
    async def publish(ctx: HttpContext, body: PublishBody):
        context = require_context(ctx)
        auth = policy_auth(ctx.scope.get("user"))
        try:
            await realtime.authorize(
                context.env,
                body.channel,
                "publish",
                auth=auth,
                credential=credential_context(ctx),
                payload=body.payload,
            )
            event = check_event(body.event)
        except PermissionError as exc:
            raise refusal(exc) from exc
        except PayloadTooLarge as exc:
            raise too_large(exc) from exc
        return await realtime.publish(
            context.env, body.channel, event, body.payload, sender=auth.get("user_id")
        )

    @router.get("/channels/{channel}/presence", summary="Who is present on a channel")
    async def presence(ctx: HttpContext, channel: str):
        context = require_context(ctx)
        try:
            await realtime.authorize(
                context.env,
                channel,
                "subscribe",
                auth=policy_auth(ctx.scope.get("user")),
                credential=credential_context(ctx),
            )
        except PermissionError as exc:
            raise refusal(exc) from exc
        return {"channel": channel, "members": realtime.presence_members(context.env, channel)}

    @router.get("/channels/{channel}/history", summary="Recent messages on a channel")
    async def history(ctx: HttpContext, channel: str):
        context = require_context(ctx)
        try:
            rule = await realtime.authorize(
                context.env,
                channel,
                "subscribe",
                auth=policy_auth(ctx.scope.get("user")),
                credential=credential_context(ctx),
            )
        except PermissionError as exc:
            raise refusal(exc) from exc
        limit = max(1, min(limit_from(ctx), rule.history or 1))
        return {
            "channel": channel,
            "messages": await realtime.history(
                context.env,
                channel,
                limit,
                auth=policy_auth(ctx.scope.get("user")),
                credential=credential_context(ctx),
            ),
        }

    app.mount_router(router)
