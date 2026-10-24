"""Assembling Studio: CSRF, Inertia, the built front end, and routes.

Studio is the control plane's web UI, and a service of its own: it is not part of the server. It manages the server over the server's
internal listener with short-lived service tokens, holds no credential of its own, and has no sign-in unless one is configured, which is
why it is published on localhost by default.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sillo import SilloApp
from sillo.security.csrf import CSRFMiddleware
from sillo.security.csrf.config import CSRFConfig
from sillo_inertia import Inertia, vite_react

from pawabase_server.core.clients import ServiceClient
from pawabase_server.core.service import create_service
from studio.access import StudioAccessGate
from studio.config import StudioSettings
from studio.password_access import StudioPasswordGate
from studio.register import register_routes
from studio.scope import ProjectScope

ROOT = Path(__file__).resolve().parent
ENTRY = "src/main.jsx"


def asset_version(manifest: Path) -> str | None:
    """The build's fingerprint, so clients on an older bundle reload."""
    if not manifest.is_file():
        return None
    return hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]


def create_studio(
    settings: StudioSettings | None = None, *, client: ServiceClient | None = None
) -> SilloApp:
    """Build Studio.

    Args:
        settings: Studio's settings.
        client: A prepared client for the server, instead of one built from ``settings.server_url`` (tests).
    """
    settings = settings or StudioSettings()
    if problems := settings.validate_for_production():
        if settings.app_env == "production":
            raise ValueError("; ".join(problems))
        if any("STUDIO" in p for p in problems):
            raise ValueError("; ".join(p for p in problems if "STUDIO" in p))
    secret = (
        settings.csrf_secret
        or hashlib.sha256(f"studio-csrf:{settings.internal_secret}".encode()).hexdigest()
    )
    client = client or ServiceClient(
        settings.server_url,
        secret=settings.internal_secret,
        issuer="studio",
        # Some management calls run long: migrating a large table, restoring a backup.
        timeout=120.0,
    )
    frontend = Path(settings.frontend_dir)
    if not frontend.is_absolute():
        frontend = ROOT / frontend
    manifest = frontend / "dist" / ".vite" / "manifest.json"

    app = create_service(
        "studio",
        settings,
        title="Pawabase Studio",
        description="Studio: manage this Pawabase backend.",
        docs=[],
    )
    inertia = Inertia(
        root_view=ROOT / "resources" / "views" / "app.html",
        base_dir=frontend,
        version=asset_version(manifest),
        vite=vite_react(
            entry=ENTRY,
            dev_server=settings.vite_dev_server,
            manifest_path=manifest,
            dev=settings.vite_dev,
        ),
    )
    inertia.middleware(app)
    # The double-submit cookie is named the way axios (Inertia's HTTP client) reads and echoes it by default, so every Inertia visit
    # carries it.
    app.use(
        CSRFMiddleware(
            CSRFConfig(
                enabled=True,
                secret_key=secret,
                cookie_name="XSRF-TOKEN",
                header_name="X-XSRF-TOKEN",
                cookie_secure=settings.cookie_secure,
                cookie_samesite="lax",
                exempt_urls=["/health", "/internal/*"],
            )
        )
    )
    if settings.studio_access_secret:
        app.use(
            StudioAccessGate(
                settings.studio_access_secret,
                secure_cookie=settings.cookie_secure,
                guard=ProjectScope(client).guard,
            )
        )
    elif settings.password_access_enabled:
        app.use(
            StudioPasswordGate(
                settings.studio_username,
                settings.studio_password,
                session_secret=settings.internal_secret,
                secure_cookie=settings.cookie_secure,
            )
        )
    app.state["core"] = client
    app.state["inertia"] = inertia
    app.state["frontend"] = frontend

    register_routes(app, settings, client, inertia, frontend)

    @app.on_shutdown
    async def close() -> None:
        await client.close()

    return app


def page_json(response: Any) -> dict[str, Any]:
    """Decode an Inertia JSON response (tests)."""
    return json.loads(response.text)
