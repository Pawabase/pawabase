"""The realtime HTTP and WebSocket routes, one module per concern."""

from __future__ import annotations

from sillo import SilloApp

from pawabase_server.realtime import Realtime
from pawabase_server.routes.realtime import internal, public, sse, ws


def register_routes(app: SilloApp, realtime: Realtime) -> None:
    ws.register(app, realtime)
    sse.register(app, realtime)
    public.register(app, realtime)
    internal.register(app, realtime)
