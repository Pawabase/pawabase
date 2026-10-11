"""Studio's settings (``PAWABASE_*`` environment variables).

Studio is its own service: it has its own configuration, and the only things it shares with the server are the internal secret (to sign the
service tokens it calls the server with) and the server's address.
"""

from __future__ import annotations

from typing import Literal

from sillo.config import Config


class StudioSettings(Config):
    """Studio settings.

    Attributes:
        server_url: The server's internal address (its internal listener, never its public one).
        public_gateway_url: The server's public address as browsers reach it, shown in Studio (API URLs, snippets, the realtime socket).
        internal_secret: Signs the service tokens Studio calls the server with. It must be the server's.
        csrf_secret: Signs the CSRF cookie. Derived from the internal secret when empty.
        cookie_secure: Mark cookies ``Secure``. Turn on behind HTTPS.
        vite_dev, vite_dev_server: Load the front end from the Vite dev server (hot reload) instead of the built bundle.
        frontend_dir: The front end's directory, holding ``dist/`` once built.
        studio_access_secret: When set, Studio requires a session that only a link signed with this secret can start (a hosting service
            that serves Studio). Empty keeps Studio open, which is only right behind localhost or your own proxy.
        studio_username, studio_password: Optional self-hosted Studio login. Set both to show a form and require its signed session cookie.
            Deliberately separate from the signed-link gate.
    """

    app_env: Literal["local", "testing", "staging", "production"] = "local"
    debug: bool = False
    project_name: str = "Pawabase"
    #: Where Studio listens. It has no sign-in unless one is configured, so it binds the loopback by default; a container binds
    #: ``0.0.0.0`` and publishes the port on the host's loopback only.
    studio_host: str = "127.0.0.1"
    studio_port: int = 8090
    log_level: str = "info"
    forwarded_allow_ips: str = "*"

    server_url: str = "http://127.0.0.1:8001"
    public_gateway_url: str = "http://localhost:8080"
    internal_secret: str = "dev-internal-secret-change-me-please"

    csrf_secret: str = ""
    cookie_secure: bool = False
    vite_dev: bool = False
    vite_dev_server: str = "http://localhost:5173"
    frontend_dir: str = "frontend"
    studio_access_secret: str = ""
    studio_username: str = ""
    studio_password: str = ""

    class Env:
        env_prefix = "PAWABASE_"

    @property
    def password_access_enabled(self) -> bool:
        """Whether this self-hosted Studio has a local login configured."""
        return bool(self.studio_username and self.studio_password)

    def validate_for_production(self) -> list[str]:
        """Name every development default still in place; Studio refuses to start in production while any exist."""
        problems = []
        if self.internal_secret.startswith("dev-") or len(self.internal_secret) < 32:
            problems.append(
                "PAWABASE_INTERNAL_SECRET must be set to a random value of 32+ characters"
            )
        if bool(self.studio_username) != bool(self.studio_password):
            problems.append(
                "PAWABASE_STUDIO_USERNAME and PAWABASE_STUDIO_PASSWORD must be set together"
            )
        if self.studio_access_secret and self.password_access_enabled:
            problems.append(
                "configure either PAWABASE_STUDIO_ACCESS_SECRET or the "
                "PAWABASE_STUDIO_USERNAME/PAWABASE_STUDIO_PASSWORD pair, not both"
            )
        return problems
