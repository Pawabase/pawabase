"""Which headers cross the gateway, in each direction."""

from __future__ import annotations

from typing import Any

from app.proxy.clientip import chain_for_upstream
from app.proxy.request import KEY_HEADERS, Request
from pawabase_core.context import CONTEXT_HEADER
from pawabase_core.tokens import issue_context_token

HOP_BY_HOP = frozenset(
    {
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
)
#: Never passed on from a client: the key itself, anything that claims to be the platform, and the proxy chain, which the gateway rebuilds.
REQUEST_DROPPED = frozenset(
    {
        *KEY_HEADERS,
        "x-forwarded-for",
        "x-forwarded-proto",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-real-ip",
        "forwarded",
    }
)
#: Not passed on from an upstream: transport details that no longer apply once the body is re-streamed.
RESPONSE_DROPPED = (HOP_BY_HOP - {"content-length"}) | {"content-encoding"}


def request_headers(
    request: Request, context: Any, *, secret: str, request_id: str | None, peer: str | None
) -> dict[str, str]:
    """The headers sent to an upstream: the client's, minus what must not cross, plus the gateway's own."""
    forwarded = {
        name: value
        for name, value in request.headers.items()
        if name not in HOP_BY_HOP
        and name not in REQUEST_DROPPED
        and not name.startswith("x-pawabase-")
    }
    forwarded["x-forwarded-for"] = chain_for_upstream(request.client_ip, peer)
    scheme = request.scope.get("scheme", "http")
    forwarded["x-forwarded-proto"] = (
        request.headers.get("x-forwarded-proto", scheme) if request.via_proxy else scheme
    )
    forwarded["x-forwarded-host"] = request.host
    if request_id:
        forwarded["x-request-id"] = request_id
    if context is not None:
        forwarded[CONTEXT_HEADER] = issue_context_token(secret, context)
    return forwarded


def response_headers(items: Any, request_id: str | None) -> list[tuple[bytes, bytes]]:
    """An upstream's response headers as ASGI pairs, with transport headers removed and the request id added."""
    kept = [
        (k.encode("latin-1"), v.encode("latin-1"))
        for k, v in items
        if k.lower() not in RESPONSE_DROPPED
    ]
    if request_id and not any(k == b"x-request-id" for k, _ in kept):
        kept.append((b"x-request-id", request_id.encode("latin-1")))
    return kept
