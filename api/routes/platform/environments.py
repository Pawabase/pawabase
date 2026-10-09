"""This runtime, its environments, API keys and secrets."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sillo import HttpContext, Router, created, no_content
from sillo.auth.apikey import generate_api_key
from sillo.exceptions import HTTPException
from tortoise.transactions import in_transaction

from app import blueprints, env_settings
from app.jobs.environments import PurgeEnvironmentJob
from app.platform import Platform
from app.secrets import mask
from database.models import ApiKey, Environment, Secret
from pawabase_core import envvars
from pawabase_core.records import upsert
from routes.common import (
    MANAGE,
    NAME_PATTERN,
    actor,
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
            if method.upper() not in {
                "*",
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
            } or not path.startswith("/"):
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
    max_keys: int = 0,
) -> tuple[str, ApiKey]:
    """Mint a key with Sillo's API-key generator; only the hash is stored."""
    if (
        max_keys
        and await ApiKey.filter(environment=environment, revoked_at=None).count() >= max_keys
    ):
        raise HTTPException(
            status_code=403, detail="the environment API key limit has been reached"
        )
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


def check_infra(infra: dict[str, Any] | None) -> None:
    """Refuse the one ``infra`` setting that is no longer read: mail.

    ``database_url`` and ``storage`` still work, deprecated. Mail comes from environment
    variables only, so storing it here would be silently ignored. ``null`` is allowed, to
    clear what an older install left behind.
    """
    if infra and infra.get("mail") is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "infra.mail is no longer supported: configure mail with PAWABASE_MAIL_* "
                "(or <ENVIRONMENT>_MAIL_*) environment variables"
            ),
        )


def deprecations(environment: Environment) -> list[str]:
    """What an environment still keeps in ``infra``, and where it belongs now."""
    prefix = envvars.prefix_for(environment.name)
    infra = environment.infra or {}
    notes = []
    if infra.get("database_url"):
        notes.append(f"infra.database_url is deprecated: set {prefix}_DATA_URL")
    if infra.get("storage"):
        notes.append(f"infra.storage is deprecated: set {prefix}_STORAGE_* variables")
    if infra.get("mail"):
        notes.append(f"infra.mail is ignored: set PAWABASE_MAIL_* or {prefix}_MAIL_* variables")
    return notes


async def check_environment_name(name: str, also: Iterable[str] = ()) -> None:
    """Refuse a name whose ``<NAME>_*`` variables would be read by another environment."""
    existing = await Environment.all().values_list("name", flat=True)
    problem = envvars.name_problem(name, [*existing, *also])
    if problem:
        raise HTTPException(status_code=422, detail=problem)


def environment_view(environment: Environment) -> dict[str, Any]:
    return {**dump(environment), "deprecations": deprecations(environment)}


def register(r: Router, platform: Platform) -> None:

    async def require_environment_capacity(extra: int = 1) -> None:
        maximum = platform.settings.max_environments
        if maximum and await Environment.all().count() + extra > maximum:
            raise HTTPException(
                status_code=403, detail="the installation environment limit has been reached"
            )

    def key_limit(env: str) -> int:
        return platform.limit(
            env, "MAX_API_KEYS_PER_ENVIRONMENT", platform.settings.max_api_keys_per_environment
        )

    # ── this runtime ─────────────────────────────────────────────────────

    @r.get("/runtime", auth=MANAGE, tags=["runtime"], summary="This runtime and its environments")
    async def runtime(ctx: HttpContext):
        environments = await Environment.all()
        return {
            "name": platform.settings.project_name,
            "app_env": platform.settings.app_env,
            "environments": [environment_view(e) for e in environments],
        }

    @r.get(
        "/usage",
        auth=MANAGE,
        tags=["runtime"],
        summary="Configured runtime limits and management-plane usage",
    )
    async def usage(ctx: HttpContext):
        env = ctx.query_params.get("env") or None
        environments = await Environment.all()
        # With an environment named, the limits are the ones it actually runs under: its
        # own `<ENV>_*` variables first, the deployment-wide ones otherwise.
        keys = 0
        if env:
            environment = await get_environment(env)
            keys = await ApiKey.filter(environment=environment, revoked_at=None).count()
        return {
            "environments": {
                "used": len(environments),
                "limit": platform.settings.max_environments,
            },
            "api_keys": {
                "used": keys,
                "limit": key_limit(env) if env else platform.settings.max_api_keys_per_environment,
            },
            "uploads": {
                "limit": platform.limit(env, "MAX_UPLOAD_BYTES", platform.settings.max_upload_bytes)
                if env
                else platform.settings.max_upload_bytes
            },
            "users": {
                "limit": platform.limit(env, "MAX_USERS", platform.settings.max_users)
                if env
                else platform.settings.max_users
            },
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
                detail={
                    "message": "this is not a valid blueprint",
                    "where": exc.where,
                    "problem": exc.message,
                },
            ) from exc
        names = list(dict.fromkeys(body.environments or DEFAULT_ENVIRONMENTS))
        await require_environment_capacity(len(names))
        for name in names:
            if not re.match(NAME_PATTERN, name):
                raise HTTPException(status_code=422, detail=f"invalid environment name {name!r}")
        for name in names:
            await check_environment_name(name, also=names)
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
                    environment,
                    "Default publishable key",
                    "publishable",
                    created_by=_actor(ctx),
                    max_keys=key_limit(environment.name),
                )
                secret, _ = await create_key(
                    environment,
                    "Default secret key",
                    "secret",
                    created_by=_actor(ctx),
                    max_keys=key_limit(environment.name),
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
                detail={
                    "message": "the blueprint could not be applied",
                    "where": exc.where,
                    "problem": exc.message,
                },
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
        await require_environment_capacity()
        if await Environment.filter(name=body.name).exists():
            raise HTTPException(status_code=409, detail=f"environment {body.name!r} already exists")
        await check_environment_name(body.name)
        environment = await Environment.create(name=body.name, settings={"public_docs": False})
        publishable, _ = await create_key(
            environment,
            "Default publishable key",
            "publishable",
            created_by=_actor(ctx),
            max_keys=key_limit(environment.name),
        )
        secret, _ = await create_key(
            environment,
            "Default secret key",
            "secret",
            created_by=_actor(ctx),
            max_keys=key_limit(environment.name),
        )
        copied = {}
        if body.copy_from:
            from routes.platform.promote import copy_definitions

            source = await get_environment(body.copy_from)
            copied = await copy_definitions(source, environment, box=platform.box)
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
        await require_environment_capacity()
        name = body.name or f"pr-{body.ref}"
        if await Environment.filter(name=name).exists():
            raise HTTPException(status_code=409, detail=f"environment {name!r} already exists")
        await check_environment_name(name)
        check_infra(body.infra)
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
            preview,
            "Preview publishable key",
            "publishable",
            expires_at=preview.preview_expires_at,
            created_by=_actor(ctx),
            max_keys=key_limit(preview.name),
        )
        secret, _ = await create_key(
            preview,
            "Preview secret key",
            "secret",
            expires_at=preview.preview_expires_at,
            created_by=_actor(ctx),
            max_keys=key_limit(preview.name),
        )
        from routes.platform.promote import copy_definitions

        copied = await copy_definitions(source, preview, box=platform.box)
        await audit(
            ctx,
            "preview.created",
            env=name,
            target=name,
            details={
                "source": env,
                "expires_at": preview.preview_expires_at.isoformat(),
                "copied": copied,
            },
        )
        return created(
            {
                **environment_view(preview),
                "keys": {"publishable": publishable, "secret": secret},
                "copied": copied,
            }
        )

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
        check_infra(updates.get("infra"))
        if updates.get("settings") is not None:
            updates["settings"] = env_settings.validate(updates["settings"])
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
        if (environment.settings or {}).get("deletion_pending"):
            raise HTTPException(
                status_code=409, detail="this environment is already queued for deletion"
            )

        # Keep the environment in place until the worker has removed every
        # external concern (identity records, resource tables and objects).
        # This makes the operation durable and avoids a request timeout
        # leaving orphaned data behind.
        environment.settings = {**(environment.settings or {}), "deletion_pending": True}
        await environment.save()
        job_id = await platform.dispatch(
            PurgeEnvironmentJob,
            env=env,
            target=env,
            source="environment.delete",
            requested_by=actor(ctx),
        )
        await audit(
            ctx, "environment.deletion_queued", env=env, target=env, details={"job_id": job_id}
        )
        return {"queued": True, "job_id": job_id}

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
        copied = await copy_definitions(source, target, include=body.include, box=platform.box)
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
            max_keys=key_limit(environment.name),
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
                "ciphertext": platform.box.seal(body.value, env),
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
