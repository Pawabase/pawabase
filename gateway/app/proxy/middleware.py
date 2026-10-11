"""The gateway as ASGI middleware: for each routed request, who is asking, may they, is there room, and where does it go.

The order, for HTTP and WebSocket alike:

1. refuse methods and paths no client should send;
2. resolve the API key into a platform context;
3. apply the key's and the environment's rules (:mod:`app.proxy.policy`);
4. apply rate limits and quotas (:mod:`app.proxy.admission`);
5. strip what must not cross, sign the context, and stream the request and response through (:mod:`app.proxy.forward`,
   :mod:`app.proxy.bridge`).

Anything the route table does not claim passes through to the gateway's own routes untouched.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sillo.core.http import HttpContext
from sillo.http import get_request_id_from_request

from app.config import GatewaySettings
from app.hosts import normalise
from app.hosts import parse as parse_hosts
from app.keys import KeyRejected, KeyResolver
from app.proxy import bridge, policy
from app.proxy.admission import IP_SCOPE, KEY_SCOPE, Admission, Rejected
from app.proxy.clientip import TrustedProxies, parse_address
from app.proxy.errors import WS_UNAUTHORIZED, Refusal, close_websocket, respond
from app.proxy.forward import Forwarder
from app.proxy.headers import request_headers
from app.proxy.metrics import Latency, new_stats
from app.proxy.request import Request, plausible_key
from app.proxy.upstream import Upstreams
from app.quotas import RequestQuota
from app.routing import Upstream, is_clean_path, route_for
from pawabase_core.clients import ServiceError
from pawabase_core.context import SCOPE_KEY

logger = logging.getLogger("pawabase.gateway")

KNOWN_KEY_ERRORS = ("missing_api_key", "invalid_api_key", "key_service_unavailable")
KEY_MESSAGES = {
    "missing_api_key": "Send a valid API key in the apikey header.",
    "invalid_api_key": "Send a valid API key in the apikey header.",
    "key_service_unavailable": "Try again shortly.",
}


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
        self.ws_bases = ws_bases
        self.stats = new_stats()
        self.latency = Latency()
        self.trusted = TrustedProxies(settings.trusted_proxies)
        self.allowed_methods = frozenset(
            m.strip().upper() for m in settings.allowed_methods.split(",") if m.strip()
        )
        self.upstreams = Upstreams(clients, settings)
        self.forwarder = Forwarder(self.upstreams, settings, self.stats, self.latency)
        self.admission = Admission(
            settings, self.stats, rate_backend=rate_backend, request_quota=request_quota
        )
        #: Which environment each hostname serves (``PAWABASE_HOSTS``); empty maps nothing.
        self.hosts: dict[str, str] = parse_hosts(getattr(settings, "hosts", ""))
        #: Verified custom domains from Studio, refreshed by :class:`app.domains.DomainSync`.
        self.domains: dict[str, str] = {}
        self.app: Any = None

    # Names other parts of the gateway (status, usage, tests) reach for.
    @property
    def clients(self) -> dict[str, httpx.AsyncClient]:
        return self.upstreams.clients

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
        name = normalise(host)
        return self.hosts.get(name) or self.domains.get(name)

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
        return request.request_id(minted)

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
        except ServiceError:
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
        peer = scope.get("client")
        headers = request_headers(
            request,
            context,
            secret=self.settings.internal_secret,
            request_id=request_id,
            peer=peer[0] if peer else None,
        )
        await self.forwarder.forward(request, receive, send, upstream.service, headers, request_id)

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
                await self._bridge(request, scope, receive, send, upstream, context)
        except Rejected as rejected:
            await close_websocket(send, rejected.refusal)

    async def _bridge(
        self, request: Request, scope, receive, send, upstream: Upstream, context: Any
    ) -> None:
        peer = scope.get("client")
        forwarded = request_headers(
            request,
            context,
            secret=self.settings.internal_secret,
            request_id=self._request_id(request),
            peer=peer[0] if peer else None,
        )
        forwarded = {k: v for k, v in forwarded.items() if not k.startswith("sec-websocket")}
        query = request.upstream_query()
        url = self.ws_bases[upstream.service] + scope["path"] + (f"?{query}" if query else "")
        try:
            remote = await bridge.open_remote(url, forwarded, self.settings)
        except Exception as exc:
            self.stats["upstream_errors"] += 1
            await send(
                {
                    "type": "websocket.close",
                    "code": bridge.refusal_code(exc),
                    "reason": "upstream refused the connection",
                }
            )
            return
        self.stats["websockets"] += 1
        await send({"type": "websocket.accept"})
        await bridge.pump(receive, send, remote)

    async def close(self) -> None:
        await self.upstreams.close()
        await self.admission.close()
