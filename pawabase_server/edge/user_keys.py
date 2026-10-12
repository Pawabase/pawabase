"""Turning a person's API key into the access token the application expects.

The edge knows the environment (from the project key) and the person's key; it asks the identity layer for a short-lived user access
token and puts that where a signed-in user's token would be. The answer is kept briefly so a busy key is not a database lookup per
request: a revoked key is refused within :data:`CACHE_SECONDS`.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from pawabase_server.db.models import Environment
from pawabase_server.identity import user_keys
from pawabase_server.identity.environment import AuthConfig

CACHE_SECONDS = 45
MAX_ENTRIES = 4096


class UserKeyGate:
    def __init__(self, secret: str, *, clock: Any = time.monotonic) -> None:
        self._secret = secret
        self._clock = clock
        self._tokens: dict[str, tuple[float, str]] = {}
        self._refused: dict[str, tuple[float, str]] = {}

    async def token_for(self, raw: str, *, env: str, ip: str | None) -> str:
        """The access token for *raw* in *env*. Raises :class:`~pawabase_server.identity.user_keys.KeyRefused`."""
        slot = hashlib.sha256(f"{env}:{raw}".encode()).hexdigest()
        now = self._clock()
        if (hit := self._tokens.get(slot)) and hit[0] > now:
            return hit[1]
        if (miss := self._refused.get(slot)) and miss[0] > now:
            raise user_keys.KeyRefused(miss[1])
        environment = await Environment.get_or_none(name=env)
        if environment is None:
            raise user_keys.KeyRefused("invalid API key")
        config = AuthConfig.from_api(env, {"auth": environment.auth or {}})
        try:
            token = await user_keys.mint(raw, env=env, config=config, secret=self._secret, ip=ip)
        except user_keys.KeyRefused as exc:
            self._remember(self._refused, slot, now + 5, str(exc))
            raise
        self._remember(self._tokens, slot, now + CACHE_SECONDS, token)
        return token

    @staticmethod
    def _remember(store: dict[str, tuple[float, str]], slot: str, until: float, value: str) -> None:
        if len(store) >= MAX_ENTRIES:
            store.clear()
        store[slot] = (until, value)
