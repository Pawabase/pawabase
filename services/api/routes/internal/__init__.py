"""Endpoints other Pawabase services call. Service tokens only.

The API is the authority on project configuration, so the gateway resolves
keys here, Akountz reads each environment's auth settings and sends mail
through here, and Angula reads realtime channel rules from here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, accepted
from sillo.auth.apikey import hash_api_key
from sillo.exceptions import HTTPException

from app.platform import Platform
from database.models import Environment, PolicyDef, ProjectKey
from pawabase_kit.service import SERVICE_ONLY


class ResolveBody(BaseModel):
    key: str = Field(min_length=8, max_length=512)
    project: str | None = None
    env: str | None = None


class MailBody(BaseModel):
    project: str
    env: str
    to: list[str]
    subject: str = ""
    text: str | None = None
    html: str | None = None
    template: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = "akountz"


class EventBody(BaseModel):
    project: str
    env: str
    name: str
    payload: Any = None
    actor: str | None = None


USAGE_WRITE_INTERVAL = timedelta(seconds=60)


def register(app: Any, platform: Platform) -> None:
    r = Router(prefix="/internal/v1", tags=["internal"], exclude_from_schema=True)

    @r.post("/keys/resolve", auth=SERVICE_ONLY, request_model=ResolveBody)
    async def resolve_key(ctx: HttpContext, body: ResolveBody):
        query = ProjectKey.filter(key_hash=hash_api_key(body.key)).select_related(
            "environment__project"
        )
        # When the caller names a project/environment, scope the lookup to it
        # directly (an indexed join, not a global hash scan) and reject a key
        # that resolves but belongs elsewhere with a distinct, useful message.
        if body.project or body.env:
            scoped = query
            if body.project:
                scoped = scoped.filter(environment__project__ref=body.project)
            if body.env:
                scoped = scoped.filter(environment__name=body.env)
            key = await scoped.first()
            if key is None and await query.first() is not None:
                raise HTTPException(
                    status_code=401,
                    detail=f"this key does not belong to project={body.project!r} env={body.env!r}",
                )
        else:
            key = await query.first()
        now = datetime.now(UTC)
        if (
            key is None
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at <= now)
        ):
            raise HTTPException(status_code=401, detail="invalid API key")
        if key.last_used_at is None or now - key.last_used_at > USAGE_WRITE_INTERVAL:
            await ProjectKey.filter(id=key.id).update(last_used_at=now, use_count=key.use_count + 1)
        environment = key.environment
        return {
            "project": environment.project.ref,
            "env": environment.name,
            "role": "service" if key.role == "secret" else "anon",
            "scopes": key.scopes or [],
            "key_id": str(key.id),
            "cors_origins": (environment.settings or {}).get("cors_origins", []),
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        }

    @r.get("/environments/{project}/{env}/auth", auth=SERVICE_ONLY)
    async def auth_config(ctx: HttpContext, project: str, env: str):
        state = await platform.state(project, env)
        config = dict(state.environment.auth or {})
        providers = {}
        for name, provider in (config.get("providers") or {}).items():
            providers[name] = {
                key: platform.resolve_value(state, value) for key, value in (provider or {}).items()
            }
        config["providers"] = providers
        return {
            "project": project,
            "env": env,
            "project_name": state.project_name,
            "auth": config,
            "public_url": platform.settings.public_url,
        }

    @r.get("/environments/{project}/{env}/realtime", auth=SERVICE_ONLY)
    async def realtime_config(ctx: HttpContext, project: str, env: str):
        state = await platform.state(project, env)
        environment = state.environment
        policies = await PolicyDef.filter(environment_id=environment.id)
        realtime = dict((environment.settings or {}).get("realtime") or {})
        channels = list(realtime.get("channels") or [])
        for resource in state.resources.values():
            if resource.realtime:
                # Resource channels follow the resource's own read policy.
                read = (resource.operations or {}).get("list") or {}
                channels.append(
                    {
                        "pattern": f"resource:{resource.name}",
                        "subscribe": read.get("policy") or "authenticated",
                        "publish": "service",
                        "presence": False,
                    }
                )
        return {
            "version": environment.version,
            "channels": channels,
            "allow_client_publish": realtime.get("allow_client_publish", True),
            "default_policy": realtime.get("default_policy", "authenticated"),
            "policies": {
                p.name: {"condition": p.condition, "description": p.description} for p in policies
            },
        }

    @r.post("/mail", auth=SERVICE_ONLY, request_model=MailBody)
    async def send_mail(ctx: HttpContext, body: MailBody):
        from app.jobs.mail import SendMailJob

        await platform.state(body.project, body.env)
        job_id = await platform.dispatch(
            SendMailJob,
            project=body.project,
            env=body.env,
            target=",".join(body.to),
            source=body.source,
            to=body.to,
            subject=body.subject,
            text=body.text,
            html=body.html,
            template=body.template,
            data=body.data,
        )
        return accepted({"job_id": job_id})

    @r.post("/events", auth=SERVICE_ONLY, request_model=EventBody)
    async def publish_event(ctx: HttpContext, body: EventBody):
        state = await platform.state(body.project, body.env)
        event_id = await platform.emit(state, body.name, body.payload, actor=body.actor)
        return accepted({"event_id": event_id})

    @r.get("/environments", auth=SERVICE_ONLY)
    async def environments(ctx: HttpContext):
        rows = await Environment.all().select_related("project")
        return {
            "data": [{"project": e.project.ref, "env": e.name, "version": e.version} for e in rows]
        }

    app.mount_router(r)
