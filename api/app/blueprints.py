"""Blueprints: a whole backend as one JSON document, for sharing systems.

A blueprint is exported from one environment and can only be used to **create a
new project**. It never updates a running system: there is no endpoint that
applies a blueprint to an existing project, so sharing a system can't overwrite
anyone's live definitions or data.

What a blueprint carries:

* every definition (schemas, transformers, policies, resources, mail templates,
  flows, routes, buckets, subscriptions, webhooks, inbound hooks, schedules);
* the environment's roles and their permissions (from Akountz);
* the non-sensitive parts of the auth settings and environment settings;
* optionally, sample data: up to ``max_rows`` records per resource.

What it never carries: API keys, secrets, webhook and inbound-hook signing
secrets, OAuth provider credentials, infrastructure (database, storage, mail)
settings, users and sessions.

Importing validates every definition with the same request models and
validators the Platform API uses, in dependency order, so a hand-edited or
malicious blueprint can't store anything the API itself would refuse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sillo.exceptions import HTTPException

from app.data.store import SqlError
from app.state import bump
from database.models import Environment, Project, Resource, WebhookEndpoint
from routes.platform.definitions import KINDS

if TYPE_CHECKING:
    from app.platform import Platform

FORMAT = "pawabase.blueprint"
VERSION = 1

#: Definitions are created in this order so references resolve: policies before
#: the resources and buckets that name them, schemas and flows before routes.
ORDER = (
    "schemas",
    "transformers",
    "policies",
    "resources",
    "mail-templates",
    "flows",
    "routes",
    "buckets",
    "subscriptions",
    "webhooks",
    "inbound-hooks",
    "schedules",
)
KIND_BY_PATH = {kind["path"]: kind for kind in KINDS}

#: Auth settings safe to share. Provider credentials, redirect URLs and the
#: site URL belong to the deployment, not the system.
AUTH_KEYS = (
    "signup_enabled",
    "require_email_verification",
    "password_policy",
    "password_min_length",
    "access_ttl",
    "refresh_ttl",
    "magic_link_enabled",
    "mfa_enabled",
    "default_roles",
    "emails",
)
#: Environment settings that describe a deployment rather than the system.
SETTINGS_SKIP = ("cors_origins",)

MAX_DEFINITIONS = 5000
MAX_ROWS_PER_RESOURCE = 5000


class Blueprint(BaseModel):
    """The document's shape. Extra top-level keys are ignored."""

    format: Literal["pawabase.blueprint"]
    version: int = Field(ge=1, le=VERSION)
    name: str = ""
    description: str = ""
    exported_at: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    definitions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    roles: list[dict[str, Any]] = Field(default_factory=list)
    auth: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class BlueprintError(Exception):
    """A blueprint that can't be applied, with where in the document."""

    def __init__(self, where: str, message: str) -> None:
        super().__init__(f"{where}: {message}")
        self.where = where
        self.message = message


# ── export ───────────────────────────────────────────────────────────────────


def _body(kind: dict[str, Any], item: Any) -> dict[str, Any]:
    """A stored definition in the shape its create request takes."""
    body: dict[str, Any] = {}
    for name in kind["body"].model_fields:
        if name == "secret":
            continue  # signing secrets stay with the environment that made them
        attribute = "fields_" if kind["model"] is Resource and name == "fields" else name
        if hasattr(item, attribute):
            body[name] = getattr(item, attribute)
    return body


async def export_environment(
    platform: Platform, environment: Environment, *, include_data: bool, max_rows: int
) -> dict[str, Any]:
    """Build the blueprint of one environment."""
    project: Project = environment.project
    definitions: dict[str, list[dict[str, Any]]] = {}
    for path in ORDER:
        kind = KIND_BY_PATH[path]
        rows = await kind["model"].filter(environment=environment).order_by("id")
        if rows:
            definitions[path] = [_body(kind, row) for row in rows]

    try:
        roles_page = await platform.akountz.get(
            f"/admin/v1/projects/{project.ref}/envs/{environment.name}/roles"
        )
        roles = [
            {
                "name": role["name"],
                "description": role.get("description", ""),
                "permissions": role.get("permissions", []),
            }
            for role in roles_page.get("data", [])
        ]
    except Exception:
        roles = []

    data: dict[str, list[dict[str, Any]]] = {}
    if include_data:
        state = await platform.state(project.ref, environment.name)
        limit = max(1, min(max_rows, MAX_ROWS_PER_RESOURCE))
        for resource in definitions.get("resources", []):
            try:
                store = await state.store(resource["name"])
                rows, _ = await store.list(filters=[], sort=[], limit=limit, offset=0)
            except SqlError:
                continue  # the table doesn't exist yet
            if rows:
                data[resource["name"]] = [dict(row) for row in rows]

    return {
        "format": FORMAT,
        "version": VERSION,
        "name": project.name,
        "description": project.description or "",
        "exported_at": datetime.now(UTC).isoformat(),
        "source": {"project": project.ref, "env": environment.name, "definitions_version": environment.version},
        "definitions": definitions,
        "roles": roles,
        "auth": {key: (environment.auth or {})[key] for key in AUTH_KEYS if key in (environment.auth or {})},
        "settings": {k: v for k, v in (environment.settings or {}).items() if k not in SETTINGS_SKIP},
        "data": data,
    }


# ── import ───────────────────────────────────────────────────────────────────


def parse(document: Any) -> Blueprint:
    """Check a blueprint's shape before anything is created."""
    try:
        blueprint = Blueprint.model_validate(document)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "blueprint"
        raise BlueprintError(where, first["msg"]) from exc
    unknown = set(blueprint.definitions) - set(ORDER)
    if unknown:
        raise BlueprintError("definitions", f"unknown kinds: {', '.join(sorted(unknown))}")
    if sum(len(items) for items in blueprint.definitions.values()) > MAX_DEFINITIONS:
        raise BlueprintError("definitions", f"more than {MAX_DEFINITIONS} definitions")
    for name, rows in blueprint.data.items():
        if len(rows) > MAX_ROWS_PER_RESOURCE:
            raise BlueprintError(f"data.{name}", f"more than {MAX_ROWS_PER_RESOURCE} rows")
    return blueprint


def summarise(blueprint: Blueprint) -> dict[str, Any]:
    return {
        "definitions": {path: len(items) for path, items in blueprint.definitions.items()},
        "roles": len(blueprint.roles),
        "data_rows": {name: len(rows) for name, rows in blueprint.data.items()},
    }


async def _create(
    platform: Platform, environment: Environment, path: str, index: int, item: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    kind = KIND_BY_PATH[path]
    label = item.get("name") or item.get("slug") or item.get("path") or index
    where = f"definitions.{path}[{index}] ({label})"
    try:
        body = kind["body"].model_validate(item)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        raise BlueprintError(where, f"{field}: {first['msg']}") from exc
    try:
        values = await kind["validate"](platform, environment.project.ref, environment.name, body)
    except HTTPException as exc:
        raise BlueprintError(where, str(exc.detail)) from exc
    reveal = values.pop("_reveal", {})
    if kind["model"] is WebhookEndpoint:
        # A shared system's webhooks point at its author's servers. Import them
        # switched off; the new owner reviews the URLs and turns them on.
        values["enabled"] = False
    natural = (
        {"slug": values["slug"]}
        if kind.get("key") == "slug"
        else (
            {"method": values["method"], "path": values["path"]}
            if path == "routes"
            else {"name": values["name"]}
        )
    )
    if await kind["model"].filter(environment=environment, **natural).exists():
        raise BlueprintError(where, "appears twice in the blueprint")
    item_row = await kind["model"].create(environment=environment, **values)
    await bump(environment.id)  # later validators read state that includes this item
    return item_row, reveal


async def apply(
    platform: Platform,
    project: Project,
    environments: list[Environment],
    blueprint: Blueprint,
) -> dict[str, Any]:
    """Build every environment of a **new** project from *blueprint*.

    Raises :class:`BlueprintError` on the first problem; the caller removes the
    half-built project.
    """
    report: dict[str, Any] = {
        **summarise(blueprint),
        "secrets": {},
        "warnings": [],
        "data_environment": environments[0].name if environments else None,
    }
    for environment in environments:
        environment.project = project
        environment.auth = {k: v for k, v in blueprint.auth.items() if k in AUTH_KEYS}
        environment.settings = {
            **(environment.settings or {}),
            **{k: v for k, v in blueprint.settings.items() if k not in SETTINGS_SKIP},
        }
        await environment.save(update_fields=["auth", "settings"])

        relations: dict[str, list[dict[str, Any]]] = {}
        for path in ORDER:
            for index, item in enumerate(blueprint.definitions.get(path, [])):
                item = dict(item)
                if path == "resources":
                    # Relations can point at resources that come later in the
                    # list, so resources are created first and linked after.
                    relations[item.get("name", "")] = item.pop("relations", []) or []
                row, reveal = await _create(platform, environment, path, index, item)
                if reveal:
                    key = getattr(row, "slug", None) or getattr(row, "name", str(index))
                    report["secrets"].setdefault(environment.name, {})[f"{path}:{key}"] = reveal
        for name, links in relations.items():
            if links:
                await Resource.filter(environment=environment, name=name).update(relations=links)
        await bump(environment.id)

        state = await platform.state(project.ref, environment.name)
        for resource in blueprint.definitions.get("resources", []):
            try:
                store = await state.store(resource["name"])
                await store.migrate()
            except SqlError as exc:
                raise BlueprintError(f"resources.{resource['name']}", f"migrate: {exc}") from exc

        for role in blueprint.roles:
            try:
                await platform.akountz.put(
                    f"/admin/v1/projects/{project.ref}/envs/{environment.name}/roles",
                    json={
                        "name": role.get("name"),
                        "description": role.get("description", ""),
                        "permissions": role.get("permissions", []),
                    },
                )
            except Exception as exc:
                raise BlueprintError(f"roles ({role.get('name')})", str(exc)) from exc

    if blueprint.definitions.get("webhooks"):
        report["warnings"].append(
            "Webhooks were imported switched off. Review their URLs, then enable them."
        )
    if blueprint.data and environments:
        await _load_data(platform, project, environments[0], blueprint)
        report["warnings"].append(
            f"Sample data was loaded into {environments[0].name} only; other environments start empty."
        )
    return report


async def _load_data(
    platform: Platform, project: Project, environment: Environment, blueprint: Blueprint
) -> None:
    """Insert sample rows as they were exported, ids included, without events."""
    state = await platform.state(project.ref, environment.name)
    for name, rows in blueprint.data.items():
        try:
            store = await state.store(name)
        except Exception as exc:
            raise BlueprintError(f"data.{name}", "no resource with that name") from exc
        for index, row in enumerate(rows):
            try:
                await store.create(row)
            except Exception as exc:
                raise BlueprintError(f"data.{name}[{index}]", str(exc)) from exc
        spec = store.spec
        if store.source.dialect == "postgres" and spec.id_type == "integer":
            # Rows kept their ids; move the sequence past them.
            table, key = spec.table, spec.primary_key
            await store.source.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{key}'), "
                f'COALESCE((SELECT MAX("{key}") FROM "{table}"), 1))',
                [],
            )
