"""What Studio asks of the public entry point: its counters and its limit usage. Service tokens only."""

from __future__ import annotations

from sillo import HttpContext, SilloApp

from pawabase_server.core.service import SERVICE_ONLY
from pawabase_server.db.models import Project
from pawabase_server.settings import Settings


def register(app: SilloApp, settings: Settings) -> None:
    @app.get("/internal/v1/gateway/stats", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def stats(ctx: HttpContext):
        edge, resolver = app.state["edge"], app.state["key_resolver"]
        return {
            **edge.stats,
            "key_lookups": resolver.lookups,
            "key_stale_served": resolver.stale_served,
            "latency": edge.latency.snapshot(),
            "custom_domains": len(edge.domains),
        }

    @app.get("/internal/v1/gateway/usage", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def usage(ctx: HttpContext):
        edge, quota = app.state["edge"], app.state["request_quota"]
        project_id = ctx.query_params.get("project")
        project_usage = await edge.env_limits.usage()
        if project_id:
            project = await Project.get_or_none(id=project_id)
            if project is None:
                return {"project": None}
            return {
                "project": {
                    "id": project.id,
                    "limits": project.limits or {},
                    "limits_version": project.limits_version,
                    "usage": project_usage.get(project.id, {}),
                }
            }
        return {
            **await quota.usage(),
            "projects": project_usage,
            "connections": {
                "active": edge.admission.active_connections(),
                "limit": settings.max_active_connections,
            },
        }
