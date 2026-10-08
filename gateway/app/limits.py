"""Limits an environment sets for itself, with ``<ENV>_*`` environment variables.

The deployment-wide limits (``PAWABASE_DAILY_REQUEST_LIMIT``, ``PAWABASE_RATE_LIMIT``,
``PAWABASE_MAX_ACTIVE_CONNECTIONS``) always apply. An environment can set its own on top
of them, so a request has to fit under both::

    PAWABASE_DAILY_REQUEST_LIMIT=1000000      # the whole deployment
    STAGING_DAILY_REQUEST_LIMIT=5000          # staging, inside that
    PRODUCTION_RATE_LIMIT=3000                # production's own rate limit
    PRODUCTION_RATE_WINDOW=60

Values are read the first time an environment is seen and kept for the life of the process,
so changing one means restarting the gateway, as with every other deployment setting. A
value that is not a whole number is logged and ignored for that setting: the deployment-wide
limit still applies, which is the safe direction to fail in.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sillo.security import RateLimitConfig, RateLimitMiddleware

from app.quotas import RequestQuota
from pawabase_core import envvars

logger = logging.getLogger("pawabase.gateway")


@dataclass
class EnvironmentLimit:
    """The limits one environment adds. A field is ``None`` when the environment sets none."""

    quota: RequestQuota | None = None
    limiter: RateLimitMiddleware | None = None
    connections: asyncio.BoundedSemaphore | None = None
    max_connections: int = 0
    rate_limit: int = 0
    rate_window: int = 0


class EnvironmentLimits:
    """Builds, once per environment, the limits its ``<ENV>_*`` variables ask for."""

    def __init__(
        self,
        settings: Any,
        *,
        key_func: Callable[..., str],
        rate_backend: Any = "memory",
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._key_func = key_func
        self._backend = rate_backend
        self._environ = environ
        self._cache: dict[str, EnvironmentLimit] = {}

    def for_environment(self, env: str) -> EnvironmentLimit:
        found = self._cache.get(env)
        if found is None:
            found = self._cache[env] = self._build(env)
        return found

    def _number(self, env: str, key: str) -> int:
        try:
            return max(envvars.integer(env, key, 0, self._environ), 0)
        except ValueError as exc:
            logger.error("%s; ignoring it, the deployment-wide limit still applies", exc)
            return 0

    def _build(self, env: str) -> EnvironmentLimit:
        settings = self._settings
        daily = self._number(env, "DAILY_REQUEST_LIMIT")
        monthly = self._number(env, "MONTHLY_REQUEST_LIMIT")
        rate = self._number(env, "RATE_LIMIT")
        window = self._number(env, "RATE_WINDOW")
        connections = self._number(env, "MAX_ACTIVE_CONNECTIONS")
        limit = EnvironmentLimit(max_connections=connections, rate_limit=rate, rate_window=window)
        if daily or monthly:
            limit.quota = RequestQuota(
                daily_limit=daily,
                monthly_limit=monthly,
                reservation=settings.request_quota_reservation,
                redis_url=settings.redis_url,
                scope=env,
            )
        if rate or window:
            limit.limiter = RateLimitMiddleware(
                RateLimitConfig(
                    limit=rate or settings.rate_limit,
                    window=window or settings.rate_window,
                    namespace=f"gw:{env}",
                    backend=self._backend,
                    key_func=self._key_func,
                )
            )
        if connections:
            limit.connections = asyncio.BoundedSemaphore(connections)
        return limit

    async def usage(self) -> dict[str, Any]:
        """Counters for every environment that has limits of its own."""
        report: dict[str, Any] = {}
        for env, limit in sorted(self._cache.items()):
            entry: dict[str, Any] = {}
            if limit.quota is not None:
                entry.update(await limit.quota.usage())
            if limit.connections is not None:
                entry["connections"] = {
                    "active": limit.max_connections - limit.connections._value,
                    "limit": limit.max_connections,
                }
            if limit.limiter is not None:
                entry["rate"] = {"limit": limit.rate_limit, "window": limit.rate_window}
            if entry:
                report[env] = entry
        return report

    async def close(self) -> None:
        for limit in self._cache.values():
            if limit.quota is not None:
                await limit.quota.close()
