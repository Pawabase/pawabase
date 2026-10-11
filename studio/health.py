"""The status lights in Studio's top bar: is every part of the server up, and how fast does it answer.

Every open tab polls this, so the answer is computed once and shared for a few seconds, and concurrent callers wait for the same
computation instead of each running every check.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sillo import HttpContext, SilloApp
from sillo.responses import JSONResponse

from studio.support import Deps, recently_seen

PROBE_TIMEOUT = 3.0
CACHE_SECONDS = 3.0
NO_STORE = {"Cache-Control": "no-store"}


async def probe(label: str, check: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    """Run one health check with a deadline. Never raises: a failure is a ``down`` entry that says why."""
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=PROBE_TIMEOUT)
        status, detail = "up", None
    except Exception as exc:
        status, detail = "down", f"{type(exc).__name__}: {exc}"[:200]
    return {
        "name": label,
        "status": status,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "detail": detail,
    }


def worker_entry(label: str, kind: str, workers: list[dict[str, Any]] | None) -> dict[str, Any]:
    """What heartbeats say about a worker or scheduler. Processes that exited long ago leave rows behind; only recent ones count."""
    if workers is None:
        return {"name": label, "status": "unknown", "detail": "the API is unreachable"}
    mine = [w for w in workers if w.get("kind") == kind and recently_seen(w)]
    alive = [w for w in mine if w.get("alive")]
    if alive:
        return {"name": label, "status": "up", "detail": f"{len(alive)} running"}
    return {
        "name": label,
        "status": "down" if mine else "unknown",
        "detail": "stopped" if mine else "not running",
    }


class StatusBoard:
    """Computes the status payload at most once per ``ttl`` seconds, however many callers ask."""

    def __init__(
        self, compute: Callable[[], Awaitable[dict[str, Any]]], ttl: float = CACHE_SECONDS
    ) -> None:
        self._compute = compute
        self._ttl = ttl
        self._value: dict[str, Any] | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, Any]:
        if self._value is not None and time.monotonic() - self._at < self._ttl:
            return self._value
        async with self._lock:
            if self._value is not None and time.monotonic() - self._at < self._ttl:
                return self._value
            self._value = await self._compute()
            self._at = time.monotonic()
            return self._value


def register(app: SilloApp, deps: Deps) -> None:
    core = deps.core

    async def component(name: str) -> None:
        """One part of the server, as the server itself reports it."""
        report = await core.get("/v1/status")
        if not report["services"].get(name, {}).get("ok"):
            raise RuntimeError(f"{name} reports it is not healthy")

    async def alive() -> None:
        await core.get("/health")

    async def compute() -> dict[str, Any]:
        services = list(
            await asyncio.gather(
                probe("Gateway", alive),
                probe("API", lambda: component("api")),
                probe("Auth", lambda: component("auth")),
                probe("Realtime", lambda: component("realtime")),
            )
        )
        try:
            workers = (await core.request("GET", "/platform/v1/workers")).get("data", [])
        except Exception:
            workers = None
        services += [
            worker_entry("Worker", "worker", workers),
            worker_entry("Scheduler", "scheduler", workers),
        ]
        return {"services": services, "checked_at": time.time()}

    board = StatusBoard(compute)

    @app.get("/studio/status", exclude_from_schema=True)
    async def status(ctx: HttpContext):
        """Every service's health, for the status lights in Studio's top bar."""
        return JSONResponse(await board.get(), headers=NO_STORE)
