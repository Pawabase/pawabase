"""Refusing a request: one description that becomes an HTTP response or a WebSocket close."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: WebSocket close codes the gateway uses (4000-4999 are for applications).
WS_UNAUTHORIZED = 4001
WS_FORBIDDEN = 4003
WS_MAINTENANCE = 4503
WS_CAPACITY = 4429
WS_BAD_GATEWAY = 1011


@dataclass(frozen=True)
class Refusal:
    """Why a request was not forwarded.

    ``error`` is a stable code clients can branch on; ``message`` is for people. ``ws_code`` is the close code when the request was a
    WebSocket upgrade. ``extra`` is merged into the JSON body, and ``headers`` added to the response.
    """

    status: int
    error: str
    message: str = ""
    ws_code: int = WS_FORBIDDEN
    extra: dict[str, Any] = field(default_factory=dict)
    headers: tuple[tuple[bytes, bytes], ...] = ()

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.error}
        if self.message:
            body["message"] = self.message
        return {**body, **self.extra}


async def respond_json(
    send: Any, status: int, body: dict[str, Any], headers: tuple[tuple[bytes, bytes], ...] = ()
) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


async def respond(send: Any, refusal: Refusal, request_id: str | None = None) -> None:
    headers = refusal.headers
    if request_id:
        headers = (*headers, (b"x-request-id", request_id.encode("latin-1")))
    await respond_json(send, refusal.status, refusal.body(), headers)


async def close_websocket(send: Any, refusal: Refusal) -> None:
    await send({"type": "websocket.close", "code": refusal.ws_code, "reason": refusal.error})
