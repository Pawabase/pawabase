"""This runtime, its environments, API keys and secrets."""

from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sillo import HttpContext, Router, created, no_content
from sillo.auth.apikey import generate_api_key
from sillo.exceptions import HTTPException
from tortoise.transactions import in_transaction

from app import blueprints
from app.platform import Platform
from app.secrets import mask
from database.models import ApiKey, Environment, Secret
from pawabase_core.records import upsert
from routes.common import (
    MANAGE,
    NAME_PATTERN,
    audit,
    changed,
    dump,
    get_environment,
)

DEFAULT_ENVIRONMENTS = ["development", "production"]


class BlueprintApply(BaseModel):
    blueprint: dict[str, Any] = Field(description="An exported blueprint.")
    environments: list[str] = Field(
        default_factory=lambda: list(DEFAULT_ENVIRONMENTS),
        description="The new environments to build from it. Each must not exist yet.",
    )


class EnvironmentCreate(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    copy_from: str | None = Field(
        default=None, description="Copy definitions from this environment"
    )


class PreviewCreate(BaseModel):
    name: str | None = Field(default=None, pattern=NAME_PATTERN)
    ref: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9][a-z0-9-]*$")
    expires_in_hours: int = Field(default=72, ge=1, le=720)
    infra: dict[str, Any] = Field(default_factory=dict)
    auth: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] | None = None


class EnvironmentUpdate(BaseModel):
    infra: dict[str, Any] | None = None
    auth: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    is_default: bool | None = None


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: Literal["publishable", "secret"] = "publishable"
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    allowed_ips: list[str] = Field(default_factory=list, description="Client IPs or CIDR ranges")
    allowed_routes: list[str] = Field(
        default_factory=list,
        description="Gateway route rules such as 'GET /rest/v1/orders' or '/functions/v1/*'",
    )

    @field_validator("allowed_ips")
    @classmethod
    def valid_ip_ranges(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid IP address or CIDR: {value}") from exc
        return values

    @field_validator("allowed_routes")
    @classmethod
    def valid_route_rules(cls, values: list[str]) -> list[str]:
        for value in values:
            parts = value.split(None, 1)
            method, path = (parts[0], parts[1]) if len(parts) == 2 else ("*", parts[0])
            if method.upper() not in {"*", "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} or not path.startswith("/"):
                raise ValueError("route rules must be '/path/*' or 'METHOD /path/*'")
        return values


class SecretPut(BaseModel):
    value: str = Field(min_length=1, max_length=65536)
    description: str = ""


class PromoteRequest(BaseModel):
    to: str = Field(pattern=NAME_PATTERN)
    include: list[str] | None = Field(
        default=None, description="Definition kinds to copy; all when omitted"
    )


SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


async def create_key(
    environment: Environment,
    name: str,
    role: str,
    *,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
    allowed_ips: list[str] | None = None,
    allowed_routes: list[str] | None = None,
    created_by: str | None = None,
) -> tuple[str, ApiKey]:
    """Mint a key with Sillo's API-key generator; only the hash is stored."""
    full, _raw, digest = generate_api_key(prefix="pb_pk" if role == "publishable" else "pb_sk")
    key = await ApiKey.create(
        environment=environment,
        name=name,
        role=role,
        prefix=full[:14],
        key_hash=digest,
        scopes=scopes or [],
        expires_at=expires_at,
        allowed_ips=allowed_ips or [],
        allowed_routes=allowed_routes or [],
        created_by=created_by,
    )
    return full, key


def key_view(key: ApiKey) -> dict[str, Any]:
    data = dump(key, exclude=("key_hash",))
    data["active"] = key.revoked_at is None and (
        key.expires_at is None or key.expires_at > datetime.now(UTC)
    )
    return data


def environment_view(environment: Environment) -> dict[str, Any]:
    return dump(environment)


def register(r: Router, platform: Platform) -> None:

    # ── this runtime ─────────────────────────────────────────────────────

    @r.get("/runtime", auth=MANAGE, tags=["runtime"], summary="This runtime and its environments")
    async def runtime(ctx: HttpContext):
        environments = await Environment.all()
        return {
            "name": platform.settings.project_name,
            "app_env": platform.settings.app_env,
            "environments": [environment_view(e) for e in environments],
        }

    @r.post(
        "/blueprints/apply",
        auth=MANAGE,
        tags=["runtime"],
        request_model=BlueprintApply,
        summary="Create new environments from an exported blueprint",
    )
    async def apply_blueprint(ctx: HttpContext, body: BlueprintApply):
        try:
            blueprint = blueprints.parse(body.blueprint)
        except blueprints.BlueprintError as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": "this is not a valid blueprint", "where": exc.where, "problem": exc.message},
            ) from exc
        names = list(dict.fromkeys(body.environments or DEFAULT_ENVIRONMENTS))
        for name in names:
            if not re.match(NAME_PATTERN, name):
                raise HTTPException(status_code=422, detail=f"invalid environment name {name!r}")
        taken = [name for name in names if await Environment.filter(name=name).exists()]
        if taken:
            raise HTTPException(
                status_code=409,
                detail=f"environment {taken[0]!r} already exists; a blueprint only creates new environments",
            )
        keys: dict[str, dict[str, str]] = {}
        environments: list[Environment] = []
        async with in_transaction():
            for name in names:
                environment = await Environment.create(name=name, settings={"public_docs": False})
                environments.append(environment)
                publishable, _ = await create_key(
                    environment, "Default publishable key", "publishable", created_by=_actor(ctx)
                )
                secret, _ = await create_key(
                    environment, "Default secret key", "secret", created_by=_actor(ctx)
                )
                keys[name] = {"publishable": publishable, "secret": secret}
        try:
            report = await blueprints.apply(platform, environments, blueprint)
        except blueprints.BlueprintError as exc:
            # Half-built environments are worse than none: remove them and say where it failed.
            for environment in environments:
                await environment.delete()
                platform.envs.forget(environment.name)
            raise HTTPException(
                status_code=422,
                detail={"message": "the blueprint could not be applied", "where": exc.where, "problem": exc.message},
            ) from exc
        await audit(
            ctx,
            "blueprint.applied",
            target=", ".join(names),
            details={"blueprint": blueprints.summarise(blueprint)},
        )
        return created({"environments": names, "keys": keys, "blueprint": report})

    @r.get(
        "/envs/{env}/blueprint",
        auth=MANAGE,
        tags=["environments"],
        summary="Export the environment as a blueprint (definitions, roles, optional sample data)",
    )
    async def export_blueprint(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        try:
            include_data = ctx.query_params.get("data", "false").lower() in ("1", "true", "yes")
            max_rows = int(ctx.query_params.get("max_rows", 200))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="max_rows must be an integer") from exc
        document = await blueprints.export_environment(
            platform, environment, include_data=include_data, max_rows=max_rows
        )
        await audit(
            ctx,
            "blueprint.exported",
            env=env,
            target=env,
            details={"data": include_data, **blueprints.summarise(blueprints.parse(document))},
        )
        return document

    @r.post(
        "/code/reload",
        auth=MANAGE,
        tags=["environments"],
        summary="Reload the mounted Python code",
    )
    async def reload_code(ctx: HttpContext):
        loaded = platform.reload_code()
        await audit(ctx, "code.reloaded", target="code")
        return {
            "modules": loaded.modules,
            "errors": loaded.errors,
            "router": loaded.router is not None,
        }

    # ── environments ─────────────────────────────────────────────────────

    @r.get("/envs", auth=MANAGE, tags=["environments"], summary="List environments")
    async def list_environments(ctx: HttpContext):
        environments = await Environment.all()
        return {"data": [environment_view(e) for e in environments]}

    @r.post(
        "/envs",
        auth=MANAGE,
        tags=["environments"],
        request_model=EnvironmentCreate,
        summary="Create an environment",
    )
    async def create_environment(ctx: HttpContext, body: EnvironmentCreate):
        if await Environment.filter(name=body.name).exists():
            raise HTTPException(status_code=409, detail=f"environment {body.name!r} already exists")
        environment = await Environment.create(name=body.name, settings={"public_docs": False})
        publishable, _ = await create_key(
            environment, "Default publishable key", "publishable", created_by=_actor(ctx)
        )
        secret, _ = await create_key(
            environment, "Default secret key", "secret", created_by=_actor(ctx)
        )
        copied = {}
        if body.copy_from:
            from routes.platform.promote import copy_definitions

            source = await get_environment(body.copy_from)
            copied = await copy_definitions(source, environment)
        await audit(ctx, "environment.created", env=body.name, target=body.name)
        return created(
            {
                **environment_view(environment),
                "keys": {"publishable": publishable, "secret": secret},
                "copied": copied,
            }
        )

    @r.post(
        "/envs/{env}/previews",
        auth=MANAGE,
        tags=["previews"],
        request_model=PreviewCreate,
        summary="Create an expiring deploy-preview environment",
    )
    async def create_preview(ctx: HttpContext, env: str, body: PreviewCreate):
        """Clone definitions into an isolated, automatically-expiring environment."""
        source = await get_environment(env)
        name = body.name or f"pr-{body.ref}"
        if await Environment.filter(name=name).exists():
            raise HTTPException(status_code=409, detail=f"environment {name!r} already exists")
        settings = {**(source.settings or {}), **(body.settings or {}), "public_docs": False}
        preview = await Environment.create(
            name=name,
            infra=body.infra,
            auth=body.auth,
            settings=settings,
            preview_source=source.name,
            preview_expires_at=datetime.now(UTC) + timedelta(hours=body.expires_in_hours),
        )
        publishable, _ = await create_key(
            preview, "Preview publishable key", "publishable", expires_at=preview.preview_expires_at,
            created_by=_actor(ctx),
        )
        secret, _ = await create_key(
            preview, "Preview secret key", "secret", expires_at=preview.preview_expires_at,
            created_by=_actor(ctx),
        )
        from routes.platform.promote import copy_definitions

        copied = await copy_definitions(source, preview)
        await audit(
            ctx, "preview.created", env=name, target=name,
            details={"source": env, "expires_at": preview.preview_expires_at.isoformat(), "copied": copied},
        )
        return created({**environment_view(preview), "keys": {"publishable": publishable, "secret": secret}, "copied": copied})

    @r.get(
        "/envs/{env}",
        auth=MANAGE,
        tags=["environments"],
        summary="Get an environment's configuration",
    )
    async def get_environment_view(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        return environment_view(environment)

    @r.patch(
        "/envs/{env}",
        auth=MANAGE,
        tags=["environments"],
        request_model=EnvironmentUpdate,
        summary="Update infrastructure, auth or settings",
    )
    async def update_environment(ctx: HttpContext, env: str, body: EnvironmentUpdate):
        environment = await get_environment(env)
        updates = body.model_dump(exclude_unset=True)
        for section in ("infra", "auth", "settings"):
            if section in updates and updates[section] is not None:
                merged = {**(getattr(environment, section) or {}), **updates[section]}
                setattr(environment, section, {k: v for k, v in merged.items() if v is not None})
        if updates.get("is_default"):
            await Environment.all().update(is_default=False)
            environment.is_default = True
        await environment.save()
        await changed(ctx, environment, "environment.updated", env, {"sections": sorted(updates)})
        return environment_view(environment)

    @r.delete(
        "/envs/{env}",
        auth=MANAGE,
        tags=["environments"],
        summary="Delete an environment",
    )
    async def delete_environment(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        if await Environment.all().count() <= 1:
            raise HTTPException(status_code=409, detail="a runtime keeps at least one environment")
        await environment.delete()
        platform.envs.forget(env)
        await audit(ctx, "environment.deleted", env=env, target=env)
        return no_content()

    @r.delete(
        "/envs/{env}/previews/{preview}",
        auth=MANAGE,
        tags=["previews"],
        summary="Delete a deploy-preview environment",
    )
    async def delete_preview(ctx: HttpContext, env: str, preview: str):
        source = await get_environment(env)
        target = await get_environment(preview)
        if target.preview_source != source.name:
            raise HTTPException(status_code=404, detail="no such deploy preview")
        await target.delete()
        platform.envs.forget(preview)
        await audit(ctx, "preview.deleted", env=preview, target=preview)
        return no_content()

    @r.post(
        "/envs/{env}/promote",
        auth=MANAGE,
        tags=["environments"],
        request_model=PromoteRequest,
        summary="Copy definitions to another environment",
    )
    async def promote(ctx: HttpContext, env: str, body: PromoteRequest):
        from routes.platform.promote import copy_definitions

        source = await get_environment(env)
        target = await get_environment(body.to)
        copied = await copy_definitions(source, target, include=body.include)
        await changed(ctx, target, "environment.promoted", body.to, {"from": env, "copied": copied})
        return {"from": env, "to": body.to, "copied": copied}

    # ── keys ─────────────────────────────────────────────────────────────

    @r.get("/envs/{env}/keys", auth=MANAGE, tags=["keys"], summary="List API keys")
    async def list_keys(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        keys = await ApiKey.filter(environment=environment).order_by("-created_at")
        return {"data": [key_view(k) for k in keys]}

    @r.post(
        "/envs/{env}/keys",
        auth=MANAGE,
        tags=["keys"],
        request_model=KeyCreate,
        summary="Create an API key (shown once)",
    )
    async def create_key_route(ctx: HttpContext, env: str, body: KeyCreate):
        environment = await get_environment(env)
        full, key = await create_key(
            environment,
            body.name,
            body.role,
            scopes=body.scopes,
            expires_at=body.expires_at,
            allowed_ips=body.allowed_ips,
            allowed_routes=body.allowed_routes,
            created_by=_actor(ctx),
        )
        await audit(
            ctx,
            "key.created",
            env=env,
            target=str(key.id),
            details={"role": body.role},
        )
        return created({**key_view(key), "key": full})

    @r.post(
        "/envs/{env}/keys/{key_id}/revoke",
        auth=MANAGE,
        tags=["keys"],
        summary="Revoke an API key",
    )
    async def revoke_key(ctx: HttpContext, env: str, key_id: str):
        environment = await get_environment(env)
        key = await ApiKey.get_or_none(id=key_id, environment=environment)
        if key is None:
            raise HTTPException(status_code=404, detail="no such key")
        key.revoked_at = datetime.now(UTC)
        await key.save(update_fields=["revoked_at"])
        await audit(ctx, "key.revoked", env=env, target=key_id)
        return key_view(key)

    # ── secrets ──────────────────────────────────────────────────────────

    @r.get(
        "/envs/{env}/secrets",
        auth=MANAGE,
        tags=["secrets"],
        summary="List secrets (values are never returned)",
    )
    async def list_secrets(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        secrets = await Secret.filter(environment=environment)
        state = await platform.state(env)
        return {
            "data": [
                {
                    "name": s.name,
                    "description": s.description,
                    "preview": mask(state.secret_values.get(s.name)),
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    "updated_by": s.updated_by,
                }
                for s in secrets
            ]
        }

    @r.put(
        "/envs/{env}/secrets/{name}",
        auth=MANAGE,
        tags=["secrets"],
        request_model=SecretPut,
        summary="Set a secret",
    )
    async def put_secret(ctx: HttpContext, env: str, name: str, body: SecretPut):
        if not SECRET_NAME.match(name):
            raise HTTPException(status_code=422, detail="secret names are UPPER_SNAKE_CASE")
        environment = await get_environment(env)
        await upsert(
            Secret,
            environment=environment,
            name=name,
            defaults={
                "ciphertext": platform.box.seal(body.value),
                "description": body.description,
                "updated_by": _actor(ctx),
            },
        )
        await changed(ctx, environment, "secret.set", name)
        return {"name": name, "reference": f"secret://{name}"}

    @r.delete(
        "/envs/{env}/secrets/{name}",
        auth=MANAGE,
        tags=["secrets"],
        summary="Delete a secret",
    )
    async def delete_secret(ctx: HttpContext, env: str, name: str):
        environment = await get_environment(env)
        deleted = await Secret.filter(environment=environment, name=name).delete()
        if not deleted:
            raise HTTPException(status_code=404, detail="no such secret")
        await changed(ctx, environment, "secret.deleted", name)
        return no_content()


def _actor(ctx: HttpContext) -> str | None:
    from routes.common import actor

    return actor(ctx)
