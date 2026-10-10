"""Gateway settings (``PAWABASE_*`` environment variables)."""

from __future__ import annotations

from pawabase_core.settings import PlatformSettings


class GatewaySettings(PlatformSettings):
    """Gateway settings.

    Attributes:
        cors_origins: ``*`` lets any origin call the data plane. Browser clients
            send keys and tokens in headers, never cookies, so this is the usual
            setting for a backend-as-a-service. Environments can narrow it per
            publishable key with ``settings.cors_origins``.
        key_cache_ttl: Seconds a resolved API key is cached (Sillo cache).
        rate_limit, rate_window: Requests per window, per key or client address.
        max_body_bytes: Largest request body forwarded (uploads included).
        upstream_timeout: Seconds to wait for an upstream response.
        hosts: Which environment each hostname serves, as ``environment=host[|host...]`` pairs
            separated by commas: ``production=api.example.com,staging=stg.example.com|stg.acme.io``.
            Empty (the default) maps nothing. A key must belong to the environment its host serves;
            a request on a host that is not listed is unaffected.
    """

    service_name: str = "gateway"
    cors_origins: str = "*"
    key_cache_ttl: int = 30
    #: Seconds between status-page probes of the services; 0 turns probing off.
    status_probe_interval: float = 300
    rate_limit: int = 600
    rate_window: int = 60
    max_body_bytes: int = 100 * 1024 * 1024
    upstream_timeout: float = 60.0
    hosts: str = ""
