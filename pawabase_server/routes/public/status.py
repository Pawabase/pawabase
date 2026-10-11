"""The public status page, and the health of each part of the server."""

from __future__ import annotations

import asyncio
from typing import Any

from sillo import HttpContext, SilloApp, html
from sillo.exceptions import HTTPException

from pawabase_server.observability.status import page, render


def register(app: SilloApp) -> None:
    async def page_data(env: str | None) -> dict[str, Any]:
        data = await page(env)
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

    async def run(name: str, check: Any) -> tuple[str, dict[str, Any]]:
        try:
            return name, {"ok": bool(await check())}
        except Exception as exc:
            return name, {"ok": False, "error": type(exc).__name__}

    @app.get("/v1/status", summary="Health of every part of the server", tags=["server"])
    async def services(ctx: HttpContext):
        checks: dict[str, Any] = app.state.get("health") or {}
        results = dict(await asyncio.gather(*(run(n, c) for n, c in checks.items())))
        return {"ok": all(r["ok"] for r in results.values()), "services": results}
