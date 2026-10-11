"""Accounts: the service the identity routes share, and finding, creating and presenting one user's account.

:class:`Accounts` is what sign-in, sessions, MFA, organizations and recovery are built on. It owns no infrastructure of its own: secrets, cache,
mail and events all come from the platform it is given, so identity runs in the same process as everything else and never reaches it over HTTP.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.cache import base as cache_base
from sillo.exceptions import HTTPException
from sillo.hashing import validate_password
from sillo.helpers.signing import URLSafeTimedSerializer

from pawabase_server.core import envvars
from pawabase_server.core.context import PlatformContext
from pawabase_server.core.ids import is_ulid
from pawabase_server.core.tokens import derive_env_secret
from pawabase_server.db.models import AuthUser, Identity, Membership
from pawabase_server.identity.environment import AuthConfig

logger = logging.getLogger("pawabase.accounts")

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


async def find_by_email(env: str, email: str) -> AuthUser | None:
    return await AuthUser.filter(env=env, email=email.lower(), deleted_at=None).first()


async def get_user(env: str, user_id: Any) -> AuthUser | None:
    if not is_ulid(user_id):
        return None
    return await AuthUser.filter(id=str(user_id).upper(), env=env, deleted_at=None).first()


async def unique_username(env: str, wanted: str | None, email: str) -> str:
    base = re.sub(r"[^a-z0-9_.-]", "", (wanted or email.split("@", 1)[0]).lower())[:40] or "user"
    candidate = base
    while await AuthUser.filter(env=env, username=candidate).exists():
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
    max_users: int = 0,
    enforce_restrictions: bool = True,
) -> AuthUser:
    email = normalise_email(email)
    if enforce_restrictions and (refusal := config.email_refused(email)):
        raise HTTPException(status_code=403, detail=refusal)
    if await find_by_email(config.env, email):
        raise HTTPException(status_code=409, detail="an account with this email already exists")
    if username and await AuthUser.filter(env=config.env, username=username.lower()).exists():
        raise HTTPException(status_code=409, detail="that username is taken")
    if max_users and await AuthUser.filter(env=config.env, deleted_at=None).count() >= max_users:
        raise HTTPException(status_code=403, detail="the environment user limit has been reached")
    user = AuthUser(
        env=config.env,
        email=email,
        username=await unique_username(config.env, username, email),
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
    from pawabase_server.identity import rbac

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
    from pawabase_server.identity import rbac

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
                "organizations": [
                    {"slug": m.organization.slug, "name": m.organization.name, "role": m.role}
                    for m in await Membership.filter(user=user).prefetch_related("organization")
                ],
            }
        )
    return data


class Accounts:
    """Identity's shared services: secrets derived per environment, cached auth settings, and the platform's mail and events.

    Args:
        platform: The platform (or anything with ``settings``, ``cache``, ``box``, ``auth_settings``, ``emit_event`` and ``queue_mail``).
    """

    def __init__(self, platform: Any) -> None:
        self.platform = platform
        self.settings = platform.settings
        self.cache = platform.cache
        self.box = platform.box
        self.outbox: list[dict[str, Any]] = []  # recent mail, for tests and diagnostics

    def max_users(self, env: str) -> int:
        """The most active users an environment may have: its own ``<ENV>_MAX_USERS``, else the deployment's."""
        return envvars.integer(env, "MAX_USERS", self.settings.max_users)

    async def cache_get(self, key: str) -> Any:
        value = await self.cache.get(key)
        return None if value is getattr(cache_base, "_MISSING", None) else value

    # ── secrets ──────────────────────────────────────────────────────────

    def refresh_secret(self, env: str) -> str:
        """Signs refresh tokens (Sillo's token pair). Separate from access tokens."""
        return derive_env_secret(self.settings.jwt_master_secret, f"{env}:refresh")

    def state_secret(self, env: str) -> str:
        """Signs OAuth state cookies and PKCE verifiers (sillo-oauth)."""
        return derive_env_secret(self.settings.internal_secret, f"{env}:oauth")

    def serializer(self, env: str, purpose: str) -> URLSafeTimedSerializer:
        """Sillo's timed signer for one purpose in one environment."""
        return URLSafeTimedSerializer(
            derive_env_secret(self.settings.internal_secret, env),
            salt=f"akountz:{purpose}",
        )

    # ── what identity asks of the platform ───────────────────────────────

    async def auth_settings(self, env: str) -> dict[str, Any]:
        return await self.platform.auth_settings(env)

    async def emit(self, env: str, name: str, payload: Any, *, actor: str | None = None) -> None:
        """Publish an identity event. Failure to publish never fails the sign-in."""
        try:
            await self.platform.emit_event(env, name, payload, actor=actor)
        except Exception as exc:
            logger.warning("could not publish %s: %s", name, exc)

    async def send_mail(
        self, env: str, *, to: str, subject: str, text: str, html: str | None = None
    ) -> None:
        self.outbox.append({"env": env, "to": [to], "subject": subject, "text": text, "html": html})
        del self.outbox[:-100]
        try:
            await self.platform.queue_mail(
                env, to=[to], subject=subject, text=text, html=html, source="accounts"
            )
        except Exception as exc:
            logger.warning("could not queue mail to %s: %s", to, exc)

    def context(self, env: str) -> PlatformContext:
        return PlatformContext(env=env, role="service")
