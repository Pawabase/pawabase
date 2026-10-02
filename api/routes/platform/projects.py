"""Projects, environments, API keys and secrets."""

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
from database.models import Environment, Organization, Project, ProjectKey, Secret
from pawabase_core.records import upsert
from routes.common import (
    NAME_PATTERN,
    OPERATOR,
    OPERATOR_ADMIN,
    PROJECT_REF_PATTERN,
    audit,
    changed,
    dump,
    get_environment,
    get_org,
    get_project,
    my_org_ids,
    operator_of,
)

DEFAULT_ENVIRONMENTS = ["development", "production"]


class ProjectCreate(BaseModel):
    ref: str = Field(pattern=PROJECT_REF_PATTERN, description="Stable reference, e.g. acme")
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    org: str | None = Field(
        default=None,
        description="The organization's slug. Required for operators; services without one "
        "create the project in the 'default' organization.",
    )
    environments: list[str] = Field(default_factory=lambda: list(DEFAULT_ENVIRONMENTS))
    blueprint: dict[str, Any] | None = Field(
        default=None,
        description="Build the new project from an exported blueprint. Only possible at creation.",
    )


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


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
        allowed_ips=allowed_ips or [],
        allowed_routes=allowed_routes or [],
        created_by=created_by,
    )
    return full, key


def key_view(key: ProjectKey) -> dict[str, Any]:
    data = dump(key, exclude=("key_hash",))
    data["active"] = key.revoked_at is None and (
        key.expires_at is None or key.expires_at > datetime.now(UTC)
    )
    return data


def project_view(project: Project, **extra: Any) -> dict[str, Any]:
    """A project as the management plane reports it, with its organization's slug."""
    org = project.organization if hasattr(project.organization, "slug") else None
    data = {**dump(project), "org": org.slug if org else None, **extra}
    if "environments" not in extra and hasattr(project, "environments"):
        try:
            data["environments"] = [e.name for e in project.environments]
        except Exception:
            pass
    return data


async def resolve_org(ctx: HttpContext, slug: str | None) -> Organization:
    """The organization a new project goes into."""
    if slug:
        return await get_org(ctx, slug, "developer")
    if operator_of(ctx) is not None:
        raise HTTPException(
            status_code=422, detail="choose the organization the project belongs to (org)"
        )
    org, _ = await Organization.get_or_create(
        slug="default", defaults={"name": "Default", "created_by": None}
    )
    return org


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
        query = Project.all()
        if slug := ctx.query_params.get("org"):
            org = await get_org(ctx, slug)
            query = query.filter(organization=org)
        elif (mine := await my_org_ids(ctx)) is not None:
            query = query.filter(organization_id__in=mine)
        projects = await query.prefetch_related("environments", "organization")
        return {"data": [project_view(p) for p in projects]}

    @r.post(
        "/projects",
        auth=OPERATOR,
        tags=["projects"],
        request_model=ProjectCreate,
        summary="Create a project",
    )
    async def create_project(ctx: HttpContext, body: ProjectCreate):
        org = await resolve_org(ctx, body.org)
        if await Project.filter(ref=body.ref).exists():
            raise HTTPException(status_code=409, detail=f"project {body.ref!r} already exists")
        blueprint = None
        if body.blueprint is not None:
            try:
                blueprint = blueprints.parse(body.blueprint)
            except blueprints.BlueprintError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"message": "this is not a valid blueprint", "where": exc.where, "problem": exc.message},
                ) from exc
        keys: dict[str, dict[str, str]] = {}
        environments: list[Environment] = []
        async with in_transaction():
            project = await Project.create(
                ref=body.ref,
                name=body.name,
                description=body.description,
                created_by=_actor(ctx),
                organization=org,
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
                environments.append(environment)
                publishable, _ = await create_key(
                    environment, "Default publishable key", "publishable", created_by=_actor(ctx)
                )
                secret, _ = await create_key(
                    environment, "Default secret key", "secret", created_by=_actor(ctx)
                )
                keys[name] = {"publishable": publishable, "secret": secret}
        report = None
        if blueprint is not None:
            try:
                report = await blueprints.apply(platform, project, environments, blueprint)
            except blueprints.BlueprintError as exc:
                # A half-built project is worse than none: remove it and say where it failed.
                await project.delete()
                platform.envs.forget(project.ref)
                raise HTTPException(
                    status_code=422,
                    detail={"message": "the blueprint could not be applied", "where": exc.where, "problem": exc.message},
                ) from exc
        await audit(
            ctx,
            "project.created",
            project=project.ref,
            org=org.slug,
            target=project.ref,
            details={"blueprint": blueprints.summarise(blueprint)} if blueprint else None,
        )
        return created(
            {
                **dump(project),
                "org": org.slug,
                "environments": list(keys),
                "keys": keys,
                "blueprint": report,
            }
        )

    @r.get("/projects/{ref}", auth=OPERATOR, tags=["projects"], summary="Get a project")
    async def get_project_view(ctx: HttpContext, ref: str):
        project = await get_project(ref)
        await project.fetch_related("organization")
        environments = await Environment.filter(project=project).select_related("project")
        return project_view(project, environments=[environment_view(e) for e in environments])

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
        auth=OPERATOR_ADMIN,
        tags=["projects"],
        summary="Delete a project and all its definitions",
    )
    async def delete_project(ctx: HttpContext, ref: str):
        project = await get_project(ref)
        await project.fetch_related("organization")
        await audit(ctx, "project.deleted", project=ref, target=ref)
        await project.delete()
        platform.envs.forget(ref)
        return no_content()

    @r.get(
        "/projects/{ref}/envs/{env}/blueprint",
        auth=OPERATOR,
        tags=["projects"],
        summary="Export the environment as a blueprint (definitions, roles, optional sample data)",
    )
    async def export_blueprint(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
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
            "project.blueprint_exported",
            project=ref,
            env=env,
            target=ref,
            details={"data": include_data, **blueprints.summarise(blueprints.parse(document))},
        )
        return document

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

    @r.post(
        "/projects/{ref}/envs/{env}/previews",
        auth=OPERATOR,
        tags=["previews"],
        request_model=PreviewCreate,
        summary="Create an expiring deploy-preview environment",
    )
    async def create_preview(ctx: HttpContext, ref: str, env: str, body: PreviewCreate):
        """Clone definitions into an isolated, automatically-expiring environment."""
        source = await get_environment(ref, env)
        name = body.name or f"pr-{body.ref}"
        if await Environment.filter(project_id=source.project_id, name=name).exists():
            raise HTTPException(status_code=409, detail=f"environment {name!r} already exists")
        settings = {**(source.settings or {}), **(body.settings or {}), "public_docs": False}
        preview = await Environment.create(
            project_id=source.project_id,
            name=name,
            infra=body.infra,
            auth=body.auth,
            settings=settings,
            preview_source=source.name,
            preview_expires_at=datetime.now(UTC) + timedelta(hours=body.expires_in_hours),
        )
        preview.project = source.project
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
            ctx, "preview.created", project=ref, env=name, target=name,
            details={"source": env, "expires_at": preview.preview_expires_at.isoformat(), "copied": copied},
        )
        return created({**environment_view(preview), "keys": {"publishable": publishable, "secret": secret}, "copied": copied})

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

    @r.delete(
        "/projects/{ref}/envs/{env}/previews/{preview}",
        auth=OPERATOR,
        tags=["previews"],
        summary="Delete a deploy-preview environment",
    )
    async def delete_preview(ctx: HttpContext, ref: str, env: str, preview: str):
        source = await get_environment(ref, env)
        target = await get_environment(ref, preview)
        if target.project_id != source.project_id or target.preview_source != source.name:
            raise HTTPException(status_code=404, detail="no such deploy preview")
        await target.delete()
        platform.envs.forget(ref)
        await audit(ctx, "preview.deleted", project=ref, env=preview, target=preview)
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
            allowed_ips=body.allowed_ips,
            allowed_routes=body.allowed_routes,
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
