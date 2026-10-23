"""Deciding whether there is room for a request or a connection: rate limits, request quotas and connection ceilings.

Every limit exists twice: once for the whole installation and once per project (see
:mod:`pawabase_server.edge.limits`). A request has to fit under both. Limits are applied *after* a key has been resolved so a flood of bad keys is spent
against the caller's address, not against any environment's budget.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from sillo.core.http import HttpContext
from sillo.security import RateLimitConfig, RateLimitMiddleware

from pawabase_server.core.namespace import scoped
from pawabase_server.core.telemetry import note
from pawabase_server.edge.errors import WS_CAPACITY, Refusal
from pawabase_server.edge.limits import EnvironmentLimits
from pawabase_server.edge.quotas import RequestQuota
from pawabase_server.projects import quotas
from pawabase_server.settings import Settings

KEY_SCOPE = "pawabase.gateway.key"
IP_SCOPE = "pawabase.gateway.client_ip"


def limit_key(ctx: HttpContext) -> str:
    """What a rate limit is counted against: the key when there is one, else the verified client address."""
    key_id = ctx.scope.get(KEY_SCOPE)
    if key_id:
        return f"key:{key_id}"
    return f"ip:{ctx.scope.get(IP_SCOPE) or 'unknown'}"


class Rejected(Exception):
    """Raised by :meth:`Admission.connection` when a WebSocket may not be opened."""

    def __init__(self, refusal: Refusal) -> None:
        super().__init__(refusal.error)
        self.refusal = refusal


class Admission:
    def __init__(
        self,
        settings: Settings,
        stats: dict[str, int],
        *,
        rate_backend: Any = "memory",
        request_quota: RequestQuota | None = None,
    ) -> None:
        self.stats = stats
        self.limiter = RateLimitMiddleware(
            RateLimitConfig(
                limit=settings.rate_limit,
                window=settings.rate_window,
                namespace=scoped("gw"),
                backend=rate_backend,
                key_func=limit_key,
            )
        )
        self.request_quota = request_quota or RequestQuota()
        self.env_limits = EnvironmentLimits(settings, key_func=limit_key, rate_backend=rate_backend)
        self.connections = (
            asyncio.BoundedSemaphore(settings.max_active_connections)
            if settings.max_active_connections > 0
            else None
        )
        self.max_connections = settings.max_active_connections

    async def admit_request(
        self, scope: dict[str, Any], receive: Any, context: Any, info: dict[str, Any] | None = None
    ) -> Refusal | None:
        """Rate limit and quota checks for one HTTP request. ``None`` means it may proceed."""
        own = self._project_limit(info)
        limiter = own.limiter if own is not None and own.limiter is not None else self.limiter
        result = await limiter.check(HttpContext(scope, receive))
        if result is not None and not result.allowed:
            self.stats["rate_limited"] += 1
            note("rate_limited", True)
            retry = max(int(result.retry_after), 1)
            return Refusal(
                429,
                "rate_limit_exceeded",
                extra={"retry_after": retry},
                headers=((b"retry-after", str(retry).encode()),),
            )
        if info:
            over = await quotas.bandwidth_over(info.get("project_id"), info.get("project_limits"))
            if over is not None:
                self.stats["quota_rejected"] += 1
                return Refusal(
                    429,
                    "bandwidth_quota_exceeded",
                    "This project has used its bandwidth for the month.",
                )
        if own is not None and own.quota is not None and not await own.quota.acquire():
            self.stats["quota_rejected"] += 1
            return Refusal(
                429, "request_quota_exceeded", "This project's request quota has been reached."
            )
        if not await self.request_quota.acquire():
            self.stats["quota_rejected"] += 1
            return Refusal(
                429, "request_quota_exceeded", "The installation request quota has been reached."
            )
        return None

    @contextlib.asynccontextmanager
    async def connection(
        self, context: Any, info: dict[str, Any] | None = None
    ) -> AsyncIterator[None]:
        """Hold one connection slot, for the project and for the installation, for as long as the ``with`` block runs.

        Raises :class:`Rejected` when either ceiling is reached. Slots are released however the block ends, so a bridge that crashes cannot
        leak capacity.
        """
        own = self._project_limit(info)
        project_slots = own.connections if own is not None else None
        if project_slots is not None and project_slots.locked():
            self.stats["connections_rejected"] += 1
            raise Rejected(
                Refusal(
                    429,
                    "project_connection_limit",
                    "project connection limit reached",
                    ws_code=WS_CAPACITY,
                )
            )
        if self.connections is not None and self.connections.locked():
            self.stats["connections_rejected"] += 1
            raise Rejected(
                Refusal(429, "connection_limit", "connection limit reached", ws_code=WS_CAPACITY)
            )
        held: list[asyncio.BoundedSemaphore] = []
        try:
            for slots in (project_slots, self.connections):
                if slots is not None:
                    await slots.acquire()
                    held.append(slots)
            yield
        finally:
            for slots in held:
                slots.release()

    def active_connections(self) -> int:
        return self.max_connections - self.connections._value if self.connections is not None else 0

    def _project_limit(self, info: dict[str, Any] | None):
        if not info:
            return None
        return self.env_limits.for_project(
            info.get("project_id"), info.get("project_limits_version"), info.get("project_limits")
        )

    async def close(self) -> None:
        await self.env_limits.close()
        await self.request_quota.close()
