"""Declaring an endpoint once, for the API and for the blueprint.

``@endpoint`` registers a Pawabase function (the code) and records the route that
reaches it (the blueprint's ``routes`` entry), so the code, the route table and the
documentation cannot drift apart. The function itself is service-only
(``/functions/v1/<name>`` is closed to clients); the route carries the real policy.

    @endpoint("orders.list", "GET", "/dash/{store}/orders",
              area="orders", permission="orders.read", summary="List orders")
    async def orders_list(c: Ctx): ...
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any

from pawabase.functions import function

from .context import Ctx


@dataclass(slots=True)
class EndpointSpec:
    name: str
    method: str
    path: str
    area: str
    summary: str
    policy: Any
    permission: str | None
    input_fields: list[dict[str, Any]] | None
    rate_limit: dict[str, int]
    original: str  # the Sell4me route(s) this replaces, for the blueprint's coverage table
    tags: list[str] = field(default_factory=list)


REGISTRY: dict[str, EndpointSpec] = {}
#: Job functions (schedules, events, queued work): name -> what it does. For the blueprint document; they have no route.
JOBS: dict[str, str] = {}


def endpoint(
    name: str,
    method: str,
    path: str,
    *,
    area: str,
    summary: str,
    permission: str | None = None,
    policy: Any = "authenticated",
    fields: list[dict[str, Any]] | None = None,
    rate_limit: dict[str, int] | None = None,
    original: str = "",
    timeout: float = 30.0,
):
    """Register ``async def handler(c: Ctx)`` as function *name*, reachable at *method* *path*."""

    def decorator(handler):
        @functools.wraps(handler)
        async def wrapper(ctx):
            return await handler(Ctx(ctx))

        function(name, policy="service", description=summary, timeout=timeout)(wrapper)
        REGISTRY[name] = EndpointSpec(
            name=name,
            method=method.upper(),
            path=path,
            area=area,
            summary=summary,
            policy=policy,
            permission=permission,
            input_fields=fields,
            rate_limit=rate_limit or {},
            original=original,
            tags=[area],
        )
        return handler

    return decorator


def job(name: str, summary: str, *, timeout: float = 120.0):
    """Register a function that is only ever run by a schedule, an event or a flow."""

    def decorator(handler):
        @functools.wraps(handler)
        async def wrapper(ctx):
            return await handler(Ctx(ctx))

        function(name, policy="service", description=summary, timeout=timeout)(wrapper)
        JOBS[name] = summary
        return handler

    return decorator
