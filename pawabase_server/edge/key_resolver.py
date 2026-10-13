"""Resolving API keys into platform contexts.

The edge resolves a key once and caches the answer, refusals included (briefly), so a burst of requests with a bad key does not become a
burst of database lookups. Three more cases matter once traffic is real:

* a popular key expires and a hundred requests arrive in the same instant: they share **one** lookup;
* the database is unreachable when a key's answer has just expired: the last good answer keeps being used for ``stale_ttl`` seconds, so a
  database failover does not turn every client away (a key revoked during that window stays usable for at most that long);
* a refusal because the lookup itself failed is never cached as if the key were bad.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.auth.apikey import hash_api_key
from sillo.cache import BaseCache, MemoryCache
from sillo.cache import base as cache_base

from pawabase_server.core.context import PlatformContext
from pawabase_server.db.models import ApiKey, FirewallRule

logger = logging.getLogger("pawabase.edge.keys")

NEGATIVE_TTL = 5
#: How often a key's last-used time is written, so a busy key is not an UPDATE per request.
USAGE_WRITE_INTERVAL = timedelta(seconds=60)


class KeyRejected(Exception):
    """The key is not valid (unknown, revoked, expired, or for another environment). The message is safe to show the caller."""


async def lookup_key(raw: str, env: str | None = None) -> dict[str, Any]:
    """Look *raw* up in the database and describe what it allows.

    A key belongs to one environment and names it. When the caller also names one, the key is held to it and refused otherwise, rather than
    serving another environment's data under a key meant for this one.

    Raises:
        KeyRejected: Unknown, revoked, expired, or for a different environment.
    """
    key = (
        await ApiKey.filter(key_hash=hash_api_key(raw))
        .select_related("environment", "environment__project")
        .first()
    )
    if key is not None and env and key.environment.name != env:
        raise KeyRejected(f"this key does not belong to environment {env!r}")
    now = datetime.now(UTC)
    if (
        key is None
        or key.revoked_at is not None
        or (key.expires_at is not None and key.expires_at <= now)
    ):
        raise KeyRejected("invalid API key")
    if key.last_used_at is None or now - key.last_used_at > USAGE_WRITE_INTERVAL:
        await ApiKey.filter(id=key.id).update(last_used_at=now, use_count=key.use_count + 1)
    environment = key.environment
    project = environment.project
    settings = environment.settings or {}
    return {
        "env": environment.name,
        # Edge-internal fields are cached with the key answer, avoiding a request-time
        # project lookup for limit enforcement.
        "project_id": project.id if project is not None else None,
        "project_limits": project.limits if project is not None else {},
        "project_limits_version": project.limits_version if project is not None else 1,
        "role": "service" if key.role == "secret" else "anon",
        "scopes": key.scopes or [],
        "allowed_ips": key.allowed_ips or [],
        "allowed_routes": key.allowed_routes or [],
        "key_id": str(key.id),
        "cors_origins": settings.get("cors_origins", []),
        "ip_allowlist": settings.get("ip_allowlist", []),
        "maintenance": settings.get("maintenance") or {},
        "firewall": [
            {"id": r.id, "name": r.name, "action": r.action, "match": r.match or {}}
            for r in await FirewallRule.filter(env=environment.name, enabled=True)
        ],
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
    }


class KeyResolver:
    def __init__(
        self,
        lookup: Callable[[str, str | None], Awaitable[dict[str, Any]]] = lookup_key,
        *,
        cache: BaseCache | None = None,
        ttl: int = 30,
        stale_ttl: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.lookup = lookup
        self.cache = cache or MemoryCache(namespace="edge-keys")
        self.ttl = ttl
        self.stale_ttl = stale_ttl
        self._clock = clock
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._last_good: dict[str, tuple[float, dict[str, Any]]] = {}
        self.lookups = 0
        self.stale_served = 0

    @staticmethod
    def _key(raw: str, env: str | None) -> str:
        # Scoping the cache key by the requested environment too means a key resolved once for the wrong environment can't shadow a later,
        # correctly-scoped lookup of the same raw key.
        scope = env or ""
        return "key:" + hashlib.sha256(f"{scope}:{raw}".encode()).hexdigest()

    async def resolve(
        self, raw: str, *, env: str | None = None
    ) -> tuple[PlatformContext, dict[str, Any]]:
        cache_key = self._key(raw, env)
        cached = await self.cache.get(cache_key)
        if cached is not getattr(cache_base, "_MISSING", None) and cached is not None:
            if cached.get("rejected"):
                raise KeyRejected(cached["rejected"])
            return self._context(cached), cached
        info = await self._lookup(cache_key, raw, env)
        return self._context(info), info

    async def _lookup(self, cache_key: str, raw: str, env: str | None) -> dict[str, Any]:
        pending = self._inflight.get(cache_key)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._inflight[cache_key] = future
        try:
            info = await self._ask(cache_key, raw, env)
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # marked retrieved: waiters get it through the shield, and an unawaited one must not warn
            raise
        else:
            future.set_result(info)
            return info
        finally:
            self._inflight.pop(cache_key, None)

    async def _ask(self, cache_key: str, raw: str, env: str | None) -> dict[str, Any]:
        self.lookups += 1
        try:
            info = await self.lookup(raw, env)
        except KeyRejected as exc:
            message = str(exc) or "invalid API key"
            await self.cache.set(cache_key, {"rejected": message}, ttl=NEGATIVE_TTL)
            self._last_good.pop(cache_key, None)
            raise KeyRejected(message) from exc
        except Exception as exc:
            return self._stale_or_raise(cache_key, exc)
        await self.cache.set(cache_key, info, ttl=self.ttl)
        if self.stale_ttl > 0:
            self._last_good[cache_key] = (self._clock(), info)
        return info

    def _stale_or_raise(self, cache_key: str, exc: BaseException) -> dict[str, Any]:
        kept = self._last_good.get(cache_key)
        if kept is not None and self._clock() - kept[0] < self.ttl + self.stale_ttl:
            self.stale_served += 1
            logger.warning("key lookup failed; using the last answer for a key: %s", exc)
            return kept[1]
        raise exc

    @staticmethod
    def _context(info: dict[str, Any]) -> PlatformContext:
        return PlatformContext(
            env=info["env"],
            role=info["role"],
            key_id=info.get("key_id"),
            scopes=tuple(info.get("scopes") or ()),
        )
