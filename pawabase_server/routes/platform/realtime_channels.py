"""Studio's CRUD surface for durable/real channel rules: the DB-backed sibling of the hand-edited
``Environment.settings["realtime"]["channels"]`` JSON list (see ``RealtimeChannelConfig``). Unlike the
``definitions.py`` kinds, these rows are not branch-versioned — a channel rule is operational configuration,
checked on every publish/subscribe, not something you'd want to leave stranded on a feature branch.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from pawabase_server.db.models import RealtimeChannelConfig
from pawabase_server.platform import Platform
from pawabase_server.routes.common import MANAGE, audit, get_environment

Kind = Literal["public", "private", "presence", "protected", "ephemeral", "durable"]


class ChannelBody(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    kind: Kind = "public"
    subscribe: str = Field(default="authenticated", max_length=64)
    publish: str = Field(default="authenticated", max_length=64)
    presence: bool = True
    history: int = Field(default=50, ge=0, le=1000)
    message_size_limit: int = Field(default=0, ge=0)
    publish_rate_limit: int = Field(default=0, ge=0)
    subscription_limit: int = Field(default=0, ge=0)
    retention_seconds: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=300)


class ChannelPatch(BaseModel):
    kind: Kind | None = None
    subscribe: str | None = Field(default=None, max_length=64)
    publish: str | None = Field(default=None, max_length=64)
    presence: bool | None = None
    history: int | None = Field(default=None, ge=0, le=1000)
    message_size_limit: int | None = Field(default=None, ge=0)
    publish_rate_limit: int | None = Field(default=None, ge=0)
    subscription_limit: int | None = Field(default=None, ge=0)
    retention_seconds: int | None = Field(default=None, ge=0)
    description: str | None = Field(default=None, max_length=300)


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}/realtime-channels"

    def reload(env: str) -> None:
        realtime = platform.app.state.get("realtime") if platform.app is not None else None
        if realtime is not None:
            realtime.store.invalidate(env)

    async def row_of(env: str, channel_id: str) -> RealtimeChannelConfig:
        environment = await get_environment(env)
        row = await RealtimeChannelConfig.get_or_none(id=channel_id, environment=environment)
        if row is None:
            raise HTTPException(status_code=404, detail="no such channel rule")
        return row

    @r.get(base, auth=MANAGE, tags=["realtime"], summary="Durable channel rules, as real records")
    async def list_channels(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        rows = await RealtimeChannelConfig.filter(environment=environment)
        return {"data": [row.describe() for row in rows]}

    @r.post(base, auth=MANAGE, tags=["realtime"], request_model=ChannelBody, summary="Add a channel rule")
    async def add_channel(ctx: HttpContext, env: str, body: ChannelBody):
        environment = await get_environment(env)
        if await RealtimeChannelConfig.get_or_none(environment=environment, pattern=body.pattern):
            raise HTTPException(status_code=409, detail=f"a rule for {body.pattern!r} already exists")
        row = await RealtimeChannelConfig.create(environment=environment, **body.model_dump())
        reload(env)
        await audit(ctx, "realtime.channel_added", env=env, target=row.id, details={"pattern": row.pattern})
        return created(row.describe())

    @r.patch(
        f"{base}/{{channel_id}}",
        auth=MANAGE,
        tags=["realtime"],
        request_model=ChannelPatch,
        summary="Change a channel rule",
    )
    async def patch_channel(ctx: HttpContext, env: str, channel_id: str, body: ChannelPatch):
        row = await row_of(env, channel_id)
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        await row.save()
        reload(env)
        await audit(ctx, "realtime.channel_changed", env=env, target=row.id, details={"pattern": row.pattern})
        return row.describe()

    @r.delete(f"{base}/{{channel_id}}", auth=MANAGE, tags=["realtime"], summary="Delete a channel rule")
    async def delete_channel(ctx: HttpContext, env: str, channel_id: str):
        row = await row_of(env, channel_id)
        pattern = row.pattern
        await row.delete()
        reload(env)
        await audit(ctx, "realtime.channel_removed", env=env, target=channel_id, details={"pattern": pattern})
        return no_content()
