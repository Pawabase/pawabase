"""Helpers shared by the management-plane routes."""

from __future__ import annotations

from typing import Any

from sillo import HttpContext
from sillo.exceptions import HTTPException

from app.state import bump
from database.models import ORG_ROLES, AuditEntry, Environment, Organization, OrgMember, Project
from pawabase_kit.policies import PolicyGate

#: Studio (a service token acting for an operator), CLI operators and other
#: services. Project end users are refused, anonymous callers get a 401.
class OperatorGate(PolicyGate):
    """The management-plane gate, and the one place organization access is enforced.

    Every route under ``/projects/{ref}`` passes through here, so a route can
    not forget to check that the operator's organization owns the project.
    """

    def __init__(self, policy: Any, *, need: str | None = None, **kwargs: Any) -> None:
        super().__init__(policy, **kwargs)
        self.need = need

    async def authenticate(self, ctx: HttpContext) -> bool:
        await super().authenticate(ctx)
        ref = (ctx.path_params or {}).get("ref")
        if ref is not None:
            await authorize_project(ctx, str(ref), self.need)
        return True


_OPERATORS = {"any": [{"kind": "operator"}, {"kind": "service"}]}
OPERATOR = OperatorGate(_OPERATORS, schemes=None)
#: For actions that change who or what a project is: deleting it.
OPERATOR_ADMIN = OperatorGate(_OPERATORS, schemes=None, need="admin")

PROJECT_REF_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"
NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"


def actor(ctx: HttpContext) -> str | None:
    user = ctx.scope.get("user")
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    claims = getattr(user, "claims", {}) or {}
    return claims.get("email") or user.identity


ROLE_RANK = {role: rank for rank, role in enumerate(ORG_ROLES)}
READ_METHODS = ("GET", "HEAD", "OPTIONS")


def operator_of(ctx: HttpContext) -> tuple[str, str | None] | None:
    """``(user id, email)`` of the signed-in operator; ``None`` for services.

    A service credential (another Pawabase service, no operator named) is not
    bound to an organization and may reach every one.
    """
    user = ctx.scope.get("user")
    if user is None or getattr(user, "kind", None) != "operator":
        return None
    return user.identity, user.email


async def membership(ctx: HttpContext, org: Organization) -> OrgMember | None:
    operator = operator_of(ctx)
    if operator is None:
        return None
    return await OrgMember.get_or_none(organization=org, user_id=operator[0])


async def require_role(ctx: HttpContext, org: Organization, need: str = "viewer") -> str:
    """The caller's role in *org*, refusing anyone below *need*.

    Operators who are not members get a 404, so an organization's existence is
    not disclosed to outsiders.
    """
    if operator_of(ctx) is None:
        return "owner"
    member = await membership(ctx, org)
    if member is None:
        raise HTTPException(status_code=404, detail=f"no organization {org.slug!r}")
    if ROLE_RANK.get(member.role, -1) < ROLE_RANK[need]:
        raise HTTPException(status_code=403, detail=f"this needs the {need} role or higher")
    return member.role


async def get_org(ctx: HttpContext, slug: str, need: str = "viewer") -> Organization:
    org = await Organization.get_or_none(slug=slug)
    if org is None or (operator_of(ctx) is not None and await membership(ctx, org) is None):
        raise HTTPException(status_code=404, detail=f"no organization {slug!r}")
    await require_role(ctx, org, need)
    return org


async def authorize_project(ctx: HttpContext, ref: str, need: str | None = None) -> None:
    """Refuse an operator who may not use project *ref* this way.

    Reading needs ``viewer``; changing anything needs ``developer``, unless
    the route asks for more. Non-members get a 404 so a project's existence is
    not disclosed. Services are not bound to an organization.
    """
    if operator_of(ctx) is None:
        return
    project = await Project.filter(ref=ref).prefetch_related("organization").first()
    member = (
        await membership(ctx, project.organization)
        if project is not None and project.organization is not None
        else None
    )
    if member is None:
        raise HTTPException(status_code=404, detail=f"no project {ref!r}")
    need = need or ("viewer" if ctx.method in READ_METHODS else "developer")
    if ROLE_RANK.get(member.role, -1) < ROLE_RANK[need]:
        raise HTTPException(status_code=403, detail=f"this needs the {need} role or higher")


async def get_project(ref: str) -> Project:
    project = await Project.get_or_none(ref=ref)
    if project is None:
        raise HTTPException(status_code=404, detail=f"no project {ref!r}")
    return project


async def get_environment(ref: str, env: str) -> Environment:
    environment = (
        await Environment.filter(project__ref=ref, name=env).select_related("project").first()
    )
    if environment is None:
        raise HTTPException(status_code=404, detail=f"no environment {ref}/{env}")
    return environment


async def my_org_ids(ctx: HttpContext) -> list[int] | None:
    """The organizations the caller belongs to; ``None`` when unrestricted."""
    operator = operator_of(ctx)
    if operator is None:
        return None
    return await OrgMember.filter(user_id=operator[0]).values_list("organization_id", flat=True)


async def audit(
    ctx: HttpContext,
    action: str,
    *,
    project: str | None = None,
    env: str | None = None,
    target: str = "",
    details: dict[str, Any] | None = None,
    org: str | None = None,
) -> None:
    if org is None and project is not None:
        row = await Project.filter(ref=project).select_related("organization").first()
        org = row.organization.slug if row and row.organization else None
    await AuditEntry.create(
        project=project,
        env=env,
        org=org,
        actor=actor(ctx),
        action=action,
        target=target,
        details=details or {},
    )


async def changed(
    ctx: HttpContext,
    environment: Environment,
    action: str,
    target: str,
    details: dict[str, Any] | None = None,
) -> None:
    """A definition changed: bump the version and audit it."""
    await bump(environment.id)
    await audit(
        ctx,
        action,
        project=environment.project.ref,
        env=environment.name,
        target=target,
        details=details,
    )


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
