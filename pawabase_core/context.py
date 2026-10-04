"""Which environment a request belongs to, and with what credential.

The gateway resolves an API key into a :class:`PlatformContext`, signs it, and
forwards it in the ``X-Pawabase-Context`` header. Internal services read it back
with :class:`~pawabase_core.auth.ContextMiddleware` and find it with
:func:`current_context`. The raw key never travels past the gateway.
"""

from __future__ import annotations

import contextvars
from dataclasses import asdict, dataclass, field
from typing import Any

CONTEXT_HEADER = "x-pawabase-context"
SCOPE_KEY = "pawabase.context"

#: Credential roles. ``anon`` is a publishable key with no elevated rights;
#: ``service`` is a secret key (or Studio, acting for the runtime) and bypasses policies.
ROLES = ("anon", "service")


@dataclass(frozen=True, slots=True)
class PlatformContext:
    """The resolved credential of a request.

    Attributes:
        env: Environment name, e.g. ``"production"``.
        role: ``anon`` or ``service``.
        key_id: The API key that was presented, if any.
        scopes: Scopes granted to that key. Empty means unrestricted within the role.
    """

    env: str
    role: str = "anon"
    key_id: str | None = None
    scopes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_service(self) -> bool:
        """Whether this credential bypasses policies."""
        return self.role == "service"

    def allows_scope(self, scope: str) -> bool:
        """Whether the key's scopes permit *scope* (``resource:read`` etc.)."""
        if not self.scopes:
            return True
        family = scope.split(":", 1)[0]
        return scope in self.scopes or f"{family}:*" in self.scopes or "*" in self.scopes

    def to_claims(self) -> dict[str, Any]:
        """The context as token claims."""
        data = asdict(self)
        data["scopes"] = list(self.scopes)
        return data

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> PlatformContext:
        """Rebuild a context from verified claims."""
        return cls(
            env=str(claims["env"]),
            role=str(claims.get("role", "anon")),
            key_id=claims.get("key_id"),
            scopes=tuple(claims.get("scopes") or ()),
        )


_current: contextvars.ContextVar[PlatformContext | None] = contextvars.ContextVar(
    "pawabase_context", default=None
)


def current_context(ctx: Any = None) -> PlatformContext | None:
    """The context of the request being served.

    Args:
        ctx: A Sillo context. When omitted, the context bound to the current
            task is returned, which is how flows and functions reach it.
    """
    if ctx is not None:
        found = ctx.scope.get(SCOPE_KEY)
        if found is not None:
            return found
    return _current.get()


def require_context(ctx: Any = None) -> PlatformContext:
    """Like :func:`current_context`, but a missing context is an error."""
    from sillo.exceptions import HTTPException

    found = current_context(ctx)
    if found is None:
        raise HTTPException(status_code=401, detail="An API key is required")
    return found


def bind_context(value: PlatformContext | None) -> contextvars.Token:
    """Bind *value* to the current task. Returns the reset token."""
    return _current.set(value)


def reset_context(token: contextvars.Token) -> None:
    """Undo :func:`bind_context`."""
    _current.reset(token)
