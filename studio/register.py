"""Studio's routes, one module per concern.

========= ==========================================================================================
pages     the Inertia pages and the built front end's assets
apidocs   an environment's API reference
health    the status lights in the top bar
console   the realtime console's ticket and WebSocket relay
bridge    the browser's way to the platform's services, and the API explorer
support   what they share: ``Deps`` and request/response helpers
========= ==========================================================================================
"""

from __future__ import annotations

from pathlib import Path

from sillo import SilloApp
from sillo_inertia import Inertia

from pawabase_server.core.clients import ServiceClient
from studio import apidocs, bridge, console, health, pages
from studio.config import StudioSettings
from studio.support import Deps


def register_routes(
    app: SilloApp,
    settings: StudioSettings,
    core: ServiceClient,
    inertia: Inertia,
    frontend: Path,
) -> None:
    deps = Deps(settings=settings, core=core, inertia=inertia, frontend=frontend)
    # Order is routing precedence: specific paths are registered before the ``/envs/{env}/{section}`` catch-all in ``pages``.
    for module in (console, apidocs, health, bridge, pages):
        module.register(app, deps)
