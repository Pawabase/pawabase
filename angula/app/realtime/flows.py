"""Handing client publications to flows without making the publisher wait for it.

A client publication becomes a ``realtime.message`` event that flows can react to. Posting it to the API inline would add the API's
latency to every publish, and an API outage would stall publishers. Instead publications go into a bounded queue that a worker drains:
when the queue is full the newest are dropped and counted, which is the right trade for realtime traffic.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from app.realtime.stats import Stats
from pawabase_core.clients import ServiceClient

logger = logging.getLogger("pawabase.angula.flows")


@dataclass(frozen=True)
class Forwarded:
    env: str
    channel: str
    event: str
    payload: Any
    user_id: str | None


class FlowForwarder:
    def __init__(self, api: ServiceClient, stats: Stats, *, size: int) -> None:
        self.api = api
        self.stats = stats
        self._queue: asyncio.Queue[Forwarded] = asyncio.Queue(maxsize=max(1, size))
        self._worker: asyncio.Task[None] | None = None

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="angula-flows")

    async def stop(self, grace: float = 2.0) -> None:
        if self._worker is None:
            return
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._queue.join(), grace)
        self._worker.cancel()
        with contextlib.suppress(BaseException):
            await self._worker
        self._worker = None

    def submit(self, item: Forwarded) -> bool:
        """Queue a publication. Returns ``False`` when the queue is full and it was dropped."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.stats.bump("forward_dropped", env=item.env)
            return False
        return True

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self.api.post(
                    "/internal/v1/events",
                    json={
                        "env": item.env,
                        "name": "realtime.message",
                        "payload": {
                            "channel": item.channel,
                            "event": item.event,
                            "payload": item.payload,
                            "user_id": item.user_id,
                        },
                        "actor": item.user_id,
                    },
                )
                self.stats.bump("forwarded", env=item.env)
            except Exception as exc:
                self.stats.bump("forward_failed", env=item.env)
                self.stats.error(item.env, item.channel, "forward", str(exc))
            finally:
                self._queue.task_done()
