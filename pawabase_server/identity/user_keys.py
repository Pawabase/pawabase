"""API keys a person creates for themselves.

A key is a bearer credential (``Authorization: Bearer pbu_…``, alongside the environment's own key in ``apikey``) that acts as its
owner: the same roles, the same organization role, the same permissions, narrowed to the key's ``scopes`` when it has any. The edge
turns a valid key into a short-lived user access token before the request reaches the application, so every route that accepts a
signed-in user accepts a key, with no per-route work.

Only a hash is stored. A key stops working when it is revoked or expires, when its owner is disabled or deleted, or when the
environment turns user keys off.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.auth.apikey import generate_api_key, hash_api_key

from pawabase_server.core.tokens import issue_user_token
from pawabase_server.db.models import AuthUser, UserApiKey
from pawabase_server.identity import rbac, sessions
from pawabase_server.identity.environment import AuthConfig
from pawabase_server.identity.sessions import membership_role
from pawabase_server.projects import quotas

PREFIX = "pbu"
#: How long the access token an edge mints for a key lives.
TOKEN_TTL = 300
#: Marks a session id as a key's, so routes that need a signed-in *person* can refuse it.
SESSION_PREFIX = "key:"


def looks_like_key(value: str) -> bool:
    return value.startswith(PREFIX + "_") and 20 <= len(value) <= 120


async def create(
    config: AuthConfig,
    user: AuthUser,
    *,
    name: str,
    scopes: list[str] | None = None,
    expires_in_days: int | None = None,
    org: str | None = None,
) -> tuple[str, UserApiKey]:
    """Make a key for *user*. Returns the plaintext (shown once) and the row. Raises ``ValueError`` when the directory's rules refuse it."""
    policy = config.policy("api_keys")
    if not policy["enabled"]:
        raise ValueError("API keys are not turned on for this environment")
    held = await UserApiKey.filter(user=user, revoked_at=None).count()
    if held >= policy["max_per_user"]:
        raise ValueError(
            f"you can have at most {policy['max_per_user']} API keys; delete one first"
        )
    # The plan's ceiling sits above whatever the environment's own directory settings allow.
    quotas.require_room(
        "max_user_api_keys_per_user", quotas.allowance(config.project_limits, "max_user_api_keys_per_user"), held
    )
    longest = policy["max_days"]
    days = expires_in_days
    if longest:
        if days is None:
            days = longest
        elif days > longest:
            raise ValueError(f"a key may live at most {longest} days")
    if days is not None and days < 1:
        raise ValueError("expires_in_days must be at least 1")
    if scopes and not policy["scoped"]:
        raise ValueError("scoped keys are not turned on for this environment")
    if org and await membership_role(config, user, org) is None:
        raise ValueError("you are not a member of that organization")
    full, _raw, digest = generate_api_key(prefix=PREFIX)
    row = await UserApiKey.create(
        user=user,
        env=config.env,
        name=name,
        prefix=full[:12],
        key_hash=digest,
        scopes=sorted(set(scopes or [])),
        org=org,
        expires_at=datetime.now(UTC) + timedelta(days=days) if days else None,
    )
    return full, row


def view(row: UserApiKey, *, email: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    status = (
        "revoked"
        if row.revoked_at
        else "expired"
        if row.expires_at and row.expires_at <= now
        else "active"
    )
    out = {
        "id": str(row.id),
        "name": row.name,
        "prefix": row.prefix,
        "scopes": row.scopes or [],
        "org": row.org,
        "status": status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "last_ip": row.last_ip,
        "use_count": row.use_count,
    }
    if email is not None:
        out["user_email"] = email
    return out


class KeyRefused(Exception):
    """The key is unknown, revoked, expired, its owner is unavailable, or the environment does not allow keys."""


async def mint(
    raw: str, *, env: str, config: AuthConfig, secret: str, ip: str | None = None
) -> str:
    """The user access token a valid key stands for. Raises :class:`KeyRefused`."""
    row = await UserApiKey.filter(key_hash=hash_api_key(raw)).select_related("user").first()
    now = datetime.now(UTC)
    if (
        row is None
        or row.env != env
        or row.revoked_at is not None
        or (row.expires_at is not None and row.expires_at <= now)
    ):
        raise KeyRefused("invalid API key")
    user = row.user
    if user.is_disabled or user.deleted_at is not None or user.env != env:
        raise KeyRefused("invalid API key")
    if not config.policy("api_keys")["enabled"]:
        raise KeyRefused("API keys are not turned on for this environment")
    if row.last_used_at is None or now - row.last_used_at > timedelta(seconds=60):
        await UserApiKey.filter(id=row.id).update(
            last_used_at=now, last_ip=ip, use_count=row.use_count + 1
        )
    permissions = await rbac.permissions_of(user)
    scopes = row.scopes or []
    if scopes and config.policy("api_keys")["scoped"]:
        permissions = [p for p in permissions if p in scopes or any(_covers(s, p) for s in scopes)]
    org_role = await membership_role(config, user, row.org) if row.org else None
    return issue_user_token(
        secret,
        env=env,
        user_id=str(user.id),
        jti=secrets.token_hex(12),
        session_id=f"{SESSION_PREFIX}{row.id}",
        ttl=TOKEN_TTL,
        email=user.email,
        roles=await rbac.roles_of(user),
        permissions=permissions,
        org=row.org if org_role else None,
        org_role=org_role,
        aal="aal1",
        extra=sessions._claims(config, user),
        app=user.app_metadata or None,
    )


def _covers(scope: str, permission: str) -> bool:
    """``storage:*`` covers ``storage:read``."""
    return scope.endswith(":*") and permission.startswith(scope[:-1])
