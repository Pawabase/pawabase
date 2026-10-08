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
import fnmatch
import ipaddress
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
from app.quotas import RequestQuota
from app.routing import route_for
from pawabase_core.clients import ServiceError
from pawabase_core.context import CONTEXT_HEADER, SCOPE_KEY
from pawabase_core.telemetry import note
from pawabase_core.tokens import issue_context_token

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
        request_quota: RequestQuota | None = None,
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
        self.request_quota = request_quota or RequestQuota()
        self.connections = (
            asyncio.BoundedSemaphore(settings.max_active_connections)
            if settings.max_active_connections > 0
            else None
        )
        self.app: Any = None
        self.stats = {
            "proxied": 0,
            "rejected_keys": 0,
            "rate_limited": 0,
            "upstream_errors": 0,
            "websockets": 0,
            "quota_rejected": 0,
            "connections_rejected": 0,
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

    @classmethod
    def _scope(cls, scope: dict[str, Any], headers: dict[str, str]) -> str | None:
        """The environment the caller says the key is for, if it says: optional, since a key names its own."""
        env = headers.get("x-environment")
        for name, value in parse_qsl(scope.get("query_string", b"").decode("latin-1")):
            if name == "environment" and value:
                env = value
        return env

    @classmethod
    def _query_without_key(cls, scope: dict[str, Any]) -> str:
        # Only the apikey itself is a secret; the environment hint is kept
        # so upstream handlers (e.g. the health check) that read them
        # themselves still see them.
        dropped = {"apikey"}
        pairs = [
            (k, v)
            for k, v in parse_qsl(
                scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True
            )
            if k not in dropped
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
        env = self._scope(scope, headers)
        try:
            context, info = await self.resolver.resolve(raw, env=env)
        except KeyRejected as exc:
            self.stats["rejected_keys"] += 1
            return None, None, str(exc) or "invalid_api_key"
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

    @staticmethod
    def _key_allowed(scope: dict[str, Any], info: dict[str, Any]) -> str | None:
        """Return a stable rejection code when a key's gateway restrictions fail."""
        client = scope.get("client")
        address = client[0] if client else None
        allowed_ips = info.get("allowed_ips") or []
        if allowed_ips:
            try:
                ip = ipaddress.ip_address(address or "")
                if not any(ip in ipaddress.ip_network(item, strict=False) for item in allowed_ips):
                    return "ip_not_allowed"
            except ValueError:
                # Bad stored rules must fail closed; management validation prevents this.
                return "ip_not_allowed"
        rules = info.get("allowed_routes") or []
        if rules:
            method, path = scope.get("method", "GET").upper(), scope.get("path", "")
            for rule in rules:
                parts = rule.split(None, 1)
                rule_method, pattern = (
                    (parts[0].upper(), parts[1]) if len(parts) == 2 else ("*", parts[0])
                )
                if (rule_method in ("*", method)) and fnmatch.fnmatchcase(path, pattern):
                    break
            else:
                return "route_not_allowed"
        return None

    @staticmethod
    def _environment_gate(scope: dict[str, Any], context: Any, info: dict[str, Any]) -> tuple[str, str, int] | None:
        """Environment-wide settings: ``(code, message, retry_after)`` when the request is refused.

        Maintenance mode refuses everything except secret keys (unless the
        environment says otherwise); the IP allowlist restricts secret keys,
        because browsers on publishable keys have no stable address.
        """
        maintenance = info.get("maintenance") or {}
        if maintenance.get("enabled") and not (
            context.role == "service" and maintenance.get("allow_secret_keys", True)
        ):
            return (
                "maintenance",
                maintenance.get("message") or "This service is down for maintenance.",
                int(maintenance.get("retry_after") or 300),
            )
        allowlist = info.get("ip_allowlist") or []
        if allowlist and context.role == "service":
            client = scope.get("client")
            try:
                ip = ipaddress.ip_address(client[0] if client else "")
                if any(ip in ipaddress.ip_network(item, strict=False) for item in allowlist):
                    return None
            except ValueError:
                pass
            return "ip_not_allowed", "Secret keys may not be used from this address.", 0
        return None

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
            KNOWN_CODES = ("missing_api_key", "invalid_api_key", "key_service_unavailable")
            status = 503 if error == "key_service_unavailable" else 401
            # `error` is either a fixed code, or (from KeyRejected) a specific,
            # user-facing reason such as an environment mismatch.
            code = error if error in KNOWN_CODES else "invalid_api_key"
            message = (
                error
                if code not in ("missing_api_key", "key_service_unavailable") and error != code
                else {
                    "missing_api_key": "Send a valid API key in the apikey header.",
                    "invalid_api_key": "Send a valid API key in the apikey header.",
                    "key_service_unavailable": "Try again shortly.",
                }[code]
            )
            await _json(send, status, {"error": code, "message": message})
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
            if denied := self._key_allowed(scope, info or {}):
                await _json(
                    send,
                    403,
                    {"error": denied, "message": "This API key is not allowed for this request."},
                )
                return
            if gate := self._environment_gate(scope, context, info or {}):
                code, message, retry_after = gate
                if code == "maintenance":
                    await _json(
                        send,
                        503,
                        {"error": code, "message": message, "retry_after": retry_after},
                        [(b"retry-after", str(retry_after).encode())],
                    )
                else:
                    await _json(send, 403, {"error": code, "message": message})
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
        if not await self.request_quota.acquire():
            self.stats["quota_rejected"] += 1
            await _json(
                send,
                429,
                {
                    "error": "request_quota_exceeded",
                    "message": "The installation request quota has been reached.",
                },
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
        context, info, error = await self._context(scope, headers, upstream.key)
        first = await receive()  # websocket.connect
        if first["type"] != "websocket.connect":
            return
        if error:
            await send({"type": "websocket.close", "code": 4001, "reason": error})
            return
        if context is not None and (denied := self._key_allowed(scope, info or {})):
            await send({"type": "websocket.close", "code": 4003, "reason": denied})
            return
        if context is not None and (gate := self._environment_gate(scope, context, info or {})):
            await send(
                {"type": "websocket.close", "code": 4503 if gate[0] == "maintenance" else 4003, "reason": gate[0]}
            )
            return
        if self.connections is not None and self.connections.locked():
            self.stats["connections_rejected"] += 1
            await send(
                {"type": "websocket.close", "code": 4429, "reason": "connection limit reached"}
            )
            return
        if self.connections is not None:
            await self.connections.acquire()
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
            if self.connections is not None:
                self.connections.release()
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
        if self.connections is not None:
            self.connections.release()
