"""Bridging a client's WebSocket to a service's.

The two directions run as tasks; when either ends, the other is cancelled and both sockets are closed. Message size is capped so one client
cannot make the gateway buffer an unbounded frame, and a ping keeps NATs from silently dropping an idle connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import websockets

from app.config import GatewaySettings
from app.proxy.errors import WS_BAD_GATEWAY, WS_FORBIDDEN

logger = logging.getLogger("pawabase.gateway.bridge")


def refusal_code(exc: Exception) -> int:
    """The close code for a failed upstream handshake: a 401/403 from the service is a refusal, anything else a gateway failure."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return WS_FORBIDDEN if status in (401, 403) else WS_BAD_GATEWAY


async def open_remote(url: str, headers: dict[str, str], settings: GatewaySettings) -> Any:
    return await websockets.connect(
        url,
        additional_headers=headers,
        open_timeout=settings.ws_open_timeout,
        max_size=settings.ws_max_message_bytes,
        ping_interval=settings.ws_ping_interval or None,
    )


async def pump(receive: Any, send: Any, remote: Any) -> None:
    """Carry frames both ways until one side ends."""

    async def to_remote() -> None:
        while True:
            message = await receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await remote.send(message["text"])
            elif message.get("bytes") is not None:
                await remote.send(message["bytes"])

    async def to_client() -> None:
        try:
            async for data in remote:
                await send(
                    {
                        "type": "websocket.send",
                        ("bytes" if isinstance(data, bytes) else "text"): data,
                    }
                )
        finally:
            with contextlib.suppress(Exception):
                await send({"type": "websocket.close", "code": remote.close_code or 1000})

    tasks = [asyncio.create_task(to_remote()), asyncio.create_task(to_client())]
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task
    finally:
        with contextlib.suppress(Exception):
            await remote.close()
