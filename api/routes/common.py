"""Helpers shared by the management-plane routes."""

from __future__ import annotations

from typing import Any

from sillo import HttpContext
from sillo.exceptions import HTTPException

from app.state import bump
from database.models import AuditEntry, Environment
from pawabase_core.context import current_context
from pawabase_core.policies import PolicyGate


class ManagementGate(PolicyGate):
    """The management-plane gate: Studio and scoped service API keys.

    Studio reaches the API with a service token. The CLI reaches it with a
    secret API key, which arrives from the gateway as a signed context. A key
    manages *its own* environment and nothing else: the gateway proves whose
    key it is, and this is where the path is held to it. Without that check a
    secret key for one environment could administer any other.
    """

    async def authenticate(self, ctx: HttpContext) -> bool:
        platform = current_context(ctx)
        if platform is not None and platform.is_service:
            env = (ctx.path_params or {}).get("env")
            if env not in (None, platform.env):
                raise HTTPException(
                    status_code=403, detail="This API key belongs to a different environment"
                )
            return True
        await super().authenticate(ctx)
        return True


MANAGE = ManagementGate({"any": [{"kind": "service"}]}, schemes=None)

NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,62}$"
ENV_PATTERN = NAME_PATTERN


def actor(ctx: HttpContext) -> str | None:
    """Who is acting: Studio's service identity, or the API key that was presented."""
    user = ctx.scope.get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        claims = getattr(user, "claims", {}) or {}
        return claims.get("email") or user.identity
    platform = current_context(ctx)
    if platform is not None and platform.key_id:
        return f"key:{platform.key_id}"
    return None


async def get_environment(env: str) -> Environment:
    environment = await Environment.filter(name=env).first()
    if environment is None:
        raise HTTPException(status_code=404, detail=f"no environment {env!r}")
    return environment


async def audit(
    ctx: HttpContext,
    action: str,
    *,
    env: str | None = None,
    target: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    await AuditEntry.create(
        env=env,
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
    await audit(ctx, action, env=environment.name, target=target, details=details)


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
