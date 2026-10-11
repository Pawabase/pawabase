"""What other Pawabase services ask the gateway: its counters and its limit usage. Service tokens only."""

from __future__ import annotations

from sillo import HttpContext, SilloApp

from app.config import GatewaySettings
from app.keys import KeyResolver
from app.proxy import GatewayProxy
from app.quotas import RequestQuota
from pawabase_core.service import SERVICE_ONLY


def register(
    app: SilloApp,
    settings: GatewaySettings,
    proxy: GatewayProxy,
    resolver: KeyResolver,
    quota: RequestQuota,
) -> None:
    @app.get("/internal/v1/gateway/stats", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def stats(ctx: HttpContext):
        return {
            **proxy.stats,
            "key_lookups": resolver.lookups,
            "key_stale_served": resolver.stale_served,
            "latency": proxy.latency.snapshot(),
            "circuits": proxy.upstreams.snapshot(),
            "custom_domains": len(proxy.domains),
        }

    @app.get("/internal/v1/gateway/usage", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def usage(ctx: HttpContext):
        return {
            **await quota.usage(),
            "environments": await proxy.env_limits.usage(),
            "connections": {
                "active": proxy.admission.active_connections(),
                "limit": settings.max_active_connections,
            },
        }
