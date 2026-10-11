"""What the management plane and Studio ask of realtime: publish on a project's behalf, and inspect what is connected. Service tokens only."""

from __future__ import annotations

from sillo import HttpContext, Router, SilloApp
from sillo.exceptions import HTTPException

from pawabase_server.core.context import require_context
from pawabase_server.core.service import SERVICE_ONLY
from pawabase_server.realtime import PayloadTooLarge, Realtime, room_name
from pawabase_server.routes.realtime.public import PublishBody, too_large


def register(app: SilloApp, realtime: Realtime) -> None:
    router = Router(prefix="/internal/v1", tags=["internal"], exclude_from_schema=True)

    @router.post("/publish", auth=SERVICE_ONLY, request_model=PublishBody)
    async def publish(ctx: HttpContext, body: PublishBody):
        context = require_context(ctx)
        try:
            return await realtime.publish(
                context.env, body.channel, body.event, body.payload, sender="server"
            )
        except PayloadTooLarge as exc:
            raise too_large(exc) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/realtime/summary", auth=SERVICE_ONLY)
    async def summary(ctx: HttpContext):
        return realtime.summary()

    @router.get("/realtime/{env}/summary", auth=SERVICE_ONLY)
    async def environment_summary(ctx: HttpContext, env: str):
        return realtime.environment_summary(env)

    @router.get("/realtime/{env}/channels", auth=SERVICE_ONLY)
    async def channels(ctx: HttpContext, env: str):
        return {"data": realtime.channels(env)}

    @router.get("/realtime/{env}/channels/{channel}", auth=SERVICE_ONLY)
    async def channel_detail(ctx: HttpContext, env: str, channel: str):
        room = room_name(env, channel)
        subscribers = [
            realtime.connections[str(peer.id)].describe()
            for peer in realtime.hub.members(room)
            if str(peer.id) in realtime.connections
        ]
        return {
            "channel": channel,
            "subscribers": subscribers,
            "presence": realtime.presence_members(env, channel),
            "history": await realtime.history(env, channel, 20),
        }

    @router.get("/realtime/{env}/connections", auth=SERVICE_ONLY)
    async def connections(ctx: HttpContext, env: str):
        return {"data": [c.describe() for c in realtime.connections.values() if c.env == env]}

    @router.delete("/realtime/{env}/connections/{connection_id}", auth=SERVICE_ONLY)
    async def disconnect(ctx: HttpContext, env: str, connection_id: str):
        connection = realtime.connections.get(connection_id)
        if connection is None or connection.env != env:
            raise HTTPException(status_code=404, detail="no such connection")
        await connection.peer.close()
        await realtime.disconnect(connection)
        return {"closed": connection_id}

    @router.post("/realtime/{env}/refresh", auth=SERVICE_ONLY)
    async def refresh(ctx: HttpContext, env: str):
        """Drop the cached rules for an environment and re-check every subscription against what the API says now."""
        realtime.store.invalidate(env)
        await realtime.store.get(env)
        revoked = 0
        for connection in [c for c in realtime.connections.values() if c.env == env]:
            revoked += await realtime.revalidate(connection)
        return {"env": env, "revoked": revoked}

    @router.get("/realtime/{env}/activity", auth=SERVICE_ONLY)
    async def activity(ctx: HttpContext, env: str):
        return {
            "deliveries": [item for item in reversed(realtime.recent) if item["env"] == env][:100],
            "errors": [item for item in reversed(realtime.errors) if item["env"] == env][:100],
        }

    app.mount_router(router)
