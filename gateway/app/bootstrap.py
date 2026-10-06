"""Assembling the gateway: proxy, CORS, rate limits, request ids, health."""

from __future__ import annotations

import asyncio
from html import escape
from typing import Any

import httpx
from sillo import HttpContext, SilloApp, html
from sillo.security import CorsConfig, CORSMiddleware

from app.config import GatewaySettings
from app.keys import KeyResolver
from app.proxy import GatewayProxy
from app.quotas import RequestQuota
from pawabase_core.clients import ServiceClient
from pawabase_core.service import SERVICE_ONLY, create_service

SERVICES = ("api", "akountz", "angula")


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
    resolver = KeyResolver(api, cache=cache, ttl=settings.key_cache_ttl)
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
    app.state["proxy"] = proxy
    app.state["request_quota"] = quota

    @app.get("/", exclude_from_schema=True)
    async def landing(ctx: HttpContext):
        """A page for people who open the address in a browser: the API itself lives under /auth/v1, /rest/v1, /functions/v1 and the other prefixes."""
        name = escape(settings.project_name)
        return html(
            f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{name}</title>
<style>:root{{color-scheme:light dark}}body{{margin:0;min-height:100vh;display:grid;place-items:center;font:15px/1.6 system-ui,sans-serif}}
main{{max-width:30rem;padding:2rem;text-align:center}}h1{{margin:.5rem 0}}p{{color:gray}}code{{background:rgba(127,127,127,.15);padding:.1rem .4rem;border-radius:6px}}
.dot{{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;background:#2e8a5e;margin-right:.4rem}}</style></head>
<body><main><h1>{name}</h1><p><span class="dot"></span>This project's API is running.</p>
<p>Send requests with your project's API key in the <code>apikey</code> header, for example <code>/auth/v1/signup</code>, <code>/rest/v1/…</code> or <code>/functions/v1/…</code>.</p>
<p><a href="/v1/status">Service status</a></p></main></body></html>"""
        )

    @app.get("/v1/status", summary="Health of every Pawabase service", tags=["gateway"])
    async def status(ctx: HttpContext):
        async def check(name: str) -> tuple[str, dict[str, Any]]:
            try:
                response = await clients[name].get("/health", timeout=3)
                return name, {"ok": response.status_code == 200}
            except Exception as exc:
                return name, {"ok": False, "error": type(exc).__name__}

        results = dict(await asyncio.gather(*(check(name) for name in SERVICES)))
        return {"ok": all(r["ok"] for r in results.values()), "services": results}

    @app.get("/internal/v1/gateway/stats", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def stats(ctx: HttpContext):
        return {**proxy.stats, "key_lookups": resolver.lookups}

    @app.on_shutdown
    async def close() -> None:
        for client in clients.values():
            await client.aclose()
        await api.close()
        await quota.close()

    return app
