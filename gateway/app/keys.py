"""Resolving API keys into platform contexts.

The API owns keys. The gateway asks it once per key and caches the answer, including refusals (briefly), so a burst of requests with a bad
key does not become a burst of lookups. Three more cases matter once traffic is real:

* a popular key expires and a hundred requests arrive in the same instant: they share **one** lookup;
* the API is unreachable when a key's answer has just expired: the last good answer keeps being used for ``stale_ttl`` seconds, so an API
  restart does not turn every client away (a key revoked during that window stays usable for at most that long);
* a refusal because the API itself failed is never cached as if the key were bad.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

from sillo.cache import BaseCache, MemoryCache
from sillo.cache import base as cache_base

from pawabase_core.clients import ServiceClient, ServiceError
from pawabase_core.context import PlatformContext

logger = logging.getLogger("pawabase.gateway.keys")

NEGATIVE_TTL = 5
REFUSALS = (401, 404, 422)


class KeyRejected(Exception):
    pass


class KeyResolver:
    def __init__(
        self,
        api: ServiceClient,
        *,
        cache: BaseCache | None = None,
        ttl: int = 30,
        stale_ttl: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.cache = cache or MemoryCache(namespace="gateway-keys")
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
            info = await self.api.post("/internal/v1/keys/resolve", json={"key": raw, "env": env})
        except ServiceError as exc:
            if exc.status in REFUSALS:
                detail = exc.body.get("detail") if isinstance(exc.body, dict) else None
                message = detail or "invalid API key"
                await self.cache.set(cache_key, {"rejected": message}, ttl=NEGATIVE_TTL)
                self._last_good.pop(cache_key, None)
                raise KeyRejected(message) from exc
            return self._stale_or_raise(cache_key, exc)
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
            logger.warning("API unreachable; using the last answer for a key: %s", exc)
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
