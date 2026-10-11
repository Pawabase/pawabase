"""Angula's HTTP and WebSocket routes, one module per concern."""

from __future__ import annotations

from sillo import SilloApp

from app.realtime import Realtime
from routes import internal, public, ws


def register_routes(app: SilloApp, realtime: Realtime) -> None:
    ws.register(app, realtime)
    public.register(app, realtime)
    internal.register(app, realtime)
