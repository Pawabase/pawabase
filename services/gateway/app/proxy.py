"""Forwarding requests and WebSockets to the internal services.

For every proxied request the gateway:

1. finds the upstream from the route table (``/internal`` and ``/admin`` are
   never reachable from outside);
2. resolves the API key (``apikey`` or ``x-api-key`` header, or ``?apikey=`` for
   browser navigations and WebSockets) into a platform context;
3. enforces the environment's allowed origins for publishable keys, and the
   rate limit (Sillo's limiter, per key or client address);
4. removes anything a client could use to impersonate the platform
   (``x-pawabase-*`` headers) and the raw key itself;
5. adds the signed ``X-Pawabase-Context``, ``X-Request-ID`` and forwarding
   headers, and streams the request and the response through.

Streaming uses ``httpx`` directly: a proxy passes raw bytes and headers,
which Sillo's ``HTTPClient`` (a JSON client) is not built for.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qsl, urlencode

import httpx
from sillo.core.http import HttpContext
from sillo.http import get_request_id_from_request
from sillo.security import RateLimitConfig, RateLimitMiddleware

from app.config import GatewaySettings
from app.keys import KeyRejected, KeyResolver
from app.routing import route_for
from pawabase_kit.clients import ServiceError
from pawabase_kit.context import CONTEXT_HEADER, SCOPE_KEY
from pawabase_kit.telemetry import note
from pawabase_kit.tokens import issue_context_token

logger = logging.getLogger("pawabase.gateway")

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
STRIPPED_REQUEST = {"apikey", "x-api-key"}
KEY_SCOPE = "pawabase.gateway.key"


async def _json(
    send: Any, status: int, body: dict[str, Any], headers: list[tuple[bytes, bytes]] | None = None
) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def _limit_key(ctx: HttpContext) -> str:
    key_id = ctx.scope.get(KEY_SCOPE)
    if key_id:
        return f"key:{key_id}"
    forwarded = ctx.headers.get("x-forwarded-for")
    client = ctx.scope.get("client")
    return "ip:" + (
        forwarded.split(",")[0].strip() if forwarded else (client[0] if client else "unknown")
    )


class GatewayProxy:
    """ASGI middleware that proxies routed paths and passes everything else on."""

    def __init__(
        self,
        settings: GatewaySettings,
        resolver: KeyResolver,
        clients: dict[str, httpx.AsyncClient],
        ws_bases: dict[str, str],
        *,
        rate_backend: Any = "memory",
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.clients = clients
        self.ws_bases = ws_bases
        self.limiter = RateLimitMiddleware(
            RateLimitConfig(
                limit=settings.rate_limit,
                window=settings.rate_window,
                namespace="gw",
                backend=rate_backend,
                key_func=_limit_key,
            )
        )
        self.app: Any = None
        self.stats = {
            "proxied": 0,
            "rejected_keys": 0,
            "rate_limited": 0,
            "upstream_errors": 0,
            "websockets": 0,
        }

    # ── common ───────────────────────────────────────────────────────────

    @staticmethod
    def _headers(scope: dict[str, Any]) -> dict[str, str]:
        return {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }

    @staticmethod
    def _key(scope: dict[str, Any], headers: dict[str, str]) -> str | None:
        for name in ("apikey", "x-api-key"):
            if headers.get(name):
                return headers[name]
        for name, value in parse_qsl(scope.get("query_string", b"").decode("latin-1")):
            if name == "apikey" and value:
                return value
        return None

    @staticmethod
    def _query_without_key(scope: dict[str, Any]) -> str:
        pairs = [
            (k, v)
            for k, v in parse_qsl(
                scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True
            )
            if k != "apikey"
        ]
        return urlencode(pairs)

    async def _context(
        self, scope: dict[str, Any], headers: dict[str, str], mode: str
    ) -> tuple[Any, dict[str, Any] | None, str | None]:
        """Returns (context, key info, error)."""
        raw = self._key(scope, headers)
        if raw is None:
            return None, None, ("missing_api_key" if mode == "required" else None)
        if mode == "none":
            return None, None, None
        try:
            context, info = await self.resolver.resolve(raw)
        except KeyRejected:
            self.stats["rejected_keys"] += 1
            return None, None, "invalid_api_key"
        except ServiceError:
            return None, None, "key_service_unavailable"
        return context, info, None

    def _forward_headers(
        self, scope: dict[str, Any], headers: dict[str, str], context: Any
    ) -> dict[str, str]:
        forwarded = {
            k: v
            for k, v in headers.items()
            if k not in HOP_BY_HOP and k not in STRIPPED_REQUEST and not k.startswith("x-pawabase-")
        }
        client = scope.get("client")
        address = client[0] if client else ""
        forwarded["x-forwarded-for"] = (
            f"{headers['x-forwarded-for']}, {address}"
            if headers.get("x-forwarded-for")
            else address
        )
        forwarded["x-forwarded-proto"] = headers.get(
            "x-forwarded-proto", scope.get("scheme", "http")
        )
        forwarded["x-forwarded-host"] = headers.get("host", "")
        try:
            request_id = (
                get_request_id_from_request(HttpContext(scope, None))
                if scope["type"] == "http"
                else None
            )
        except Exception:
            request_id = None
        if request_id:
            forwarded["x-request-id"] = request_id
        if context is not None:
            forwarded[CONTEXT_HEADER] = issue_context_token(self.settings.internal_secret, context)
        return forwarded

    # ── dispatch ─────────────────────────────────────────────────────────

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        upstream = route_for(scope.get("path", ""))
        if upstream is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, upstream)
            return
        await self._http(scope, receive, send, upstream)

    async def _http(self, scope, receive, send, upstream) -> None:
        headers = self._headers(scope)
        context, info, error = await self._context(scope, headers, upstream.key)
        if error:
            status = 503 if error == "key_service_unavailable" else 401
            await _json(
                send,
                status,
                {
                    "error": error,
                    "message": "Send a valid project API key in the apikey header."
                    if status == 401
                    else "Try again shortly.",
                },
            )
            return
        if context is not None:
            scope[SCOPE_KEY] = context
            scope[KEY_SCOPE] = context.key_id
            origins = (info or {}).get("cors_origins") or []
            origin = headers.get("origin")
            if context.role == "anon" and origins and origin and origin not in origins:
                await _json(
                    send,
                    403,
                    {"error": "origin_not_allowed", "message": f"{origin} may not use this key."},
                )
                return
        result = await self.limiter.check(HttpContext(scope, receive))
        if result is not None and not result.allowed:
            self.stats["rate_limited"] += 1
            note("rate_limited", True)
            await _json(
                send,
                429,
                {"error": "rate_limit_exceeded", "retry_after": max(int(result.retry_after), 1)},
                [(b"retry-after", str(max(int(result.retry_after), 1)).encode())],
            )
            return
        await self._forward(
            scope, receive, send, upstream.service, self._forward_headers(scope, headers, context)
        )

    async def _body(self, receive) -> AsyncIterator[bytes]:
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.settings.max_body_bytes:
                raise ValueError("body too large")
            if chunk:
                yield chunk
            if not message.get("more_body"):
                return

    async def _forward(self, scope, receive, send, service: str, headers: dict[str, str]) -> None:
        client = self.clients[service]
        query = self._query_without_key(scope)
        url = scope["path"] + (f"?{query}" if query else "")
        method = scope["method"]
        has_body = method not in ("GET", "HEAD", "OPTIONS", "DELETE") or "content-length" in {
            k.decode().lower() for k, _ in scope.get("headers", [])
        }
        request = client.build_request(
            method,
            url,
            headers=headers,
            content=self._body(receive) if has_body else None,
            timeout=self.settings.upstream_timeout,
        )
        try:
            response = await client.send(request, stream=True)
        except ValueError:
            await _json(send, 413, {"error": "payload_too_large"})
            return
        except httpx.TimeoutException:
            self.stats["upstream_errors"] += 1
            await _json(send, 504, {"error": "upstream_timeout", "service": service})
            return
        except httpx.TransportError as exc:
            self.stats["upstream_errors"] += 1
            logger.warning("upstream %s unreachable: %s", service, exc)
            await _json(send, 502, {"error": "upstream_unavailable", "service": service})
            return
        self.stats["proxied"] += 1
        note("upstream", service)
        try:
            response_headers = [
                (k.encode("latin-1"), v.encode("latin-1"))
                for k, v in response.headers.multi_items()
                if k.lower() not in HOP_BY_HOP - {"content-length"}
                and k.lower() != "content-encoding"
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": response_headers,
                }
            )
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await response.aclose()

    # ── websockets ───────────────────────────────────────────────────────

    async def _websocket(self, scope, receive, send, upstream) -> None:
        import websockets

        headers = self._headers(scope)
        context, _, error = await self._context(scope, headers, upstream.key)
        first = await receive()  # websocket.connect
        if first["type"] != "websocket.connect":
            return
        if error:
            await send({"type": "websocket.close", "code": 4001, "reason": error})
            return
        forwarded = self._forward_headers(scope, headers, context)
        forwarded = {k: v for k, v in forwarded.items() if not k.startswith("sec-websocket")}
        query = self._query_without_key(scope)
        url = self.ws_bases[upstream.service] + scope["path"] + (f"?{query}" if query else "")
        try:
            remote = await websockets.connect(
                url, additional_headers=forwarded, open_timeout=10, max_size=2**20
            )
        except Exception as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            await send(
                {
                    "type": "websocket.close",
                    "code": 4003 if code in (401, 403) else 1011,
                    "reason": "upstream refused the connection",
                }
            )
            return
        self.stats["websockets"] += 1
        await send({"type": "websocket.accept"})

        async def client_to_remote() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    await remote.close()
                    return
                if message.get("text") is not None:
                    await remote.send(message["text"])
                elif message.get("bytes") is not None:
                    await remote.send(message["bytes"])

        async def remote_to_client() -> None:
            try:
                async for data in remote:
                    if isinstance(data, bytes):
                        await send({"type": "websocket.send", "bytes": data})
                    else:
                        await send({"type": "websocket.send", "text": data})
            finally:
                code = remote.close_code or 1000
                with contextlib.suppress(Exception):
                    await send({"type": "websocket.close", "code": code})

        tasks = [asyncio.create_task(client_to_remote()), asyncio.create_task(remote_to_client())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        with contextlib.suppress(Exception):
            await remote.close()
