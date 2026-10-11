"""Talking to the internal services: connection reuse, retries, and a circuit breaker per service.

Two things keep one sick service from taking the gateway down with it:

* a **retry** for requests that are safe to repeat (no body, an idempotent method) when the service could not be reached at all, so a
  connection reset during a deploy is invisible to the client;
* a **circuit breaker**: after ``failures`` consecutive transport failures the service is declared down and requests are answered at once
  instead of each waiting out a timeout. After ``cooldown`` seconds one request is let through to find out whether it is back.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import GatewaySettings

logger = logging.getLogger("pawabase.gateway.upstream")

IDEMPOTENT = frozenset({"GET", "HEAD", "OPTIONS"})
#: Failures that mean nothing reached the service, so repeating the request cannot do it twice.
UNREACHED = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class CircuitOpen(Exception):
    """The service's circuit is open. ``retry_after`` is the seconds until it will be tried again."""

    def __init__(self, service: str, retry_after: float) -> None:
        super().__init__(f"{service} is unavailable")
        self.service = service
        self.retry_after = retry_after


@dataclass
class Breaker:
    failures: int
    cooldown: float
    clock: Callable[[], float] = time.monotonic
    consecutive: int = 0
    opened_at: float | None = None
    probing: bool = False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at >= self.cooldown:
            return "half-open"
        return "open"

    def before(self) -> float | None:
        """``None`` when a request may go ahead, else the seconds to wait. In half-open state exactly one request is the probe."""
        if self.opened_at is None:
            return None
        remaining = self.cooldown - (self.clock() - self.opened_at)
        if remaining > 0:
            return remaining
        if self.probing:
            return 1.0
        self.probing = True
        return None

    def success(self) -> None:
        self.consecutive = 0
        self.opened_at = None
        self.probing = False

    def failure(self) -> None:
        self.consecutive += 1
        self.probing = False
        if self.failures > 0 and self.consecutive >= self.failures:
            if self.opened_at is None:
                logger.warning("circuit opened after %d consecutive failures", self.consecutive)
            self.opened_at = self.clock()


class Upstreams:
    """The clients for each internal service, wrapped with a retry and a breaker."""

    def __init__(
        self,
        clients: dict[str, httpx.AsyncClient],
        settings: GatewaySettings,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.clients = clients
        self.retries = max(0, settings.upstream_retries)
        self.breakers = {
            name: Breaker(settings.breaker_failures, settings.breaker_cooldown, clock)
            for name in clients
        }

    def client(self, service: str) -> httpx.AsyncClient:
        return self.clients[service]

    async def send(
        self, service: str, request: httpx.Request, *, repeatable: bool
    ) -> httpx.Response:
        """Send *request* (streaming the response). Raises :class:`CircuitOpen`, or the transport error once retries are spent.

        ``repeatable`` says the request has no body that was consumed and an idempotent method; only then is it retried.
        """
        breaker = self.breakers[service]
        wait = breaker.before()
        if wait is not None:
            raise CircuitOpen(service, wait)
        attempts = 1 + (self.retries if repeatable and request.method in IDEMPOTENT else 0)
        last: Exception | None = None
        for _ in range(attempts):
            try:
                response = await self.clients[service].send(request, stream=True)
            except UNREACHED as exc:
                last = exc
                continue
            except httpx.TransportError:
                breaker.failure()
                raise
            except Exception:
                breaker.probing = False  # not the service's fault (for example a body that was too large): a probe must not stay claimed
                raise
            if response.status_code >= 502 and response.status_code != 501:
                # The service answered but says it is broken (for example its own upstream is down). Count it, but pass the answer on.
                breaker.failure()
            else:
                breaker.success()
            return response
        breaker.failure()
        assert last is not None
        raise last

    def snapshot(self) -> dict[str, Any]:
        return {
            name: {"state": b.state, "consecutive_failures": b.consecutive}
            for name, b in self.breakers.items()
        }

    async def close(self) -> None:
        for client in self.clients.values():
            await client.aclose()
