"""Studio's side of the public status page: its settings, and the incidents written for it."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app import status as status_page
from app.platform import Platform
from database.models import StatusIncident, StatusPage
from routes.common import MANAGE, audit, dump, get_environment


class Component(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: Literal["gateway", "api", "auth", "realtime", "workers", "manual"] = "manual"
    description: str = Field(default="", max_length=200)


class PageBody(BaseModel):
    enabled: bool = False
    title: str = Field(default="Status", min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    contact_url: str = Field(default="", max_length=500)
    components: list[Component] = Field(default_factory=list, max_length=30)


class IncidentBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    status: Literal["investigating", "identified", "monitoring", "resolved"] = "investigating"
    impact: Literal["none", "minor", "major", "critical"] = "minor"
    components: list[str] = Field(default_factory=list, max_length=30)
    message: str = Field(default="", max_length=2000)


class UpdateBody(BaseModel):
    status: Literal["investigating", "identified", "monitoring", "resolved"]
    message: str = Field(min_length=1, max_length=2000)
    impact: Literal["none", "minor", "major", "critical"] | None = None


def _incident(row: StatusIncident) -> dict[str, Any]:
    return dump(row)


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    @r.get(
        f"{base}/status-page",
        auth=MANAGE,
        tags=["status"],
        summary="The public status page's settings and what it shows now",
    )
    async def get_page(ctx: HttpContext, env: str):
        await get_environment(env)
        page = await StatusPage.get_or_none(env=env)
        settings = (
            dump(page)
            if page
            else {
                "env": env,
                "enabled": False,
                "title": "Status",
                "description": "",
                "contact_url": "",
                "components": status_page.DEFAULT_COMPONENTS,
            }
        )
        if page and not settings["components"]:
            settings["components"] = status_page.DEFAULT_COMPONENTS
        preview = await status_page.build(env, include_disabled=True) if page else None
        return {"settings": settings, "preview": preview, "sources": list(status_page.SOURCES)}

    @r.put(
        f"{base}/status-page",
        auth=MANAGE,
        tags=["status"],
        request_model=PageBody,
        summary="Save the status page's settings",
    )
    async def put_page(ctx: HttpContext, env: str, body: PageBody):
        await get_environment(env)
        values = {**body.model_dump(), "components": [c.model_dump() for c in body.components]}
        page = await StatusPage.get_or_none(env=env)
        if page is None:
            page = await StatusPage.create(env=env, **values)
        else:
            for key, value in values.items():
                setattr(page, key, value)
            await page.save()
        await audit(
            ctx, "status_page.saved", env=env, target="status", details={"enabled": body.enabled}
        )
        return dump(page)

    @r.get(
        f"{base}/incidents",
        auth=MANAGE,
        tags=["status"],
        summary="Incidents written for the status page",
    )
    async def incidents(ctx: HttpContext, env: str):
        await get_environment(env)
        return {
            "data": [
                _incident(row)
                for row in await StatusIncident.filter(env=env).order_by("-id").limit(100)
            ]
        }

    @r.post(
        f"{base}/incidents",
        auth=MANAGE,
        tags=["status"],
        request_model=IncidentBody,
        summary="Open an incident",
    )
    async def open_incident(ctx: HttpContext, env: str, body: IncidentBody):
        await get_environment(env)
        now = datetime.now(UTC).isoformat()
        row = await StatusIncident.create(
            env=env,
            title=body.title,
            status=body.status,
            impact=body.impact,
            components=body.components,
            updates=[
                {
                    "at": now,
                    "status": body.status,
                    "message": body.message or "We are looking into this.",
                }
            ],
            resolved_at=datetime.now(UTC) if body.status == "resolved" else None,
        )
        await audit(ctx, "incident.opened", env=env, target=row.id, details={"title": body.title})
        return created(_incident(row))

    @r.post(
        f"{base}/incidents/{{incident_id}}/updates",
        auth=MANAGE,
        tags=["status"],
        request_model=UpdateBody,
        summary="Post an update, and resolve if it says so",
    )
    async def post_update(ctx: HttpContext, env: str, incident_id: str, body: UpdateBody):
        row = await StatusIncident.get_or_none(id=incident_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such incident")
        row.updates = [
            *(row.updates or []),
            {"at": datetime.now(UTC).isoformat(), "status": body.status, "message": body.message},
        ]
        row.status = body.status
        if body.impact:
            row.impact = body.impact
        row.resolved_at = datetime.now(UTC) if body.status == "resolved" else None
        await row.save()
        await audit(
            ctx, "incident.updated", env=env, target=row.id, details={"status": body.status}
        )
        return _incident(row)

    @r.delete(
        f"{base}/incidents/{{incident_id}}",
        auth=MANAGE,
        tags=["status"],
        summary="Delete an incident",
    )
    async def delete_incident(ctx: HttpContext, env: str, incident_id: str):
        row = await StatusIncident.get_or_none(id=incident_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such incident")
        await row.delete()
        await audit(ctx, "incident.deleted", env=env, target=incident_id)
        return no_content()
