"""Cached, project-level gateway limits.

Limit policies live with a project rather than in the runtime's environment. The edge
receives a policy together with the already-cached API-key resolution, so enforcing a
request never adds a database read. Policies are versioned: a changed version replaces
the in-process limiter for that project.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sillo.security import RateLimitConfig, RateLimitMiddleware

from pawabase_server.core.namespace import scoped
from pawabase_server.edge.quotas import RequestQuota

logger = logging.getLogger("pawabase.gateway")

LIMIT_FIELDS = frozenset(
    {
        "daily_request_limit",
        "monthly_request_limit",
        "rate_limit",
        "rate_window",
        "max_active_connections",
    }
)


@dataclass
class ProjectLimit:
    """The non-deployment limits a project adds. A field is ``None`` when it is unlimited."""

    quota: RequestQuota | None = None
    limiter: RateLimitMiddleware | None = None
    connections: asyncio.BoundedSemaphore | None = None
    max_connections: int = 0
    rate_limit: int = 0
    rate_window: int = 0


def normalise_policy(policy: Mapping[str, Any] | None) -> dict[str, int]:
    """Return safe policy values; malformed persisted data cannot disable global limits."""
    if not isinstance(policy, Mapping):
        return {}
    result: dict[str, int] = {}
    for field in LIMIT_FIELDS:
        value = policy.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            if value not in (None, 0):
                logger.error("ignoring invalid project limit %s=%r", field, value)
            continue
        if value:
            result[field] = value
    return result


class EnvironmentLimits:
    """Build and cache project policies by ``(project id, policy version)``.

    The name is retained for status API compatibility; limits are no longer read from
    ``<ENV>_*`` variables and are shared by all environments in a project.
    """

    def __init__(
        self, settings: Any, *, key_func: Callable[..., str], rate_backend: Any = "memory"
    ) -> None:
        self._settings = settings
        self._key_func = key_func
        self._backend = rate_backend
        self._cache: dict[tuple[str, int], ProjectLimit] = {}
        self._retired: list[ProjectLimit] = []

    def for_project(
        self, project_id: str | None, version: int | None, policy: Mapping[str, Any] | None
    ) -> ProjectLimit | None:
        if not project_id:
            return None
        values = normalise_policy(policy)
        version = version if isinstance(version, int) and version > 0 else 1
        key = (str(project_id), version)
        found = self._cache.get(key)
        if found is not None:
            return found
        for old_key in [item for item in self._cache if item[0] == key[0] and item != key]:
            self._retired.append(self._cache.pop(old_key))
        if not values:
            return None
        found = self._build(str(project_id), values)
        self._cache[key] = found
        return found

    def _build(self, project_id: str, values: Mapping[str, int]) -> ProjectLimit:
        settings = self._settings
        daily = values.get("daily_request_limit", 0)
        monthly = values.get("monthly_request_limit", 0)
        rate = values.get("rate_limit", 0)
        window = values.get("rate_window", 0)
        connections = values.get("max_active_connections", 0)
        limit = ProjectLimit(max_connections=connections, rate_limit=rate, rate_window=window)
        if daily or monthly:
            limit.quota = RequestQuota(
                daily_limit=daily,
                monthly_limit=monthly,
                reservation=settings.request_quota_reservation,
                redis_url=settings.redis_url,
                scope=f"project:{project_id}",
            )
        if rate:
            limit.limiter = RateLimitMiddleware(
                RateLimitConfig(
                    limit=rate,
                    window=window or settings.rate_window,
                    namespace=scoped(f"gw:project:{project_id}"),
                    backend=self._backend,
                    key_func=self._key_func,
                )
            )
        if connections:
            limit.connections = asyncio.BoundedSemaphore(connections)
        return limit

    async def usage(self) -> dict[str, Any]:
        """Counters for project policies currently resident in this gateway."""
        report: dict[str, Any] = {}
        for (project_id, version), limit in sorted(self._cache.items()):
            entry: dict[str, Any] = {"version": version}
            if limit.quota is not None:
                entry.update(await limit.quota.usage())
            if limit.connections is not None:
                entry["connections"] = {
                    "active": limit.max_connections - limit.connections._value,
                    "limit": limit.max_connections,
                }
            if limit.limiter is not None:
                entry["rate"] = {"limit": limit.rate_limit, "window": limit.rate_window}
            report[project_id] = entry
        return report

    async def close(self) -> None:
        for limit in [*self._cache.values(), *self._retired]:
            if limit.quota is not None:
                await limit.quota.close()
