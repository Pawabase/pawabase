"""Sillo's rate limiter, applied to single routes."""

from __future__ import annotations

from typing import Any

from sillo import json as json_response
from sillo.middleware.base import BaseMiddleware
from sillo.security import RateLimitConfig, RateLimitMiddleware

from .telemetry import note


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
