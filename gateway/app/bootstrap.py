"""Assembling the gateway: proxy, CORS, rate limits, request ids, health."""

from __future__ import annotations

from typing import Any

import httpx
from sillo import SilloApp
from sillo.security import CorsConfig, CORSMiddleware

from app.config import GatewaySettings
from app.domains import DomainSync
from app.keys import KeyResolver
from app.proxy import GatewayProxy
from app.quotas import RequestQuota
from app.routes import internal, landing, status
from app.status import StatusProber
from pawabase_core.clients import ServiceClient
from pawabase_core.service import create_service

SERVICES = status.SERVICES


def create_app(
    settings: GatewaySettings | None = None,
    *,
    clients: dict[str, httpx.AsyncClient] | None = None,
    api: ServiceClient | None = None,
) -> SilloApp:
    settings = settings or GatewaySettings()
    urls = {"api": settings.api_url, "akountz": settings.akountz_url, "angula": settings.angula_url}
    clients = clients or {
        name: httpx.AsyncClient(
            base_url=url, follow_redirects=False, timeout=settings.upstream_timeout
        )
        for name, url in urls.items()
    }
    api = api or ServiceClient(
        settings.api_url, secret=settings.internal_secret, issuer="gateway", audience="api"
    )
    cache: Any = None
    rate_backend: Any = "memory"
    if settings.redis_url:
        from sillo.cache import RedisCache
        from sillo.security import RedisBackend

        cache = RedisCache(url=settings.redis_url, namespace="gateway")
        rate_backend = RedisBackend(url=settings.redis_url)
    resolver = KeyResolver(
        api, cache=cache, ttl=settings.key_cache_ttl, stale_ttl=settings.key_stale_ttl
    )
    ws_bases = {
        name: url.replace("http://", "ws://").replace("https://", "wss://")
        for name, url in urls.items()
    }
    quota = RequestQuota(
        daily_limit=settings.daily_request_limit,
        monthly_limit=settings.monthly_request_limit,
        reservation=settings.request_quota_reservation,
        redis_url=settings.redis_url,
    )
    proxy = GatewayProxy(
        settings, resolver, clients, ws_bases, rate_backend=rate_backend, request_quota=quota
    )

    app = create_service(
        "gateway",
        settings,
        title="Pawabase Gateway",
        description="The public entry point to a Pawabase installation.",
        inner=[proxy],
        docs=[],
    )
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.use(
        CORSMiddleware(
            CorsConfig(
                allow_origins=origins or ["*"],
                allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
                allow_headers=["*"],
                expose_headers=[
                    "x-request-id",
                    "x-ratelimit-limit",
                    "x-ratelimit-remaining",
                    "retry-after",
                ],
                allow_credentials=False,
                max_age=600,
            )
        )
    )
    prober = StatusProber(api, clients, every=settings.status_probe_interval)
    domains = DomainSync(api, proxy, every=settings.domain_refresh_interval)
    app.state["proxy"] = proxy
    app.state["domain_sync"] = domains
    app.state["status_prober"] = prober
    app.state["request_quota"] = quota

    landing.register(app, settings)
    status.register(app, api, clients)
    internal.register(app, settings, proxy, resolver, quota)

    @app.on_startup
    async def begin_probing() -> None:
        prober.start()
        domains.start()

    @app.on_shutdown
    async def close() -> None:
        await prober.stop()
        await domains.stop()
        await proxy.close()
        await api.close()

    return app
