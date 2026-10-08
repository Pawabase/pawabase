"""Akountz's long-lived services.

Akountz owns identity and nothing else. Project configuration, mail delivery
and the event log belong to the API, so Akountz reaches them through a
service client. When Redis is configured, events go straight onto the shared
Sillo event bus instead, so identity events never depend on the API being up.
"""

from __future__ import annotations

import logging
from typing import Any

from sillo.cache import BaseCache, MemoryCache
from sillo.cache import base as cache_base
from sillo.helpers.signing import URLSafeTimedSerializer

from app.config import AkountzSettings
from pawabase_core import envvars
from pawabase_core.clients import ServiceClient
from pawabase_core.context import PlatformContext
from pawabase_core.crypto import SecretBox
from pawabase_core.events import EventBus
from pawabase_core.tokens import derive_env_secret

logger = logging.getLogger("pawabase.akountz")


class Akountz:
    def __init__(
        self,
        settings: AkountzSettings,
        *,
        api: ServiceClient | None = None,
        cache: BaseCache | None = None,
    ) -> None:
        self.settings = settings
        self.box = SecretBox(settings.master_key)
        self.api = api or ServiceClient(
            settings.api_url, secret=settings.internal_secret, issuer="akountz", audience="api"
        )
        self.cache = cache or self._build_cache()
        self.bus = (
            EventBus("persistent", url=settings.redis_url, source="akountz")
            if settings.redis_url
            else None
        )
        self.outbox: list[dict[str, Any]] = []  # recent mail, for tests and diagnostics

    def max_users(self, env: str) -> int:
        """The most active users an environment may have: its own ``<ENV>_MAX_USERS``, else the deployment's."""
        return envvars.integer(env, "MAX_USERS", self.settings.max_users)

    def _build_cache(self) -> BaseCache:
        if self.settings.redis_url:
            from sillo.cache import RedisCache

            return RedisCache(url=self.settings.redis_url, namespace="akountz")
        return MemoryCache(namespace="akountz")

    async def start(self) -> None:
        if self.bus is not None:
            await self.bus.start()

    async def stop(self) -> None:
        if self.bus is not None:
            await self.bus.stop()
        await self.api.close()

    async def cache_get(self, key: str) -> Any:
        value = await self.cache.get(key)
        return None if value is getattr(cache_base, "_MISSING", None) else value

    # ── secrets ──────────────────────────────────────────────────────────

    def refresh_secret(self, env: str) -> str:
        """Signs refresh tokens (Sillo's token pair). Separate from access tokens."""
        return derive_env_secret(self.settings.jwt_master_secret, f"{env}:refresh")

    def state_secret(self, env: str) -> str:
        """Signs OAuth state cookies and PKCE verifiers (sillo-oauth)."""
        return derive_env_secret(self.settings.internal_secret, f"{env}:oauth")

    def serializer(self, env: str, purpose: str) -> URLSafeTimedSerializer:
        """Sillo's timed signer for one purpose in one environment."""
        return URLSafeTimedSerializer(
            derive_env_secret(self.settings.internal_secret, env),
            salt=f"akountz:{purpose}",
        )

    # ── outbound effects ─────────────────────────────────────────────────

    async def emit(self, env: str, name: str, payload: Any, *, actor: str | None = None) -> None:
        """Publish an identity event. Failure to publish never fails the sign-in."""
        try:
            if self.bus is not None:
                await self.bus.emit(name, env=env, payload=payload, actor=actor)
            else:
                await self.api.post(
                    "/internal/v1/events",
                    json={
                        "env": env,
                        "name": name,
                        "payload": payload,
                        "actor": actor,
                    },
                )
        except Exception as exc:
            logger.warning("could not publish %s: %s", name, exc)

    async def send_mail(
        self, env: str, *, to: str, subject: str, text: str, html: str | None = None
    ) -> None:
        message = {
            "env": env,
            "to": [to],
            "subject": subject,
            "text": text,
            "html": html,
            "source": "akountz",
        }
        self.outbox.append(message)
        del self.outbox[:-100]
        try:
            await self.api.post("/internal/v1/mail", json=message)
        except Exception as exc:
            logger.warning("could not queue mail to %s: %s", to, exc)

    def context(self, env: str) -> PlatformContext:
        return PlatformContext(env=env, role="service")
