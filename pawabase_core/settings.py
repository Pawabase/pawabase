"""Settings every Pawabase service shares.

Each service subclasses :class:`PlatformSettings` with its own fields. Values come
from the environment and the service's ``.env`` through :class:`sillo.config.Config`,
so a missing required secret fails at startup, not on the first request.
"""

from __future__ import annotations

from typing import Literal

from sillo.config import Config


class PlatformSettings(Config):
    """Configuration shared by every service.

    Attributes:
        service_name: How this service names itself in tokens and telemetry.
        app_env: The deployment stage of the runtime itself, not of one of its environments.
        debug: Sillo debug mode.
        internal_secret: Signs service tokens and the gateway's context header.
            Every service in one installation must share it.
        jwt_master_secret: The root from which each environment's
            user-token signing key is derived.
        master_key: Encrypts stored secrets. Rotating it makes existing secrets
            unreadable, so it must be backed up with the database.
        project_name: What this runtime calls itself: Studio's title, the title of each
            generated API's documentation, and the name in emails to your users.
        api_url, akountz_url, angula_url: Internal URLs of the other services.
        redis_url: Shared Redis for platform events, cache and queues. Empty
            means in-process fallbacks, which is only correct for a single process.
        cors_origins: Comma-separated origins allowed by the gateway and Studio.
    """

    service_name: str = "pawabase"
    app_env: Literal["local", "testing", "staging", "production"] = "local"
    debug: bool = False
    project_name: str = "Pawabase"

    internal_secret: str = "dev-internal-secret-change-me-please"
    jwt_master_secret: str = "dev-jwt-master-secret-change-me-please"
    master_key: str = "dev-master-key-change-me-please-32bytes"

    api_url: str = "http://127.0.0.1:8001"
    akountz_url: str = "http://127.0.0.1:8002"
    angula_url: str = "http://127.0.0.1:8003"
    gateway_url: str = "http://127.0.0.1:8080"
    studio_url: str = "http://127.0.0.1:8090"

    redis_url: str = ""
    cors_origins: str = "http://localhost:8090,http://127.0.0.1:8090"

    class Env:
        env_prefix = "PAWABASE_"

    def origins(self) -> list[str]:
        """The CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def validate_for_production(self) -> list[str]:
        """Name every development default still in place.

        Returns:
            Problems found. Services refuse to start in production when any exist.
        """
        problems = []
        for field in ("internal_secret", "jwt_master_secret", "master_key"):
            value = getattr(self, field)
            if value.startswith("dev-") or len(value) < 32:
                problems.append(
                    f"PAWABASE_{field.upper()} must be set to a random value of 32+ characters"
                )
        return problems
