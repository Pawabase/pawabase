"""The public entry point, as ASGI middleware around the whole server.

For each request that arrives from outside, in this order, for HTTP and WebSocket alike:

1. refuse methods and paths no client should send, and anything the public route table does not claim;
2. resolve the API key into a platform context;
3. apply the key's and the environment's rules (:mod:`pawabase_server.edge.policy`);
4. apply rate limits and quotas (:mod:`pawabase_server.edge.admission`);
5. strip what must not be believed, sign the context, and hand the request to the application in-process.

There is no hop to another service: the signed context is how the application learns who is asking, and it trusts nothing a client could
have sent itself.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sillo.core.http import HttpContext
from sillo.http import get_request_id_from_request

from pawabase_server.core.context import SCOPE_KEY
from pawabase_server.edge import policy
from pawabase_server.edge.admission import IP_SCOPE, KEY_SCOPE, Admission, Rejected
from pawabase_server.edge.clientip import TrustedProxies, parse_address
from pawabase_server.edge.errors import (
    WS_UNAUTHORIZED,
    Refusal,
    close_websocket,
    respond,
    respond_json,
)
from pawabase_server.edge.headers import request_headers
from pawabase_server.edge.key_resolver import KeyRejected, KeyResolver
from pawabase_server.edge.metrics import Latency, new_stats
from pawabase_server.edge.networking import normalise_host, parse_hosts
from pawabase_server.edge.quotas import RequestQuota
from pawabase_server.edge.request import Request, plausible_key
from pawabase_server.edge.routing import Upstream, is_clean_path, is_open, route_for
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.edge")

KNOWN_KEY_ERRORS = ("missing_api_key", "invalid_api_key", "key_service_unavailable")
KEY_MESSAGES = {
    "missing_api_key": "Send a valid API key in the apikey header.",
    "invalid_api_key": "Send a valid API key in the apikey header.",
    "key_service_unavailable": "Try again shortly.",
}
BODYLESS = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})


def declared_length(headers: dict[str, str]) -> int | None:
    """The body length the client declared. Raises ``ValueError`` for a value that is not a single non-negative integer."""
    raw = headers.get("content-length")
    if raw is None:
        return None
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("invalid content-length")
    return int(raw)


class _BodyLimit:
    """Counts the request body as the application reads it, and ends the stream once it is over the limit.

    The application then sees a short body, and :meth:`Edge._http` answers 413 in place of whatever it made of it.
    """

    def __init__(self, receive: Any, limit: int) -> None:
        self._receive = receive
        self.limit = limit
        self.total = 0
        self.exceeded = False

    async def __call__(self) -> dict[str, Any]:
        if self.exceeded:
            return {"type": "http.request", "body": b"", "more_body": False}
        message = await self._receive()
        if message["type"] == "http.request":
            self.total += len(message.get("body", b""))
            if self.total > self.limit:
                self.exceeded = True
                return {"type": "http.request", "body": b"", "more_body": False}
        return message


class Edge:
    """ASGI middleware: what the outside world may reach, who is asking, and whether there is room."""

    def __init__(
        self,
        app: Any,
        settings: Settings,
        resolver: KeyResolver,
        *,
        rate_backend: Any = "memory",
        request_quota: RequestQuota | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
        self.resolver = resolver
        self.stats = new_stats()
        self.latency = Latency()
        self.trusted = TrustedProxies(settings.trusted_proxies)
        self.allowed_methods = frozenset(
            m.strip().upper() for m in settings.allowed_methods.split(",") if m.strip()
        )
        self.admission = Admission(
            settings, self.stats, rate_backend=rate_backend, request_quota=request_quota
        )
        #: Which environment each hostname serves (``PAWABASE_HOSTS``); empty maps nothing.
        self.hosts: dict[str, str] = parse_hosts(settings.hosts)
        #: Verified custom domains, refreshed by :class:`pawabase_server.edge.networking.DomainSync`.
        self.domains: dict[str, str] = {}

    # Names other parts of the server (status, usage, tests) reach for.
    @property
    def limiter(self):
        return self.admission.limiter

    @property
    def env_limits(self):
        return self.admission.env_limits

    @property
    def connections(self):
        return self.admission.connections

    @property
    def request_quota(self) -> RequestQuota:
        return self.admission.request_quota

    def served(self, host: str) -> str | None:
        """The environment a hostname serves: the configured mapping first, then verified custom domains."""
        name = normalise_host(host)
        return self.hosts.get(name) or self.domains.get(name)

    # ── dispatch ─────────────────────────────────────────────────────────

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)  # lifespan
            return
        path = scope.get("path", "")
        upstream = route_for(path)
        if upstream is None:
            if is_open(path):
                await self.app(scope, receive, send)
            elif scope["type"] == "websocket":
                await close_websocket(send, Refusal(404, "not_found"))
            else:
                await respond_json(send, 404, {"error": "not_found"})
            return
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, upstream)
        else:
            await self._http(scope, receive, send, upstream)

    def _request(self, scope: dict[str, Any]) -> Request:
        peer = scope.get("client")
        peer_ip = parse_address(peer[0] if peer else None)
        via_proxy = self.trusted.trusts(peer_ip)
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        return Request.of(scope, self.trusted.client_ip(scope, headers), via_proxy=via_proxy)

    def _request_id(self, request: Request) -> str | None:
        try:
            minted = get_request_id_from_request(HttpContext(request.scope, None))
        except Exception:
            minted = None
        return request.request_id(minted) or str(uuid.uuid4())

    async def _identify(
        self, request: Request, upstream: Upstream
    ) -> tuple[Any, dict[str, Any] | None, Refusal | None]:
        """Resolve the key. Returns ``(context, key info, refusal)``; a keyless route yields no context and no refusal."""
        raw = request.api_key()
        if raw is None:
            return (
                None,
                None,
                (
                    Refusal(
                        401,
                        "missing_api_key",
                        KEY_MESSAGES["missing_api_key"],
                        ws_code=WS_UNAUTHORIZED,
                    )
                    if upstream.key == "required"
                    else None
                ),
            )
        if upstream.key == "none":
            return None, None, None
        if not plausible_key(raw):
            self.stats["rejected_keys"] += 1
            return (
                None,
                None,
                Refusal(
                    401, "invalid_api_key", KEY_MESSAGES["invalid_api_key"], ws_code=WS_UNAUTHORIZED
                ),
            )
        env = request.environment_hint() or self.served(request.host)
        try:
            context, info = await self.resolver.resolve(raw, env=env)
        except KeyRejected as exc:
            self.stats["rejected_keys"] += 1
            reason = str(exc)
            if not reason or reason in KNOWN_KEY_ERRORS:
                reason = KEY_MESSAGES["invalid_api_key"]
            return None, None, Refusal(401, "invalid_api_key", reason, ws_code=WS_UNAUTHORIZED)
        except Exception:
            logger.warning("key lookup failed", exc_info=True)
            return (
                None,
                None,
                Refusal(
                    503,
                    "key_service_unavailable",
                    KEY_MESSAGES["key_service_unavailable"],
                    ws_code=1011,
                ),
            )
        return context, info, None

    def _screen(self, request: Request) -> Refusal | None:
        if request.method not in self.allowed_methods and request.scope["type"] == "http":
            return Refusal(
                405,
                "method_not_allowed",
                headers=((b"allow", ", ".join(sorted(self.allowed_methods)).encode()),),
            )
        if not is_clean_path(request.path):
            self.stats["bad_requests"] += 1
            return Refusal(400, "bad_path", "The path contains segments that are not allowed.")
        return None

    @staticmethod
    def _inner_scope(
        scope: dict[str, Any], request: Request, headers: dict[str, str]
    ) -> dict[str, Any]:
        """The scope the application sees: the vetted headers, and a query string without the API key."""
        inner = dict(scope)
        inner["headers"] = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
        inner["query_string"] = request.upstream_query().encode("latin-1")
        return inner

    # ── HTTP ─────────────────────────────────────────────────────────────

    async def _http(self, scope, receive, send, upstream: Upstream) -> None:
        request = self._request(scope)
        request_id = self._request_id(request)
        if (refusal := self._screen(request)) is not None:
            await respond(send, refusal, request_id)
            return
        context, info, refusal = await self._identify(request, upstream)
        if refusal is not None:
            await respond(send, refusal, request_id)
            return
        scope[IP_SCOPE] = request.client_ip
        if context is not None:
            scope[SCOPE_KEY] = context
            scope[KEY_SCOPE] = context.key_id
            if (
                refusal := policy.evaluate(
                    request, context, info or {}, served=self.served(request.host)
                )
            ) is not None:
                self.stats["refused"] += 1
                await respond(send, refusal, request_id)
                return
        if (refusal := await self.admission.admit_request(scope, receive, context)) is not None:
            await respond(send, refusal, request_id)
            return
        limit = self.settings.max_body_bytes
        try:
            length = declared_length(request.headers)
        except ValueError:
            self.stats["bad_requests"] += 1
            await respond_json(send, 400, {"error": "invalid_content_length"})
            return
        if length is not None and length > limit:
            # Refused before a byte of the body is read.
            await respond_json(send, 413, {"error": "payload_too_large", "limit": limit})
            return

        peer = scope.get("client")
        headers = request_headers(
            request,
            context,
            secret=self.settings.internal_secret,
            request_id=request_id,
            peer=peer[0] if peer else None,
        )
        inner = self._inner_scope(scope, request, headers)
        body = _BodyLimit(receive, limit)
        state: dict[str, Any] = {"replaced": False, "status": 200}

        async def guarded_send(message: dict[str, Any]) -> None:
            # A body that ran past the limit reached the application truncated; whatever it made of it is replaced by the refusal.
            if body.exceeded:
                if message["type"] == "http.response.start" and not state["replaced"]:
                    state["replaced"] = True
                    self.stats["bad_requests"] += 1
                    await respond_json(send, 413, {"error": "payload_too_large", "limit": limit})
                return
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            await send(message)

        started = time.perf_counter()
        await self.app(inner, body, guarded_send)
        self.latency.record(upstream.group, (time.perf_counter() - started) * 1000, state["status"])
        self.stats["proxied"] += 1

    # ── WebSocket ────────────────────────────────────────────────────────

    async def _websocket(self, scope, receive, send, upstream: Upstream) -> None:
        request = self._request(scope)
        first = await receive()  # websocket.connect
        if first["type"] != "websocket.connect":
            return
        if (refusal := self._screen(request)) is not None:
            await close_websocket(send, refusal)
            return
        context, info, refusal = await self._identify(request, upstream)
        if refusal is not None:
            await close_websocket(send, refusal)
            return
        scope[IP_SCOPE] = request.client_ip
        if (
            context is not None
            and (
                refusal := policy.evaluate(
                    request, context, info or {}, served=self.served(request.host)
                )
            )
            is not None
        ):
            self.stats["refused"] += 1
            await close_websocket(send, refusal)
            return
        try:
            async with self.admission.connection(context):
                peer = scope.get("client")
                headers = request_headers(
                    request,
                    context,
                    secret=self.settings.internal_secret,
                    request_id=self._request_id(request),
                    peer=peer[0] if peer else None,
                )
                headers = {k: v for k, v in headers.items() if not k.startswith("sec-websocket")}
                inner = self._inner_scope(scope, request, headers)
                replay = [first]

                async def receive_with_connect() -> dict[str, Any]:
                    return replay.pop() if replay else await receive()

                self.stats["websockets"] += 1
                await self.app(inner, receive_with_connect, send)
        except Rejected as rejected:
            await close_websocket(send, rejected.refusal)

    async def close(self) -> None:
        await self.admission.close()
