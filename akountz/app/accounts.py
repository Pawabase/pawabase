"""Finding, creating and presenting accounts."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.exceptions import HTTPException
from sillo.hashing import validate_password

from app.environment import AuthConfig
from database.models import AuthUser, Identity

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL.match(email) or len(email) > 255:
        raise HTTPException(status_code=422, detail="a valid email address is required")
    return email


def check_password_policy(config: AuthConfig, password: str) -> None:
    """Sillo's password validator for the strict policy, a length rule otherwise."""
    if config.password_policy == "strict":
        problems = validate_password(password, min_length=config.password_min_length)
    else:
        problems = (
            [f"Password must be at least {config.password_min_length} characters."]
            if len(password or "") < config.password_min_length
            else []
        )
    if len(password or "") > 1024:
        problems.append("Password is too long.")
    if problems:
        raise HTTPException(
            status_code=422,
            detail={"message": "the password does not meet the policy", "problems": problems},
        )


async def find_by_email(project: str, env: str, email: str) -> AuthUser | None:
    return await AuthUser.filter(
        project=project, env=env, email=email.lower(), deleted_at=None
    ).first()


async def get_user(project: str, env: str, user_id: Any) -> AuthUser | None:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    return await AuthUser.filter(id=uid, project=project, env=env, deleted_at=None).first()


async def unique_username(project: str, env: str, wanted: str | None, email: str) -> str:
    base = re.sub(r"[^a-z0-9_.-]", "", (wanted or email.split("@", 1)[0]).lower())[:40] or "user"
    candidate = base
    while await AuthUser.filter(project=project, env=env, username=candidate).exists():
        candidate = f"{base}{secrets.randbelow(10_000)}"
    return candidate


async def create_account(
    config: AuthConfig,
    *,
    email: str,
    password: str | None,
    username: str | None = None,
    name: str = "",
    user_metadata: dict[str, Any] | None = None,
    app_metadata: dict[str, Any] | None = None,
    verified: bool = False,
) -> AuthUser:
    email = normalise_email(email)
    if await find_by_email(config.project, config.env, email):
        raise HTTPException(status_code=409, detail="an account with this email already exists")
    if (
        username
        and await AuthUser.filter(
            project=config.project, env=config.env, username=username.lower()
        ).exists()
    ):
        raise HTTPException(status_code=409, detail="that username is taken")
    user = AuthUser(
        project=config.project,
        env=config.env,
        email=email,
        username=await unique_username(config.project, config.env, username, email),
        name=name,
        user_metadata=user_metadata or {},
        app_metadata=app_metadata or {},
        email_verified_at=datetime.now(UTC) if verified else None,
        password="",
    )
    if password:
        check_password_policy(config, password)
        user.set_password(password)
    else:
        user.set_unusable_password()
    await user.save()
    from app import rbac

    for role in config.default_roles:
        await rbac.assign_role(user, role)
    return user


def is_locked(user: AuthUser) -> bool:
    return user.locked_until is not None and user.locked_until > datetime.now(UTC)


async def record_failure(user: AuthUser, threshold: int, minutes: int) -> None:
    user.failed_logins += 1
    if user.failed_logins >= threshold:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=minutes)
        user.failed_logins = 0
    await user.save(update_fields=["failed_logins", "locked_until"])


async def record_success(user: AuthUser, ip: str | None) -> None:
    user.failed_logins = 0
    user.locked_until = None
    user.last_login = datetime.now(UTC)
    user.last_sign_in_ip = ip
    await user.save(
        update_fields=["failed_logins", "locked_until", "last_login", "last_sign_in_ip"]
    )


def iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


async def user_view(user: AuthUser, *, admin: bool = False) -> dict[str, Any]:
    from app import rbac

    identities = await Identity.filter(user=user).values(
        "id", "provider", "email", "last_sign_in_at"
    )
    data = {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "email_verified": user.email_verified_at is not None,
        "email_verified_at": iso(user.email_verified_at),
        "user_metadata": user.user_metadata or {},
        "app_metadata": user.app_metadata or {},
        "roles": await rbac.roles_of(user),
        "mfa_enabled": user.mfa_enabled,
        "identities": [{**i, "last_sign_in_at": iso(i["last_sign_in_at"])} for i in identities],
        "created_at": iso(user.created_at),
        "last_sign_in_at": iso(user.last_login),
    }
    if admin:
        data.update(
            {
                "disabled": user.is_disabled,
                "disabled_at": iso(user.disabled_at),
                "locked_until": iso(user.locked_until),
                "last_sign_in_ip": user.last_sign_in_ip,
                "has_password": user.has_usable_password(),
                "permissions": await rbac.permissions_of(user),
            }
        )
    return data
