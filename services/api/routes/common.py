"""Helpers shared by the management-plane routes."""

from __future__ import annotations

from typing import Any

from pawabase_kit.policies import PolicyGate
from sillo import HttpContext
from sillo.exceptions import HTTPException

from app.state import bump
from database.models import AuditEntry, Environment, Project

#: Studio (a service token acting for an operator), CLI operators and other
#: services. Project end users are refused, anonymous callers get a 401.
OPERATOR = PolicyGate({"any": [{"kind": "operator"}, {"kind": "service"}]}, schemes=None)

PROJECT_REF_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"
NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"


def actor(ctx: HttpContext) -> str | None:
    user = ctx.scope.get("user")
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    claims = getattr(user, "claims", {}) or {}
    return claims.get("email") or user.identity


async def get_project(ref: str) -> Project:
    project = await Project.get_or_none(ref=ref)
    if project is None:
        raise HTTPException(status_code=404, detail=f"no project {ref!r}")
    return project


async def get_environment(ref: str, env: str) -> Environment:
    environment = await Environment.filter(project__ref=ref, name=env).select_related("project").first()
    if environment is None:
        raise HTTPException(status_code=404, detail=f"no environment {ref}/{env}")
    return environment


async def audit(ctx: HttpContext, action: str, *, project: str | None = None, env: str | None = None, target: str = "", details: dict[str, Any] | None = None) -> None:
    await AuditEntry.create(project=project, env=env, actor=actor(ctx), action=action, target=target, details=details or {})


async def changed(ctx: HttpContext, environment: Environment, action: str, target: str, details: dict[str, Any] | None = None) -> None:
    """A definition changed: bump the version and audit it."""
    await bump(environment.id)
    await audit(ctx, action, project=environment.project.ref, env=environment.name, target=target, details=details)


def page_params(ctx: HttpContext, *, default: int = 50, maximum: int = 500) -> tuple[int, int]:
    try:
        limit = max(1, min(int(ctx.query_params.get("limit", default)), maximum))
        offset = max(0, int(ctx.query_params.get("offset", 0)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="limit and offset must be integers") from exc
    return limit, offset


def dump(instance: Any, *, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    """A model as JSON-ready data, without soft-delete bookkeeping."""
    if hasattr(instance, "_meta"):
        # Columns only: to_dict() also walks relations, which are lazy
        # awaitables on an instance whose relations were not fetched.
        data = {name: getattr(instance, name, None) for name in instance._meta.fields_db_projection}
    else:
        data = dict(instance)
    for key in ("deleted_at", *exclude):
        data.pop(key, None)
    if "fields_" in data:
        data["fields"] = data.pop("fields_")
    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()
        elif value is not None and type(value).__name__ == "UUID":
            data[key] = str(value)
    return data
