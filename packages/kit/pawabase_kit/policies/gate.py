"""Enforcing policies on Sillo routes.

:class:`PolicyGate` is a :class:`sillo.auth.useAuth` subclass, so it plugs into
Sillo's route-level ``auth=`` argument like any other gate. The security
requirements in the OpenAPI document come from Sillo, and the policy decision
happens where Sillo already expects authorisation to happen.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sillo.auth import useAuth
from sillo.auth.exceptions import AuthenticationFailed, PermissionDenied

from ..context import current_context
from ..principal import policy_auth
from .engine import Decision, PolicyEngine

DECISION_SCOPE_KEY = "pawabase.policy"


def credential_context(ctx: Any) -> dict[str, Any]:
    """What policies see under ``credential``: the API key's role and scopes."""
    platform = current_context(ctx)
    user = _safe_user(ctx)
    kind = getattr(user, "kind", None)
    if platform is None:
        return {
            "role": kind or "none",
            "is_service": kind in ("service", "operator"),
            "scopes": [],
            "key_id": None,
        }
    return {
        "role": platform.role,
        "is_service": platform.is_service or kind in ("service", "operator"),
        "scopes": list(platform.scopes),
        "key_id": platform.key_id,
    }


def _safe_user(ctx: Any) -> Any:
    try:
        return ctx.scope.get("user")
    except Exception:
        return None


def request_context(ctx: Any) -> dict[str, Any]:
    """What policies see under ``request``."""
    client = getattr(ctx, "client", None)
    return {
        "method": ctx.scope.get("method"),
        "path": ctx.scope.get("path"),
        "ip": getattr(client, "host", None) if client else None,
        "params": dict(ctx.path_params) if hasattr(ctx, "path_params") else {},
        "query": dict(ctx.query_params) if hasattr(ctx, "query_params") else {},
    }


def build_policy_context(
    ctx: Any,
    *,
    record: Mapping[str, Any] | None = None,
    input: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the full evaluation context for a request."""
    platform = current_context(ctx)
    context: dict[str, Any] = {
        "auth": policy_auth(_safe_user(ctx)),
        "credential": credential_context(ctx),
        "request": request_context(ctx),
        "project": platform.project if platform else None,
        "env": platform.env if platform else None,
        "record": dict(record) if record is not None else None,
        "input": input,
    }
    if extra:
        context.update(extra)
    return context


class PolicyGate(useAuth):
    """A route gate that authenticates like ``useAuth`` and then applies a policy.

    Args:
        policy: A policy reference (see :class:`PolicyEngine`).
        engine: Where references are resolved. A callable receiving the context
            is accepted so a gate can pick the engine of the request's project.
        schemes: Accepted credentials, as for ``useAuth``.
        scope: An API-key scope required as well (``resource:write``).
    """

    def __init__(
        self,
        policy: Any = "authenticated",
        *,
        engine: PolicyEngine | Callable[[Any], PolicyEngine] | None = None,
        scope: str | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)
        self.policy = policy
        self.engine = engine
        self.scope_required = scope

    def _engine(self, ctx: Any) -> PolicyEngine:
        if self.engine is None:
            return PolicyEngine()
        if isinstance(self.engine, PolicyEngine):
            return self.engine
        return self.engine(ctx)

    async def authenticate(self, ctx: Any) -> bool:
        await super().authenticate(ctx)
        platform = current_context(ctx)
        if (
            self.scope_required
            and platform is not None
            and not platform.allows_scope(self.scope_required)
        ):
            raise PermissionDenied(f"This API key lacks the {self.scope_required!r} scope")
        decision = await self._engine(ctx).check(self.policy, build_policy_context(ctx))
        ctx.scope[DECISION_SCOPE_KEY] = decision
        if not decision:
            user = _safe_user(ctx)
            if user is None or not getattr(user, "is_authenticated", False):
                raise AuthenticationFailed("Authentication required")
            raise PermissionDenied(decision.reason)
        return True


def last_decision(ctx: Any) -> Decision | None:
    """The decision the gate made for this request, for request inspection."""
    return ctx.scope.get(DECISION_SCOPE_KEY)
