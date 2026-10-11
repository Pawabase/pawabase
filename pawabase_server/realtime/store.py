"""Where an environment's channel rules come from, and how long they are trusted.

The API owns the rules. Asking it on every message would put it on the realtime hot path, so rules are cached for ``config_ttl`` seconds.
Three situations the plain cache gets wrong are handled here:

* many connections ask for the same environment in the same instant after expiry: one request goes out and the rest wait for it;
* the API is briefly unreachable: the last good rules keep serving for ``stale_config_ttl`` seconds, so a restart of the API does not
  disconnect or freeze every client;
* the environment does not exist: the answer is remembered for a few seconds, so a client hammering a wrong name costs one request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sillo.exceptions import HTTPException

from pawabase_server.realtime.rules import ChannelConfig, build_config

logger = logging.getLogger("pawabase.realtime.config")

MISSING_TTL = 5.0


@dataclass
class _Entry:
    config: ChannelConfig
    fetched: float


class ConfigStore:
    """Cached, single-flight, stale-tolerant access to every environment's :class:`ChannelConfig`."""

    def __init__(
        self,
        fetch: Callable[[str], Awaitable[Any]],
        *,
        ttl: float,
        stale_ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fetch = fetch
        self.ttl = ttl
        self.stale_ttl = stale_ttl
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._missing: dict[str, float] = {}
        self._inflight: dict[str, asyncio.Future[ChannelConfig]] = {}
        self.stale_served = 0
        self.refreshes = 0

    def known(self) -> list[str]:
        """Environments whose rules are held right now."""
        return sorted(self._entries)

    def version(self, env: str) -> int | None:
        """The version of the rules held for *env*, or ``None`` when none are held."""
        entry = self._entries.get(env)
        return entry.config.version if entry else None

    def invalidate(self, env: str | None = None) -> None:
        """Forget one environment's rules, or all of them, so the next ask fetches again."""
        if env is None:
            self._entries.clear()
            self._missing.clear()
        else:
            self._entries.pop(env, None)
            self._missing.pop(env, None)

    async def get(self, env: str) -> ChannelConfig:
        now = self._clock()
        entry = self._entries.get(env)
        if entry is not None and now - entry.fetched < self.ttl:
            return entry.config
        missing_until = self._missing.get(env)
        if missing_until is not None and now < missing_until:
            raise HTTPException(status_code=404, detail=f"no environment {env!r}")
        pending = self._inflight.get(env)
        if pending is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[ChannelConfig] = asyncio.get_running_loop().create_future()
        self._inflight[env] = future
        try:
            config = await self._fetch(env, entry)
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # marked retrieved: waiters get it through the shield, and an unawaited one must not warn
            raise
        else:
            future.set_result(config)
            return config
        finally:
            self._inflight.pop(env, None)

    async def _fetch(self, env: str, previous: _Entry | None) -> ChannelConfig:
        self.refreshes += 1
        try:
            payload: Any = await self.fetch(env)
        except HTTPException as exc:
            if exc.status_code == 404:
                self._entries.pop(env, None)
                self._missing[env] = self._clock() + MISSING_TTL
                raise
            return self._stale_or_raise(env, previous, exc)
        except Exception as exc:
            return self._stale_or_raise(env, previous, exc)
        config = build_config(payload)
        self._entries[env] = _Entry(config, self._clock())
        self._missing.pop(env, None)
        return config

    def _stale_or_raise(
        self, env: str, previous: _Entry | None, exc: BaseException
    ) -> ChannelConfig:
        if previous is not None and self._clock() - previous.fetched < self.stale_ttl:
            self.stale_served += 1
            logger.warning("serving stale realtime rules for %s: %s", env, exc)
            return previous.config
        raise exc
