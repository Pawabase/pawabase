"""Pieces shared by compiled resource and custom routes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sillo.auth.exceptions import AuthenticationFailed, PermissionDenied

from pawabase_kit.policies import PolicyGate, build_policy_context
from pawabase_kit.telemetry import note

PLAN_SCOPE_KEY = "pawabase.plan"


class PlanGate(PolicyGate):
    """A policy gate that decides what it can before a record is loaded.

    Conditions not involving ``record`` are decided here; a request no row
    could ever satisfy is refused before any query. The remainder (SQL
    filters and a per-row residual) is left on the request for the handler.
    """

    async def authenticate(self, ctx) -> bool:
        from sillo.auth import useAuth

        await useAuth.authenticate(self, ctx)
        from pawabase_kit.context import current_context

        platform = current_context(ctx)
        if (
            self.scope_required
            and platform is not None
            and not platform.allows_scope(self.scope_required)
        ):
            raise PermissionDenied(f"This API key lacks the {self.scope_required!r} scope")
        engine = self._engine(ctx)
        plan = engine.plan(self.policy, build_policy_context(ctx))
        ctx.scope[PLAN_SCOPE_KEY] = plan
        note("policy", plan.policy)
        if not plan.allowed:
            user = ctx.scope.get("user")
            if user is None or not getattr(user, "is_authenticated", False):
                raise AuthenticationFailed("Authentication required")
            raise PermissionDenied(f"policy {plan.policy!r} refused")
        return True


def cache_key(ctx, *parts: Any) -> str:
    """A cache key that includes the caller, so cached reads never cross users."""
    user = ctx.scope.get("user")
    identity = (
        user.identity if user is not None and getattr(user, "is_authenticated", False) else "anon"
    )
    from pawabase_kit.context import current_context

    platform = current_context(ctx)
    role = platform.role if platform else "none"
    raw = json.dumps([identity, role, *parts], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body = {"error": code, "message": message}
    if details is not None:
        body["details"] = details
    return body
