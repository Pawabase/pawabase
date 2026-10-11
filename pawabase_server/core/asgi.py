"""Driving an ASGI application from inside the process: a WebSocket client with no network.

Studio's realtime console is a client of the realtime socket like any other, but the socket is served by the same process. :func:`connect`
opens a WebSocket straight into an ASGI application, so the console relays frames without a loopback connection, a port or a second
handshake. The same helper serves tests.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlencode


class SocketClosed(Exception):
    """The application closed the socket, or refused the handshake. ``code`` is its close code."""

    def __init__(self, code: int = 1000, reason: str = "") -> None:
        super().__init__(f"socket closed ({code}) {reason}".strip())
        self.code = code
        self.reason = reason


class AsgiSocket:
    """One WebSocket connection to an ASGI application. Iterate it to receive frames; call :meth:`send` to send one."""

    def __init__(self, app: Any, scope: dict[str, Any]) -> None:
        self._app = app
        self._scope = scope
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.accepted = False
        self.close_code: int | None = None

    async def _open(self) -> None:
        await self._inbox.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(self._run(), name="asgi-socket")
        first = await self._outbox.get()
        if first["type"] == "websocket.accept":
            self.accepted = True
            return
        if first["type"] == "websocket.close":
            self.close_code = first.get("code", 1000)
            raise SocketClosed(self.close_code, first.get("reason", ""))
        raise SocketClosed(1011, "the application ended before accepting")

    async def _run(self) -> None:
        async def receive() -> dict[str, Any]:
            return await self._inbox.get()

        async def send(message: dict[str, Any]) -> None:
            await self._outbox.put(message)

        try:
            await self._app(self._scope, receive, send)
        except Exception:
            pass
        finally:
            await self._outbox.put({"type": "websocket.close", "code": 1011, "reason": "ended"})

    async def send(self, data: str | bytes) -> None:
        key = "text" if isinstance(data, str) else "bytes"
        await self._inbox.put({"type": "websocket.receive", key: data})

    async def recv(self) -> str | bytes:
        """The next frame. Raises :class:`SocketClosed` when the application closes the socket."""
        while True:
            message = await self._outbox.get()
            if message["type"] == "websocket.send":
                if message.get("text") is not None:
                    return message["text"]
                return message.get("bytes") or b""
            if message["type"] == "websocket.close":
                self.close_code = message.get("code", 1000)
                raise SocketClosed(self.close_code, message.get("reason", ""))

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self._frames()

    async def _frames(self) -> AsyncIterator[str | bytes]:
        while True:
            try:
                yield await self.recv()
            except SocketClosed:
                return

    async def close(self, code: int = 1000) -> None:
        await self._inbox.put({"type": "websocket.disconnect", "code": code})
        if self._task is not None:
            task, self._task = self._task, None
            try:
                await asyncio.wait_for(asyncio.shield(task), 2)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
            with contextlib.suppress(BaseException):
                await task


async def connect(
    app: Any,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
) -> AsgiSocket:
    """Open a WebSocket to *app* at *path*. Raises :class:`SocketClosed` when the handshake is refused."""
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(query or {}).encode(),
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
        ],
        "client": ("127.0.0.1", 0),
        "server": ("pawabase", 0),
        "subprotocols": [],
    }
    socket = AsgiSocket(app, scope)
    await socket._open()
    return socket
