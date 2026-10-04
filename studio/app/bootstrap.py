"""Assembling Studio: CSRF, Inertia, the built front end, and routes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sillo import SilloApp
from sillo.security.csrf import CSRFMiddleware
from sillo.security.csrf.config import CSRFConfig
from sillo_inertia import Inertia, vite_react

from app.config import StudioSettings
from pawabase_core.clients import ServiceClient
from pawabase_core.service import create_service

ROOT = Path(__file__).resolve().parent.parent
ENTRY = "src/main.jsx"


def _asset_version(manifest: Path) -> str | None:
    """The build's fingerprint, so clients on an older bundle reload."""
    if not manifest.is_file():
        return None
    return hashlib.sha256(manifest.read_bytes()).hexdigest()[:12]


def create_app(
    settings: StudioSettings | None = None, *, clients: dict[str, ServiceClient] | None = None
) -> SilloApp:
    settings = settings or StudioSettings()
    secret = (
        settings.csrf_secret
        or hashlib.sha256(f"studio-csrf:{settings.internal_secret}".encode()).hexdigest()
    )
    clients = clients or {
        "api": ServiceClient(
            settings.api_url,
            secret=settings.internal_secret,
            issuer="studio",
            audience="api",
            # Creating environments from a blueprint builds each of them in one call.
            timeout=120.0,
        ),
        "akountz": ServiceClient(
            settings.akountz_url,
            secret=settings.internal_secret,
            issuer="studio",
            audience="akountz",
        ),
        "angula": ServiceClient(
            settings.angula_url, secret=settings.internal_secret, issuer="studio", audience="angula"
        ),
    }
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
        version=_asset_version(manifest),
        vite=vite_react(
            entry=ENTRY,
            dev_server=settings.vite_dev_server,
            manifest_path=manifest,
            dev=settings.vite_dev,
        ),
    )
    inertia.middleware(app)
    # The double-submit cookie is named the way axios (Inertia's HTTP client)
    # reads and echoes it by default, so every Inertia visit carries it.
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
    app.state["clients"] = clients
    app.state["inertia"] = inertia
    app.state["settings"] = settings
    app.state["frontend"] = frontend

    from routes import register_routes

    register_routes(app, settings, clients, inertia, frontend)

    @app.on_shutdown
    async def close() -> None:
        for client in clients.values():
            await client.close()

    return app


def page_json(response: Any) -> dict[str, Any]:
    """Decode an Inertia JSON response (tests)."""
    return json.loads(response.text)
