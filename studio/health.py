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

from studio.support import Deps

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

    async def api_up() -> None:
        """The API is up when the gateway answers and the API itself reports healthy."""
        await alive()
        await component("api")

    async def compute() -> dict[str, Any]:
        # What an application's builders care about: can their users reach the API, sign in, and connect live.
        services = list(
            await asyncio.gather(
                probe("API", api_up),
                probe("Sign-in", lambda: component("auth")),
                probe("Realtime", lambda: component("realtime")),
            )
        )
        return {"services": services, "checked_at": time.time()}

    board = StatusBoard(compute)

    @app.get("/studio/status", exclude_from_schema=True)
    async def status(ctx: HttpContext):
        """Every service's health, for the status lights in Studio's top bar."""
        return JSONResponse(await board.get(), headers=NO_STORE)
