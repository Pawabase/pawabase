"""The identity routers: ``/auth/v1`` for users, ``/admin/v1`` for Studio and the other services."""

from __future__ import annotations

from sillo import Router, SilloApp

from pawabase_server.identity.accounts import Accounts


def register_routes(app: SilloApp, accounts: Accounts) -> None:
    from pawabase_server.routes.identity import admin, auth, orgs, recovery, security

    public = Router(prefix="/auth/v1")
    auth.register(public, accounts)
    recovery.register(public, accounts)
    recovery.register_keyless_recovery(public, accounts)
    security.register(public, accounts)
    orgs.register(public, accounts)
    app.mount_router(public)

    administration = Router(prefix="/admin/v1")
    admin.register(administration, accounts)
    app.mount_router(administration)
