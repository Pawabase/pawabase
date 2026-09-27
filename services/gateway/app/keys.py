"""Resolving API keys into platform contexts.

The API owns keys. The gateway asks it once per key and caches the answer
with Sillo's cache, including refusals (briefly), so a burst of requests with
a bad key does not become a burst of lookups.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sillo.cache import BaseCache, MemoryCache
from sillo.cache import base as cache_base

from pawabase_kit.clients import ServiceClient, ServiceError
from pawabase_kit.context import PlatformContext

NEGATIVE_TTL = 5


class KeyRejected(Exception):
    pass


class KeyResolver:
    def __init__(
        self, api: ServiceClient, *, cache: BaseCache | None = None, ttl: int = 30
    ) -> None:
        self.api = api
        self.cache = cache or MemoryCache(namespace="gateway-keys")
        self.ttl = ttl
        self.lookups = 0

    @staticmethod
    def _key(raw: str) -> str:
        return "key:" + hashlib.sha256(raw.encode()).hexdigest()

    async def resolve(self, raw: str) -> tuple[PlatformContext, dict[str, Any]]:
        cache_key = self._key(raw)
        cached = await self.cache.get(cache_key)
        if cached is not getattr(cache_base, "_MISSING", None) and cached is not None:
            if cached.get("rejected"):
                raise KeyRejected(cached["rejected"])
            return self._context(cached), cached
        self.lookups += 1
        try:
            info = await self.api.post("/internal/v1/keys/resolve", json={"key": raw})
        except ServiceError as exc:
            if exc.status in (401, 404, 422):
                await self.cache.set(cache_key, {"rejected": "invalid API key"}, ttl=NEGATIVE_TTL)
                raise KeyRejected("invalid API key") from exc
            raise
        await self.cache.set(cache_key, info, ttl=self.ttl)
        return self._context(info), info

    @staticmethod
    def _context(info: dict[str, Any]) -> PlatformContext:
        return PlatformContext(
            project=info["project"],
            env=info["env"],
            role=info["role"],
            key_id=info.get("key_id"),
            scopes=tuple(info.get("scopes") or ()),
        )
