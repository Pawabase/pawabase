"""Every router the API mounts.

Sillo gives a router its whole prefix subtree, so the management plane is one
router at ``/platform/v1`` that each module adds its routes to.
"""

from __future__ import annotations

from sillo import Router, SilloApp

from app.platform import Platform


def register_routes(app: SilloApp, platform: Platform) -> None:
    from routes import internal
    from routes.data import hooks, invoke, storage
    from routes.platform import (
        automation,
        backups,
        data,
        definitions,
        environments,
        functions,
        operations,
        releases,
        runtime,
    )

    management = Router(prefix="/platform/v1")
    for module in (
        environments,
        definitions,
        releases,
        data,
        backups,
        automation,
        operations,
        functions,
        runtime,
    ):
        module.register(management, platform)
    app.mount_router(management)

    internal.register(app, platform)
    storage.register(app, platform)
    invoke.register(app, platform)
    hooks.register(app, platform)
