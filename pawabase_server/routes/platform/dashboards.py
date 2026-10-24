"""Studio's CRUD for dashboards, and publishing one: pasting in a publishable key once, sealed and stored,
never returned. See ``pawabase_server.dashboards.engine`` for what a definition must look like, and
``pawabase_server.routes.public.dashboards`` for how a published one is served.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from pawabase_server.dashboards.engine import validate_dashboard
from pawabase_server.db.models import Dashboard
from pawabase_server.platform import Platform
from pawabase_server.routes.common import MANAGE, audit, get_environment

NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{1,62}$"


class DashboardBody(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    title: str = Field(default="", max_length=160)
    description: str = ""
    definition: dict[str, Any] = Field(default_factory=lambda: {"pages": []})
    enabled: bool = True


class DashboardPatch(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    description: str | None = None
    definition: dict[str, Any] | None = None
    enabled: bool | None = None


class PublishBody(BaseModel):
    public: bool
    #: The environment's own publishable key, pasted in once. Required when turning *public* on and no key
    #: has been sealed yet; omit to keep the one already stored (e.g. just flipping *public* off and on).
    publishable_key: str | None = Field(default=None, min_length=8)


def _validated(definition: dict[str, Any]) -> dict[str, Any]:
    problems = validate_dashboard(definition)
    if problems:
        raise HTTPException(
            status_code=422,
            detail={"message": "the dashboard cannot be published", "problems": problems},
        )
    return definition


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}/dashboards"

    async def row_of(env: str, dashboard_id: str) -> Dashboard:
        environment = await get_environment(env)
        row = await Dashboard.get_or_none(id=dashboard_id, environment=environment)
        if row is None:
            raise HTTPException(status_code=404, detail="no such dashboard")
        return row

    @r.get(base, auth=MANAGE, tags=["dashboards"], summary="Dashboards in this environment")
    async def list_dashboards(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        rows = await Dashboard.filter(environment=environment)
        return {"data": [row.describe() for row in rows]}

    @r.post(
        base,
        auth=MANAGE,
        tags=["dashboards"],
        request_model=DashboardBody,
        summary="Create a dashboard",
    )
    async def add_dashboard(ctx: HttpContext, env: str, body: DashboardBody):
        environment = await get_environment(env)
        if await Dashboard.get_or_none(environment=environment, name=body.name):
            raise HTTPException(
                status_code=409, detail=f"a dashboard named {body.name!r} already exists"
            )
        problems = validate_dashboard(body.definition)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={"message": "the dashboard is invalid", "problems": problems},
            )
        row = await Dashboard.create(
            environment=environment,
            name=body.name,
            title=body.title,
            description=body.description,
            definition=body.definition,
            enabled=body.enabled,
        )
        await audit(ctx, "dashboards.created", env=env, target=row.id, details={"name": row.name})
        return created(row.describe())

    @r.get(
        f"{base}/{{dashboard_id}}",
        auth=MANAGE,
        tags=["dashboards"],
        summary="A dashboard's full definition",
    )
    async def get_dashboard(ctx: HttpContext, env: str, dashboard_id: str):
        return (await row_of(env, dashboard_id)).describe()

    @r.patch(
        f"{base}/{{dashboard_id}}",
        auth=MANAGE,
        tags=["dashboards"],
        request_model=DashboardPatch,
        summary="Change a dashboard",
    )
    async def patch_dashboard(ctx: HttpContext, env: str, dashboard_id: str, body: DashboardPatch):
        row = await row_of(env, dashboard_id)
        changes = body.model_dump(exclude_unset=True)
        if "definition" in changes and changes["definition"] is not None:
            problems = validate_dashboard(changes["definition"])
            if problems:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "the dashboard is invalid", "problems": problems},
                )
        for key, value in changes.items():
            setattr(row, key, value)
        await row.save()
        await audit(ctx, "dashboards.changed", env=env, target=row.id, details={"name": row.name})
        return row.describe()

    @r.delete(
        f"{base}/{{dashboard_id}}", auth=MANAGE, tags=["dashboards"], summary="Delete a dashboard"
    )
    async def delete_dashboard(ctx: HttpContext, env: str, dashboard_id: str):
        row = await row_of(env, dashboard_id)
        name = row.name
        await row.delete()
        await audit(ctx, "dashboards.deleted", env=env, target=dashboard_id, details={"name": name})
        return no_content()

    @r.put(
        f"{base}/{{dashboard_id}}/publish",
        auth=MANAGE,
        tags=["dashboards"],
        request_model=PublishBody,
        summary="Publish or unpublish a dashboard",
    )
    async def publish_dashboard(ctx: HttpContext, env: str, dashboard_id: str, body: PublishBody):
        row = await row_of(env, dashboard_id)
        if body.public:
            _validated(row.definition)
            if body.publishable_key:
                row.publishable_key_ciphertext = platform.box.seal(body.publishable_key, env)
            elif not row.publishable_key_ciphertext:
                raise HTTPException(
                    status_code=422,
                    detail="paste in the environment's publishable key the first time you publish",
                )
        row.public = body.public
        await row.save()
        await audit(
            ctx,
            "dashboards.published" if body.public else "dashboards.unpublished",
            env=env,
            target=row.id,
            details={"name": row.name},
        )
        return row.describe()
