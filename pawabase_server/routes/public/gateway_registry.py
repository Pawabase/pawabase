"""What Studio, a load balancer, and the Compute Controller each need about the gateway fleet.

Three different audiences, three different trust levels, on purpose:

* ``/health/ready`` (mounted on the public listener, no key — see ``edge/routing.py``'s ``OPEN_PATHS``): *this* instance's own
  readiness, for a load balancer's health check. It answers from memory (no database round trip), so it stays fast and correct even
  if the database the registry itself depends on is unreachable.
* ``/internal/v1/gateway/instances`` (service token): the whole fleet, as this instance's database sees it, for Studio's instance
  list and for the Compute Controller's reconciliation loop.
* ``/internal/v1/gateway/drain`` (service token): ask one instance (by id) to stop taking new traffic. Any instance can carry the
  request for any other — it only writes a row — so the caller need not reach the draining instance directly.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sillo import HttpContext, SilloApp
from sillo.exceptions import HTTPException

from pawabase_server.core.service import SERVICE_ONLY
from pawabase_server.db.models import GatewayInstance
from pawabase_server.gateway.registry import GatewayRegistry
from pawabase_server.settings import Settings


class DrainBody(BaseModel):
    id: str


def register(app: SilloApp, settings: Settings) -> None:
    @app.get("/health/ready", exclude_from_schema=True)
    async def ready(ctx: HttpContext) -> dict[str, Any]:
        registry: GatewayRegistry | None = app.state.get("gateway_registry")
        if registry is None:
            # No registry means this role never serves the gateway (worker, scheduler, the functions service): there is nothing to
            # be "ready" for in this sense, and no load balancer should be pointed at it, so answering not-ready is the honest thing.
            return {"status": "not_ready", "reason": "this process does not serve gateway traffic"}
        if not await registry.is_ready():
            return {"status": "draining", "instance": registry.id}
        return {"status": "ready", "instance": registry.id}

    @app.get("/internal/v1/gateway/instances", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def instances(ctx: HttpContext) -> dict[str, Any]:
        rows = await GatewayInstance.all().order_by("host")
        return {
            "data": [row.describe() | {"healthy": row.is_healthy(stale_after=settings.gateway_stale_seconds)} for row in rows],
            "stale_after_seconds": settings.gateway_stale_seconds,
        }

    @app.post("/internal/v1/gateway/drain", auth=SERVICE_ONLY, request_model=DrainBody, exclude_from_schema=True)
    async def drain(ctx: HttpContext, body: DrainBody) -> dict[str, Any]:
        from datetime import UTC, datetime

        updated = await GatewayInstance.filter(id=body.id).update(draining=True, drain_requested_at=datetime.now(UTC))
        if not updated:
            raise HTTPException(status_code=404, detail=f"no gateway instance {body.id!r}")
        return {"drained": body.id}

    @app.delete("/internal/v1/gateway/instances/{instance_id}", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def remove(ctx: HttpContext, instance_id: str) -> dict[str, Any]:
        """Remove a row outright: for an instance the orchestrator confirmed is gone, not for one that is merely draining."""
        removed = await GatewayInstance.filter(id=instance_id).delete()
        return {"removed": bool(removed)}
