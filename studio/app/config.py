"""Studio settings (``PAWABASE_*`` environment variables)."""

from __future__ import annotations

from pawabase_core.settings import PlatformSettings


class StudioSettings(PlatformSettings):
    """Studio settings.

    Attributes:
        csrf_secret: Signs the CSRF cookie. Derived from the internal secret when
            empty.
        cookie_secure: Mark cookies ``Secure``. Turn on behind HTTPS.
        vite_dev: Load the front end from the Vite dev server (hot reload)
            instead of the built bundle.
        vite_dev_server: Where ``npm run dev`` listens.
        frontend_dir: The front end's directory, holding ``dist/`` once built.
        public_gateway_url: The gateway as browsers reach it, shown in Studio
            (API URLs, snippets, the realtime socket).
        access_secret: When set, Studio requires a session that only a link
            signed with this secret can start (see ``app.access``). Empty keeps
            Studio open, which is only right behind localhost or your own proxy.
    """

    service_name: str = "studio"
    csrf_secret: str = ""
    cookie_secure: bool = False
    vite_dev: bool = False
    vite_dev_server: str = "http://localhost:5173"
    frontend_dir: str = "frontend"
    public_gateway_url: str = "http://localhost:8080"
    access_secret: str = ""
