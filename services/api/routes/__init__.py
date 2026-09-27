"""Every router the API mounts.

Sillo gives a router its whole prefix subtree, so the management plane is one
router at ``/platform/v1`` that each module adds its routes to.
"""

from __future__ import annotations

from sillo import Router, SilloApp

from app.platform import Platform


def register_routes(app: SilloApp, platform: Platform) -> None:
    from routes.platform import data, definitions, projects

    management = Router(prefix="/platform/v1")
    for module in (projects, definitions, data):
        module.register(management, platform)
    app.mount_router(management)
