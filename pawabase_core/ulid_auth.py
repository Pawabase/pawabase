"""Sillo's auth tables, keyed by ULID.

Sillo's user, JWT and permission models key on auto-increment integers, and a
few of its methods turn a user's identity into an ``int``. Pawabase never uses
integer keys, so Akountz subclasses those models with a ULID key and this
module closes the gaps:

* :func:`adopt` points Sillo's own modules at the subclasses, so the methods
  Sillo ships (``Permission.assign``, ``JWTToken.revoke_family`` and the rest)
  query the ULID-keyed tables instead of its integer-keyed ones.
* :class:`UlidJWTUserMixin` replaces the four ``JWTUserMixin`` methods that
  call ``int(str(self.identity))``.
* :class:`UlidUserMixin` replaces ``load_user``, which does the same.
"""

from __future__ import annotations

import secrets
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.auth.jwt_auth import JWTUserMixin
from sillo.auth.jwt_auth.tokens import TokenForUser

#: Sillo modules that bind the model classes by name, and which names each binds.
_BOUND = {
    "sillo.permissions": ("Group", "GroupPermission", "Permission", "UserGroup", "UserPermission"),
    "sillo.permissions.models": (
        "Group",
        "GroupPermission",
        "Permission",
        "UserGroup",
        "UserPermission",
    ),
    "sillo.permissions.mixins": ("Group", "GroupPermission", "UserPermission"),
    "sillo.auth.jwt_auth": ("JWTToken", "TokenBlacklist"),
    "sillo.auth.jwt_auth.models": ("JWTToken", "TokenBlacklist"),
    "sillo.auth.jwt_auth.mixins": ("JWTToken", "TokenBlacklist"),
    "sillo.auth.jwt_auth.backend": ("TokenBlacklist",),
}


def adopt(**models: Any) -> None:
    """Make Sillo's modules use *models* (by class name) instead of their own.

    Sillo's methods look the classes up in their module's globals when they
    run, so rebinding the names is enough. Importing a Sillo module first makes
    the rebinding reach it.
    """
    import importlib

    for module_name, names in _BOUND.items():
        module = importlib.import_module(module_name)
        for name in names:
            if name in models:
                setattr(module, name, models[name])


def _jwt_token() -> Any:
    return sys.modules["sillo.auth.jwt_auth.models"].JWTToken


class UlidJWTUserMixin(JWTUserMixin):
    """``JWTUserMixin`` for users whose identity is a ULID string."""

    def _subject(self) -> str:
        return str(self.identity)  # ty: ignore[unresolved-attribute]

    async def _record_pair(
        self, family: str, access: tuple[str, datetime], refresh: tuple[str, datetime]
    ) -> None:
        token = _jwt_token()
        for kind, (jti, expires) in (("access", access), ("refresh", refresh)):
            await token.create(
                user_id=self._subject(),
                token_jti=jti,
                token_family=family,
                token_type=kind,
                expires_at=expires,
            )

    async def issue_token_pair(
        self,
        secret: str,
        access_expires: timedelta | None = None,
        refresh_expires: timedelta | None = None,
        algorithm: str = "HS256",
    ) -> dict:
        tokens = TokenForUser(self, secret=secret, algorithm=algorithm)
        family = secrets.token_hex(32)
        access_ttl = access_expires or timedelta(minutes=15)
        refresh_ttl = refresh_expires or timedelta(days=7)
        # The JTI has to travel inside the token: rotation looks the row up by it.
        access_jti, refresh_jti = secrets.token_hex(16), secrets.token_hex(16)
        access = tokens.access_token(access_ttl, jti=access_jti)
        refresh = tokens.refresh_token(refresh_ttl, jti=refresh_jti)
        now = datetime.now(UTC)
        await self._record_pair(
            family, (access_jti, now + access_ttl), (refresh_jti, now + refresh_ttl)
        )
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "token_family": family,
        }

    async def refresh_token_pair(
        self, refresh_token: str, secret: str, algorithm: str = "HS256"
    ) -> dict:
        token = _jwt_token()
        tokens = TokenForUser(self, secret=secret, algorithm=algorithm)
        try:
            payload = tokens.verify_no_expire(refresh_token)
        except Exception:
            raise ValueError("Invalid refresh token") from None
        existing = await token.filter(token_jti=payload.get("jti", refresh_token)).first()
        if existing is None or existing.token_type != "refresh":
            raise ValueError("Unknown refresh token")
        if existing.revoked:
            await token.revoke_family(existing.token_family)
            raise ValueError("Token family has been revoked — possible token theft")
        if existing.consumed_at is not None:
            await token.revoke_family(existing.token_family)
            raise ValueError("Refresh token already consumed — possible token theft")
        await existing.consume()
        access_ttl, refresh_ttl = timedelta(minutes=15), timedelta(days=7)
        access_jti, refresh_jti = secrets.token_hex(16), secrets.token_hex(16)
        access = tokens.access_token(access_ttl, jti=access_jti)
        refresh = tokens.refresh_token(refresh_ttl, jti=refresh_jti)
        now = datetime.now(UTC)
        await self._record_pair(
            existing.token_family, (access_jti, now + access_ttl), (refresh_jti, now + refresh_ttl)
        )
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "token_family": existing.token_family,
        }

    async def revoke_all_tokens(self) -> int:
        return await _jwt_token().revoke_all_for_user(self._subject())

    async def active_token_count(self) -> int:
        return (
            await _jwt_token()
            .filter(user_id=self._subject(), revoked=False, expires_at__gt=datetime.now(UTC))
            .count()
        )


class UlidUserMixin:
    """``UserBaseModel.load_user`` for ULID keys: no integer parse of the identity."""

    @classmethod
    async def load_user(cls, identity: str) -> Any:
        from .ids import is_ulid

        if not is_ulid(identity):
            return None
        user = await cls.filter(id=str(identity).upper(), is_active=True).first()  # ty: ignore[unresolved-attribute]
        if user is not None and hasattr(user, "load_permissions"):
            await user.load_permissions()
        return user
