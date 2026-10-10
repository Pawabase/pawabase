"""Keeps the gateway's list of verified custom domains current."""

from __future__ import annotations

import asyncio
import logging

from pawabase_core.clients import ServiceClient

log = logging.getLogger("pawabase.gateway.domains")


class DomainSync:
    """Fetches the verified hostnames Studio holds into ``proxy.domains`` every ``every`` seconds."""

    def __init__(self, api: ServiceClient, proxy, every: float = 30) -> None:
        self.api = api
        self.proxy = proxy
        self.every = every
        self.task: asyncio.Task[None] | None = None

    async def refresh(self) -> int:
        try:
            rows = (await self.api.get("/internal/v1/domains")).get("data", [])
        except Exception as exc:
            log.debug("domain refresh skipped: %s", exc)
            return len(self.proxy.domains)
        self.proxy.domains = {row["hostname"].lower(): row["env"] for row in rows}
        return len(self.proxy.domains)

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.every)

    def start(self) -> None:
        if self.task is None and self.every > 0:
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
