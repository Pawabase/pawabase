"""Turning an access token into the caller a connection acts as."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pawabase_core.principal import ANONYMOUS_POLICY_CONTEXT, Principal
from pawabase_core.tokens import TokenInvalid, verify_user_token

__all__ = ["Identity", "TokenInvalid", "anonymous", "identify"]


@dataclass(frozen=True)
class Identity:
    """The caller behind a connection. ``expires_at`` is epoch seconds, or ``None`` when the caller has no token."""

    auth: dict[str, Any]
    user_id: str | None
    expires_at: float | None = None


def anonymous() -> Identity:
    return Identity(dict(ANONYMOUS_POLICY_CONTEXT), None, None)


def identify(token: str | None, master_secret: str, env: str) -> Identity:
    """The :class:`Identity` a token proves, or anonymous when there is no token. Raises :class:`TokenInvalid`."""
    if not token:
        return anonymous()
    claims = verify_user_token(token, master_secret, env=env)
    principal = Principal("user", claims)
    exp = claims.get("exp")
    return Identity(
        principal.as_policy_context(),
        principal.identity,
        float(exp) if isinstance(exp, (int, float)) else None,
    )
