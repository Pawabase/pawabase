"""Akountz's routers: ``/auth/v1`` for users, ``/admin/v1`` for operators and services."""

from __future__ import annotations

from sillo import Router, SilloApp

from app.platform import Akountz


def register_routes(app: SilloApp, akountz: Akountz) -> None:
    from routes import admin, auth, orgs, recovery, security

    public = Router(prefix="/auth/v1")
    auth.register(public, akountz)
    recovery.register(public, akountz)
    recovery.register_keyless_recovery(public, akountz)
    security.register(public, akountz)
    orgs.register(public, akountz)
    app.mount_router(public)

    administration = Router(prefix="/admin/v1")
    admin.register(administration, akountz)
    app.mount_router(administration)
