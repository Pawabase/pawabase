"""Projects, environments, API keys and secrets."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.auth.apikey import generate_api_key
from sillo.exceptions import HTTPException
from tortoise.transactions import in_transaction

from app.platform import Platform
from app.secrets import mask
from database.models import Environment, Project, ProjectKey, Secret
from routes.common import (
    NAME_PATTERN,
    OPERATOR,
    PROJECT_REF_PATTERN,
    audit,
    changed,
    dump,
    get_environment,
    get_project,
)

DEFAULT_ENVIRONMENTS = ["development", "production"]


class ProjectCreate(BaseModel):
    ref: str = Field(pattern=PROJECT_REF_PATTERN, description="Stable reference, e.g. acme")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    environments: list[str] = Field(default_factory=lambda: list(DEFAULT_ENVIRONMENTS))


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class EnvironmentCreate(BaseModel):
    name: str = Field(pattern=NAME_PATTERN)
    copy_from: str | None = Field(
        default=None, description="Copy definitions from this environment"
    )


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
    created_by: str | None = None,
) -> tuple[str, ProjectKey]:
    """Mint a key with Sillo's API-key generator; only the hash is stored."""
    full, _raw, digest = generate_api_key(prefix="pb_pk" if role == "publishable" else "pb_sk")
    key = await ProjectKey.create(
        environment=environment,
        name=name,
        role=role,
        prefix=full[:14],
        key_hash=digest,
        scopes=scopes or [],
        expires_at=expires_at,
        created_by=created_by,
    )
    return full, key


def key_view(key: ProjectKey) -> dict[str, Any]:
    data = dump(key, exclude=("key_hash",))
    data["active"] = key.revoked_at is None and (
        key.expires_at is None or key.expires_at > datetime.now(UTC)
    )
    return data


def environment_view(environment: Environment) -> dict[str, Any]:
    data = dump(environment)
    data["project"] = (
        environment.project.ref if hasattr(environment.project, "ref") else data.get("project_id")
    )
    return data


def register(r: Router, platform: Platform) -> None:

    # ── projects ─────────────────────────────────────────────────────────

    @r.get("/projects", auth=OPERATOR, tags=["projects"], summary="List projects")
    async def list_projects(ctx: HttpContext):
        projects = await Project.all().prefetch_related("environments")
        return {
            "data": [
                {**dump(p), "environments": [e.name for e in p.environments]} for p in projects
            ]
        }

    @r.post(
        "/projects",
        auth=OPERATOR,
        tags=["projects"],
        request_model=ProjectCreate,
        summary="Create a project",
    )
    async def create_project(ctx: HttpContext, body: ProjectCreate):
        if await Project.filter(ref=body.ref).exists():
            raise HTTPException(status_code=409, detail=f"project {body.ref!r} already exists")
        keys: dict[str, dict[str, str]] = {}
        async with in_transaction():
            project = await Project.create(
                ref=body.ref, name=body.name, description=body.description, created_by=_actor(ctx)
            )
            for index, name in enumerate(dict.fromkeys(body.environments or DEFAULT_ENVIRONMENTS)):
                if not re.match(NAME_PATTERN, name):
                    raise HTTPException(
                        status_code=422, detail=f"invalid environment name {name!r}"
                    )
                environment = await Environment.create(
                    project=project,
                    name=name,
                    is_default=index == 0,
                    settings={"public_docs": False},
                )
                environment.project = project
                publishable, _ = await create_key(
                    environment, "Default publishable key", "publishable", created_by=_actor(ctx)
                )
                secret, _ = await create_key(
                    environment, "Default secret key", "secret", created_by=_actor(ctx)
                )
                keys[name] = {"publishable": publishable, "secret": secret}
        await audit(ctx, "project.created", project=project.ref, target=project.ref)
        return created({**dump(project), "environments": list(keys), "keys": keys})

    @r.get("/projects/{ref}", auth=OPERATOR, tags=["projects"], summary="Get a project")
    async def get_project_view(ctx: HttpContext, ref: str):
        project = await get_project(ref)
        environments = await Environment.filter(project=project).select_related("project")
        return {**dump(project), "environments": [environment_view(e) for e in environments]}

    @r.patch(
        "/projects/{ref}",
        auth=OPERATOR,
        tags=["projects"],
        request_model=ProjectUpdate,
        summary="Update a project",
    )
    async def update_project(ctx: HttpContext, ref: str, body: ProjectUpdate):
        project = await get_project(ref)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        await project.save()
        await audit(ctx, "project.updated", project=ref, target=ref)
        return dump(project)

    @r.delete(
        "/projects/{ref}",
        auth=OPERATOR,
        tags=["projects"],
        summary="Delete a project and all its definitions",
    )
    async def delete_project(ctx: HttpContext, ref: str):
        project = await get_project(ref)
        await project.delete()
        platform.envs.forget(ref)
        await audit(ctx, "project.deleted", project=ref, target=ref)
        return no_content()

    @r.post(
        "/projects/{ref}/code/reload",
        auth=OPERATOR,
        tags=["projects"],
        summary="Reload the project's Python code",
    )
    async def reload_code(ctx: HttpContext, ref: str):
        await get_project(ref)
        loaded = platform.reload_code(ref)
        await audit(ctx, "code.reloaded", project=ref, target=ref)
        return {
            "modules": loaded.modules,
            "errors": loaded.errors,
            "router": loaded.router is not None,
        }

    # ── environments ─────────────────────────────────────────────────────

    @r.get("/projects/{ref}/envs", auth=OPERATOR, tags=["projects"], summary="List environments")
    async def list_environments(ctx: HttpContext, ref: str):
        project = await get_project(ref)
        environments = await Environment.filter(project=project).select_related("project")
        return {"data": [environment_view(e) for e in environments]}

    @r.post(
        "/projects/{ref}/envs",
        auth=OPERATOR,
        tags=["projects"],
        request_model=EnvironmentCreate,
        summary="Create an environment",
    )
    async def create_environment(ctx: HttpContext, ref: str, body: EnvironmentCreate):
        project = await get_project(ref)
        if await Environment.filter(project=project, name=body.name).exists():
            raise HTTPException(status_code=409, detail=f"environment {body.name!r} already exists")
        environment = await Environment.create(
            project=project, name=body.name, settings={"public_docs": False}
        )
        environment.project = project
        publishable, _ = await create_key(
            environment, "Default publishable key", "publishable", created_by=_actor(ctx)
        )
        secret, _ = await create_key(
            environment, "Default secret key", "secret", created_by=_actor(ctx)
        )
        copied = {}
        if body.copy_from:
            from routes.platform.promote import copy_definitions

            source = await get_environment(ref, body.copy_from)
            copied = await copy_definitions(source, environment)
        await audit(ctx, "environment.created", project=ref, env=body.name, target=body.name)
        return created(
            {
                **environment_view(environment),
                "keys": {"publishable": publishable, "secret": secret},
                "copied": copied,
            }
        )

    @r.get(
        "/projects/{ref}/envs/{env}",
        auth=OPERATOR,
        tags=["projects"],
        summary="Get an environment's configuration",
    )
    async def get_environment_view(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
        return environment_view(environment)

    @r.patch(
        "/projects/{ref}/envs/{env}",
        auth=OPERATOR,
        tags=["projects"],
        request_model=EnvironmentUpdate,
        summary="Update infrastructure, auth or settings",
    )
    async def update_environment(ctx: HttpContext, ref: str, env: str, body: EnvironmentUpdate):
        environment = await get_environment(ref, env)
        updates = body.model_dump(exclude_unset=True)
        for section in ("infra", "auth", "settings"):
            if section in updates and updates[section] is not None:
                merged = {**(getattr(environment, section) or {}), **updates[section]}
                setattr(environment, section, {k: v for k, v in merged.items() if v is not None})
        if updates.get("is_default"):
            await Environment.filter(project_id=environment.project_id).update(is_default=False)
            environment.is_default = True
        await environment.save()
        await changed(ctx, environment, "environment.updated", env, {"sections": sorted(updates)})
        return environment_view(environment)

    @r.delete(
        "/projects/{ref}/envs/{env}",
        auth=OPERATOR,
        tags=["projects"],
        summary="Delete an environment",
    )
    async def delete_environment(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
        if await Environment.filter(project_id=environment.project_id).count() <= 1:
            raise HTTPException(status_code=409, detail="a project keeps at least one environment")
        await environment.delete()
        platform.envs.forget(ref)
        await audit(ctx, "environment.deleted", project=ref, env=env, target=env)
        return no_content()

    @r.post(
        "/projects/{ref}/envs/{env}/promote",
        auth=OPERATOR,
        tags=["projects"],
        request_model=PromoteRequest,
        summary="Copy definitions to another environment",
    )
    async def promote(ctx: HttpContext, ref: str, env: str, body: PromoteRequest):
        from routes.platform.promote import copy_definitions

        source = await get_environment(ref, env)
        target = await get_environment(ref, body.to)
        copied = await copy_definitions(source, target, include=body.include)
        await changed(ctx, target, "environment.promoted", body.to, {"from": env, "copied": copied})
        return {"from": env, "to": body.to, "copied": copied}

    # ── keys ─────────────────────────────────────────────────────────────

    @r.get("/projects/{ref}/envs/{env}/keys", auth=OPERATOR, tags=["keys"], summary="List API keys")
    async def list_keys(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
        keys = await ProjectKey.filter(environment=environment).order_by("-created_at")
        return {"data": [key_view(k) for k in keys]}

    @r.post(
        "/projects/{ref}/envs/{env}/keys",
        auth=OPERATOR,
        tags=["keys"],
        request_model=KeyCreate,
        summary="Create an API key (shown once)",
    )
    async def create_key_route(ctx: HttpContext, ref: str, env: str, body: KeyCreate):
        environment = await get_environment(ref, env)
        full, key = await create_key(
            environment,
            body.name,
            body.role,
            scopes=body.scopes,
            expires_at=body.expires_at,
            created_by=_actor(ctx),
        )
        await audit(
            ctx,
            "key.created",
            project=ref,
            env=env,
            target=str(key.id),
            details={"role": body.role},
        )
        return created({**key_view(key), "key": full})

    @r.post(
        "/projects/{ref}/envs/{env}/keys/{key_id}/revoke",
        auth=OPERATOR,
        tags=["keys"],
        summary="Revoke an API key",
    )
    async def revoke_key(ctx: HttpContext, ref: str, env: str, key_id: str):
        environment = await get_environment(ref, env)
        key = await ProjectKey.get_or_none(id=key_id, environment=environment)
        if key is None:
            raise HTTPException(status_code=404, detail="no such key")
        key.revoked_at = datetime.now(UTC)
        await key.save(update_fields=["revoked_at"])
        await audit(ctx, "key.revoked", project=ref, env=env, target=key_id)
        return key_view(key)

    # ── secrets ──────────────────────────────────────────────────────────

    @r.get(
        "/projects/{ref}/envs/{env}/secrets",
        auth=OPERATOR,
        tags=["secrets"],
        summary="List secrets (values are never returned)",
    )
    async def list_secrets(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
        secrets = await Secret.filter(environment=environment)
        state = await platform.state(ref, env)
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
        "/projects/{ref}/envs/{env}/secrets/{name}",
        auth=OPERATOR,
        tags=["secrets"],
        request_model=SecretPut,
        summary="Set a secret",
    )
    async def put_secret(ctx: HttpContext, ref: str, env: str, name: str, body: SecretPut):
        if not SECRET_NAME.match(name):
            raise HTTPException(status_code=422, detail="secret names are UPPER_SNAKE_CASE")
        environment = await get_environment(ref, env)
        await Secret.update_or_create(
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
        "/projects/{ref}/envs/{env}/secrets/{name}",
        auth=OPERATOR,
        tags=["secrets"],
        summary="Delete a secret",
    )
    async def delete_secret(ctx: HttpContext, ref: str, env: str, name: str):
        environment = await get_environment(ref, env)
        deleted = await Secret.filter(environment=environment, name=name).delete()
        if not deleted:
            raise HTTPException(status_code=404, detail="no such secret")
        await changed(ctx, environment, "secret.deleted", name)
        return no_content()


def _actor(ctx: HttpContext) -> str | None:
    from routes.common import actor

    return actor(ctx)
