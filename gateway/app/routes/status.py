"""The public status page, and the health of each internal service."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from sillo import HttpContext, SilloApp, html
from sillo.exceptions import HTTPException

from app.status import fetch, render
from pawabase_core.clients import ServiceClient

SERVICES = ("api", "akountz", "angula")
CHECK_TIMEOUT = 3


async def check(clients: dict[str, httpx.AsyncClient], name: str) -> tuple[str, dict[str, Any]]:
    try:
        response = await clients[name].get("/health", timeout=CHECK_TIMEOUT)
        return name, {"ok": response.status_code == 200}
    except Exception as exc:
        return name, {"ok": False, "error": type(exc).__name__}


def register(app: SilloApp, api: ServiceClient, clients: dict[str, httpx.AsyncClient]) -> None:
    async def page_data(env: str | None) -> dict[str, Any]:
        data = await fetch(api, env)
        if data is None:
            raise HTTPException(status_code=404, detail="no public status page")
        return data

    @app.get("/status", exclude_from_schema=True)
    async def default_page(ctx: HttpContext):
        return html(render(await page_data(None)))

    @app.get("/status/{env}", exclude_from_schema=True)
    async def environment_page(ctx: HttpContext, env: str):
        """The page, or its data as JSON when the name ends in ``.json``."""
        if env.endswith(".json"):
            return await page_data(env.removesuffix(".json"))
        return html(render(await page_data(env)))

    @app.get("/v1/status", summary="Health of every Pawabase service", tags=["gateway"])
    async def services(ctx: HttpContext):
        results = dict(await asyncio.gather(*(check(clients, name) for name in SERVICES)))
        return {"ok": all(r["ok"] for r in results.values()), "services": results}
