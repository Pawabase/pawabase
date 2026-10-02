"""Studio settings (``PAWABASE_*`` environment variables)."""

from __future__ import annotations

from pawabase_core.settings import PlatformSettings


class StudioSettings(PlatformSettings):
    """Studio settings.

    Attributes:
        session_secret: Signs the session cookie. Derived from the internal
            secret when empty.
        session_ttl: Seconds an idle operator session lasts.
        cookie_secure: Mark cookies ``Secure``. Turn on behind HTTPS.
        vite_dev: Load the front end from the Vite dev server (hot reload)
            instead of the built bundle.
        vite_dev_server: Where ``npm run dev`` listens.
        frontend_dir: The front end's directory, holding ``dist/`` once built.
        public_gateway_url: The gateway as browsers reach it, shown in Studio
            (API URLs, snippets, the realtime socket).
    """

    service_name: str = "studio"
    session_secret: str = ""
    session_ttl: int = 12 * 3600
    cookie_secure: bool = False
    vite_dev: bool = False
    vite_dev_server: str = "http://localhost:5173"
    frontend_dir: str = "frontend"
    public_gateway_url: str = "http://localhost:8080"
