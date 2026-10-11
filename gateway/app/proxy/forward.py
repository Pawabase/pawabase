"""Streaming an HTTP request to a service and its response back."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import GatewaySettings
from app.proxy.errors import respond_json
from app.proxy.headers import response_headers
from app.proxy.metrics import Latency
from app.proxy.request import Request
from app.proxy.upstream import CircuitOpen, Upstreams
from pawabase_core.telemetry import note

logger = logging.getLogger("pawabase.gateway.forward")

BODYLESS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})


class BodyTooLarge(Exception):
    """The request body went past ``max_body_bytes``. A distinct type so no other error is ever reported as a 413."""


def declared_length(headers: dict[str, str]) -> int | None:
    """The body length the client declared. Raises ``ValueError`` for a value that is not a single non-negative integer."""
    raw = headers.get("content-length")
    if raw is None:
        return None
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("invalid content-length")
    return int(raw)


class Forwarder:
    def __init__(
        self,
        upstreams: Upstreams,
        settings: GatewaySettings,
        stats: dict[str, int],
        latency: Latency,
    ) -> None:
        self.upstreams = upstreams
        self.settings = settings
        self.stats = stats
        self.latency = latency

    async def _body(self, receive: Any) -> AsyncIterator[bytes]:
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.settings.max_body_bytes:
                raise BodyTooLarge
            if chunk:
                yield chunk
            if not message.get("more_body"):
                return

    async def forward(
        self,
        request: Request,
        receive: Any,
        send: Any,
        service: str,
        headers: dict[str, str],
        request_id: str | None,
    ) -> None:
        scope = request.scope
        try:
            length = declared_length(request.headers)
        except ValueError:
            self.stats["bad_requests"] += 1
            await respond_json(send, 400, {"error": "invalid_content_length"})
            return
        if length is not None and length > self.settings.max_body_bytes:
            # Refused before a byte of the body is read or sent anywhere.
            await respond_json(
                send, 413, {"error": "payload_too_large", "limit": self.settings.max_body_bytes}
            )
            return
        query = request.upstream_query()
        url = scope["path"] + (f"?{query}" if query else "")
        has_body = (
            request.method not in BODYLESS
            or length is not None
            or "transfer-encoding" in request.headers
        )
        client = self.upstreams.client(service)
        built = client.build_request(
            request.method,
            url,
            headers=headers,
            content=self._body(receive) if has_body else None,
            timeout=self.settings.upstream_timeout,
        )
        started = time.perf_counter()
        try:
            response = await self.upstreams.send(service, built, repeatable=not has_body)
        except BodyTooLarge:
            await respond_json(
                send, 413, {"error": "payload_too_large", "limit": self.settings.max_body_bytes}
            )
            return
        except CircuitOpen as exc:
            self.stats["circuit_open"] += 1
            retry = max(1, round(exc.retry_after))
            await respond_json(
                send,
                503,
                {"error": "upstream_unavailable", "service": service, "retry_after": retry},
                ((b"retry-after", str(retry).encode()),),
            )
            return
        except httpx.TimeoutException:
            self.stats["upstream_errors"] += 1
            await respond_json(send, 504, {"error": "upstream_timeout", "service": service})
            return
        except httpx.TransportError as exc:
            self.stats["upstream_errors"] += 1
            logger.warning("upstream %s unreachable: %s", service, exc)
            await respond_json(send, 502, {"error": "upstream_unavailable", "service": service})
            return
        self.latency.record(service, (time.perf_counter() - started) * 1000, response.status_code)
        self.stats["proxied"] += 1
        note("upstream", service)
        await self._relay(response, send, request_id)

    async def _relay(self, response: httpx.Response, send: Any, request_id: str | None) -> None:
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": response_headers(response.headers.multi_items(), request_id),
                }
            )
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except (OSError, RuntimeError):
            # The client went away mid-response. Nothing can be sent to it; stop reading from the service so its connection is freed.
            self.stats["client_aborted"] += 1
        finally:
            await response.aclose()
