"""Pieces shared by compiled resource and custom routes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sillo import json as json_response
from sillo.auth.exceptions import AuthenticationFailed, PermissionDenied
from sillo.middleware.base import BaseMiddleware
from sillo.security import RateLimitConfig, RateLimitMiddleware

from pawabase_kit.policies import PolicyGate, build_policy_context
from pawabase_kit.telemetry import note

PLAN_SCOPE_KEY = "pawabase.plan"


class RouteRateLimit(BaseMiddleware):
    """Sillo's rate limiter, applied to one route.

    Sillo's :class:`~sillo.security.RateLimitMiddleware` is application
    middleware. Its ``check`` method holds the counting and backend logic, so a
    route-level wrapper uses that and answers with the same 429 body and
    headers Sillo does.
    """

    def __init__(self, *, limit: int, window: int, namespace: str, backend: Any = "memory") -> None:
        super().__init__()
        self.limiter = RateLimitMiddleware(
            RateLimitConfig(
                limit=limit,
                window=window,
                namespace=namespace,
                backend=backend,
                key_func=_client_key,
            )
        )

    async def dispatch(self, ctx, call_next):
        result = await self.limiter.check(ctx)
        if result is not None and not result.allowed:
            retry_after = max(int(result.retry_after), 1)
            note("rate_limited", True)
            return json_response(
                {
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Slow down and retry later.",
                    "retry_after": retry_after,
                },
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
        response = await call_next()
        if result is not None and response is not None and hasattr(response, "headers"):
            try:
                response.headers["X-RateLimit-Limit"] = str(result.limit)
                response.headers["X-RateLimit-Remaining"] = str(result.remaining)
            except Exception:
                pass
        return response


def _client_key(ctx) -> str:
    """Rate-limit per user when signed in, else per client address."""
    user = ctx.scope.get("user")
    if user is not None and getattr(user, "is_authenticated", False):
        return f"user:{user.identity}"
    forwarded = ctx.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = ctx.scope.get("client")
    return f"ip:{client[0] if client else 'unknown'}"


def rate_limit_middleware(
    settings: dict[str, Any] | None, namespace: str, redis_url: str
) -> list[Any]:
    if not settings or not settings.get("limit"):
        return []
    backend: Any = "memory"
    if redis_url:
        from sillo.security import RedisBackend

        backend = RedisBackend(url=redis_url)
    return [
        RouteRateLimit(
            limit=int(settings["limit"]),
            window=int(settings.get("window", 60)),
            namespace=namespace,
            backend=backend,
        )
    ]


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
