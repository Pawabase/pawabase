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
    #: Seconds between refreshes of the verified custom domains; 0 turns it off.
    domain_refresh_interval: float = 30
    rate_limit: int = 600
    rate_window: int = 60
    max_body_bytes: int = 100 * 1024 * 1024
    upstream_timeout: float = 60.0
    hosts: str = ""
    #: Who may vouch for a client's address through ``X-Forwarded-For``: ``private`` (the default) trusts a proxy on a private or loopback
    #: address, ``*`` trusts any peer, empty trusts none, and a comma-separated list of CIDR ranges trusts exactly those. A peer that is not
    #: trusted cannot choose the address it is rate limited, allow-listed or logged under.
    trusted_proxies: str = "private"
    #: Extra attempts at an idempotent request when the upstream could not be reached at all (no byte was sent to it).
    upstream_retries: int = 1
    #: Consecutive upstream failures before its circuit opens, and seconds it stays open before one probe is let through.
    breaker_failures: int = 5
    breaker_cooldown: float = 10.0
    #: Seconds a resolved key may still be used when the API cannot be reached to re-check it.
    key_stale_ttl: int = 60
    ws_max_message_bytes: int = 1024 * 1024
    ws_open_timeout: float = 10.0
    ws_ping_interval: float = 25.0
    #: Methods the gateway forwards; anything else is answered 405 without touching an upstream.
    allowed_methods: str = "GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS"
