"""Studio's realtime console: a genuine client of the realtime socket protocol, relayed through Studio.

The browser opens one WebSocket here and this handler relays it to the server's ``/realtime/v1/socket`` (its internal listener), signed
with a *service* platform context. A service credential bypasses every channel policy, so the console can watch, subscribe to presence on, and publish to any
channel in the environment: a developer's tool, the same way an Ably control-plane key can see every channel.

A WebSocket handshake cannot carry Studio's CSRF header, so the browser first fetches a short-lived, signed **ticket** over a normal HTTP
call, then presents it as the socket opens. A ticket works once: it is remembered until it would have expired anyway, so a copy taken from
a log or a browser history opens nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time

import websockets
from sillo import HttpContext, SilloApp, WebSocketContext
from sillo.responses import JSONResponse

from pawabase_server.core.context import CONTEXT_HEADER, PlatformContext
from pawabase_server.core.tokens import TokenInvalid, issue_context_token, verify_context_token
from studio.support import Deps

logger = logging.getLogger("pawabase.studio.console")

TICKET = "studio-console-ticket"
TICKET_TTL = 20
CLOSE_BAD_TICKET = 4001
MAX_FRAME = 2**20
IDLE_SECONDS = 900


class TicketBook:
    """Remembers which tickets were spent, so each can be used once. Entries are dropped when they would have expired anyway."""

    def __init__(self, ttl: float = TICKET_TTL, clock=time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._spent: dict[str, float] = {}

    def spend(self, nonce: str) -> bool:
        """Mark *nonce* spent. Returns ``False`` when it already was."""
        now = self._clock()
        for old in [n for n, at in self._spent.items() if now - at > self._ttl * 2]:
            del self._spent[old]
        if nonce in self._spent:
            return False
        self._spent[nonce] = now
        return True


def nonce_of(context: PlatformContext) -> str | None:
    return next((s.removeprefix("nonce:") for s in context.scopes if s.startswith("nonce:")), None)


def websocket_url(server_url: str) -> str:
    return (
        server_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1).rstrip("/")
        + "/realtime/v1/socket"
    )


async def relay(browser: WebSocketContext, remote) -> None:
    """Carry frames both ways until either side ends or the console has been idle too long."""
    last = time.monotonic()

    async def to_remote() -> None:
        nonlocal last
        while True:
            message = await browser.receive()
            last = time.monotonic()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                await remote.send(message["text"])
            elif message.get("bytes") is not None:
                await remote.send(message["bytes"])

    async def to_browser() -> None:
        nonlocal last
        try:
            async for data in remote:
                last = time.monotonic()
                await (
                    browser.send_bytes(data) if isinstance(data, bytes) else browser.send_text(data)
                )
        finally:
            with contextlib.suppress(Exception):
                await browser.close()

    async def watchdog() -> None:
        while time.monotonic() - last < IDLE_SECONDS:
            await asyncio.sleep(min(30, IDLE_SECONDS))

    tasks = [asyncio.create_task(c()) for c in (to_remote, to_browser, watchdog)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task
        with contextlib.suppress(Exception):
            await remote.close()


def register(app: SilloApp, deps: Deps) -> None:
    settings = deps.settings
    url = websocket_url(settings.server_url)
    book = TicketBook()

    @app.get("/envs/{env}/realtime/ticket", exclude_from_schema=True)
    async def ticket(ctx: HttpContext, env: str):
        context = PlatformContext(
            env=env, role="anon", key_id=TICKET, scopes=(f"nonce:{secrets.token_hex(8)}",)
        )
        return JSONResponse(
            {"ticket": issue_context_token(settings.internal_secret, context, ttl=TICKET_TTL)},
            headers={"Cache-Control": "no-store"},
        )

    @app.ws_route("/studio/ws/realtime")
    async def console(ws: WebSocketContext):
        try:
            context = verify_context_token(
                ws.query_params.get("ticket") or "", settings.internal_secret
            )
        except TokenInvalid:
            await ws.close(code=CLOSE_BAD_TICKET, reason="invalid ticket")
            return
        nonce = nonce_of(context)
        if context.key_id != TICKET or nonce is None or not book.spend(nonce):
            await ws.close(code=CLOSE_BAD_TICKET, reason="invalid ticket")
            return
        await ws.accept()
        service = PlatformContext(env=context.env, role="service", key_id="studio-console")
        header = issue_context_token(settings.internal_secret, service, ttl=60)
        try:
            remote = await websockets.connect(
                url,
                additional_headers={CONTEXT_HEADER: header},
                open_timeout=10,
                max_size=MAX_FRAME,
            )
        except Exception:
            logger.warning("the server could not be reached for the console", exc_info=True)
            await ws.close(code=1011, reason="could not reach the server")
            return
        await relay(ws, remote)
