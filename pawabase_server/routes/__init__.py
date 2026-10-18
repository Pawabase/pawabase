"""Every router the server mounts, by what it is for rather than by who wrote it.

========== ===========================================================================================
platform   ``/platform/v1``: the management plane Studio, the CLI and automation use
data       ``/storage/v1``, ``/functions/v1``, ``/flows/v1``, ``/hooks/v1``: the data plane's own routes
identity   ``/auth/v1`` for your application's users, ``/admin/v1`` for operators
realtime   ``/realtime/v1`` sockets and publishing, ``/internal/v1`` inspection
public     the landing page, the status page, and what the edge reports about itself
========== ===========================================================================================

Sillo gives a router its whole prefix subtree, so the management plane is one router at ``/platform/v1`` that each module adds its routes to.
"""

from __future__ import annotations

from sillo import Router, SilloApp

from pawabase_server.identity.accounts import Accounts
from pawabase_server.platform import Platform
from pawabase_server.realtime import Realtime
from pawabase_server.routes.common import MANAGE


def register_routes(
    app: SilloApp, platform: Platform, accounts: Accounts, realtime: Realtime
) -> None:
    from pawabase_server.routes import identity, public
    from pawabase_server.routes import realtime as realtime_routes
    from pawabase_server.routes.data import functions_internal, hooks, invoke, storage
    from pawabase_server.routes.identity import admin as identity_admin
    from pawabase_server.routes.identity import directory as identity_directory
    from pawabase_server.routes.platform import (
        agent,
        automation,
        backups,
        data,
        definitions,
        environments,
        firewall,
        functions,
        insights,
        networking,
        operations,
        releases,
        realtime_channels,
        runtime,
        schema,
        status,
    )

    management = Router(prefix="/platform/v1")
    for module in (
        agent,
        environments,
        definitions,
        releases,
        realtime_channels,
        data,
        backups,
        automation,
        operations,
        functions,
        insights,
        networking,
        firewall,
        runtime,
        schema,
        status,
    ):
        module.register(management, platform)
    # Your users, administered with the environment's secret key: the same routes as ``/admin/v1`` (which only other services reach), under
    # the management plane's own authorization. ``/platform/v1/envs/{env}/auth/users``, ``…/orgs``, ``…/roles``, ``…/sessions``…
    for register in (identity_admin.register, identity_directory.register_admin):
        register(
            management,
            accounts,
            gate=MANAGE,
            prefix="/envs/{env}/auth",
            tag="user administration",
            internal=False,
        )
    app.mount_router(management)

    storage.register(app, platform)
    invoke.register(app, platform)
    functions_internal.register(app, platform)
    hooks.register(app, platform)
    identity.register_routes(app, accounts)
    realtime_routes.register_routes(app, realtime)
    public.register_routes(app, platform.settings)
