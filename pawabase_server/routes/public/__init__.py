"""What the public entry point answers itself: a landing page, the status page, and the edge's own counters."""

from __future__ import annotations

from sillo import SilloApp

from pawabase_server.settings import Settings


def register_routes(app: SilloApp, settings: Settings) -> None:
    from pawabase_server.routes.public import internal, landing, status

    landing.register(app, settings)
    status.register(app)
    internal.register(app, settings)
