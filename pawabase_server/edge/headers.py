"""Which headers cross the edge into the server: the client's, minus what must not be believed, plus what the edge vouches for."""

from __future__ import annotations

from typing import Any

from pawabase_server.core.context import CONTEXT_HEADER
from pawabase_server.core.tokens import issue_context_token
from pawabase_server.edge.clientip import chain_for_upstream
from pawabase_server.edge.request import KEY_HEADERS, Request

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
#: Connection-level headers: they describe the hop the client made, not the request. ``host`` and ``content-length`` are kept.
CONNECTION_LEVEL = HOP_BY_HOP - {"host", "content-length"}


def request_headers(
    request: Request, context: Any, *, secret: str, request_id: str | None, peer: str | None
) -> dict[str, str]:
    """The headers the server sees: the client's, minus what must not cross, plus the edge's own."""
    forwarded = {
        name: value
        for name, value in request.headers.items()
        if name not in CONNECTION_LEVEL
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
