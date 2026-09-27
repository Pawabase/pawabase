"""Akountz settings (``PAWABASE_*`` environment variables)."""

from __future__ import annotations

from pawabase_kit.settings import PlatformSettings


class AkountzSettings(PlatformSettings):
    """Akountz settings.

    Attributes:
        database_url: Akountz's own database (users, sessions, identities, orgs).
        admin_email, admin_password: The first platform operator, created at
            startup when no operator exists yet. Studio signs in with it.
        lockout_threshold, lockout_minutes: Failed sign-ins before a temporary lock.
        config_ttl: Seconds an environment's auth configuration is cached.
    """

    service_name: str = "akountz"
    database_url: str = "sqlite://storage/akountz.db"
    db_generate_schemas: bool = False
    public_url: str = "http://127.0.0.1:8080"
    admin_email: str = ""
    admin_password: str = ""
    lockout_threshold: int = 10
    lockout_minutes: int = 15
    config_ttl: int = 10
