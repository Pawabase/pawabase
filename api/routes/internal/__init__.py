"""Endpoints other Pawabase services call. Service tokens only.

The API is the authority on the runtime's configuration, so the gateway resolves
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

from app import status as status_page
from app.platform import Platform
from database.models import ApiKey, Domain, Environment, FirewallRule, PolicyDef, StatusPage
from pawabase_core.service import SERVICE_ONLY


class ResolveBody(BaseModel):
    key: str = Field(min_length=8, max_length=512)
    env: str | None = None


class MailBody(BaseModel):
    env: str
    to: list[str]
    subject: str = ""
    text: str | None = None
    html: str | None = None
    template: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = "akountz"


class EventBody(BaseModel):
    env: str
    name: str
    payload: Any = None
    actor: str | None = None


USAGE_WRITE_INTERVAL = timedelta(seconds=60)


def register(app: Any, platform: Platform) -> None:
    r = Router(prefix="/internal/v1", tags=["internal"], exclude_from_schema=True)

    @r.post("/keys/resolve", auth=SERVICE_ONLY, request_model=ResolveBody)
    async def resolve_key(ctx: HttpContext, body: ResolveBody):
        query = ApiKey.filter(key_hash=hash_api_key(body.key)).select_related("environment")
        # A key belongs to one environment and names it. When the caller also
        # names one, hold the key to it and say so, rather than serving another
        # environment's data under a key meant for this one.
        key = await query.first()
        if key is not None and body.env and key.environment.name != body.env:
            raise HTTPException(
                status_code=401,
                detail=f"this key does not belong to environment {body.env!r}",
            )
        now = datetime.now(UTC)
        if (
            key is None
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at <= now)
        ):
            raise HTTPException(status_code=401, detail="invalid API key")
        if key.last_used_at is None or now - key.last_used_at > USAGE_WRITE_INTERVAL:
            await ApiKey.filter(id=key.id).update(last_used_at=now, use_count=key.use_count + 1)
        environment = key.environment
        return {
            "env": environment.name,
            "role": "service" if key.role == "secret" else "anon",
            "scopes": key.scopes or [],
            "allowed_ips": key.allowed_ips or [],
            "allowed_routes": key.allowed_routes or [],
            "key_id": str(key.id),
            "cors_origins": (environment.settings or {}).get("cors_origins", []),
            "ip_allowlist": (environment.settings or {}).get("ip_allowlist", []),
            "maintenance": (environment.settings or {}).get("maintenance") or {},
            "firewall": [
                {"id": r.id, "name": r.name, "action": r.action, "match": r.match or {}}
                for r in await FirewallRule.filter(env=environment.name, enabled=True)
            ],
            "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        }

    @r.get("/environments/{env}/auth", auth=SERVICE_ONLY)
    async def auth_config(ctx: HttpContext, env: str):
        state = await platform.state(env)
        config = dict(state.environment.auth or {})
        providers = {}
        for name, provider in (config.get("providers") or {}).items():
            providers[name] = {
                key: platform.resolve_value(state, value) for key, value in (provider or {}).items()
            }
        config["providers"] = providers
        return {
            "env": env,
            "project_name": platform.settings.project_name,
            "auth": config,
            "public_url": platform.settings.public_url,
        }

    @r.get("/environments/{env}/realtime", auth=SERVICE_ONLY)
    async def realtime_config(ctx: HttpContext, env: str):
        state = await platform.state(env)
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

        await platform.state(body.env)
        job_id = await platform.dispatch(
            SendMailJob,
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
        state = await platform.state(body.env)
        event_id = await platform.emit(state, body.name, body.payload, actor=body.actor)
        return accepted({"event_id": event_id})

    @r.get("/status/{env}", auth=SERVICE_ONLY)
    async def public_status(ctx: HttpContext, env: str):
        page = await status_page.build(env)
        if page is None:
            raise HTTPException(
                status_code=404, detail="this environment has no public status page"
            )
        return page

    @r.get("/status-default", auth=SERVICE_ONLY)
    async def default_status(ctx: HttpContext):
        default = await Environment.filter(is_default=True).first()
        page = await status_page.build(default.name) if default else None
        if page is None:
            raise HTTPException(status_code=404, detail="no public status page")
        return page

    @r.get("/status-enabled", auth=SERVICE_ONLY)
    async def status_enabled(ctx: HttpContext):
        return {"data": [row.env for row in await StatusPage.filter(enabled=True)]}

    @r.post("/status/{env}/samples", auth=SERVICE_ONLY)
    async def status_samples(ctx: HttpContext, env: str):
        body = await ctx.json or {}
        return {"recorded": await status_page.record(env, list(body.get("samples") or []))}

    @r.get("/domains", auth=SERVICE_ONLY)
    async def domains(ctx: HttpContext):
        rows = await Domain.filter(status="verified")
        return {"data": [{"hostname": d.hostname, "env": d.env} for d in rows]}

    @r.get("/environments", auth=SERVICE_ONLY)
    async def environments(ctx: HttpContext):
        rows = await Environment.all()
        return {"data": [{"env": e.name, "version": e.version} for e in rows]}

    app.mount_router(r)
