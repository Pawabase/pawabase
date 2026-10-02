"""Angula settings (``PAWABASE_*`` environment variables)."""

from __future__ import annotations

from pawabase_core.settings import PlatformSettings


class AngulaSettings(PlatformSettings):
    """Angula settings.

    Attributes:
        max_channels: Channels one connection may join.
        max_message_bytes: Largest client message accepted.
        history_bytes: Payload bytes retained per channel for replay.
        config_ttl: Seconds an environment's channel rules are cached.
        idle_timeout: Seconds without traffic before a peer is pruned.
    """

    service_name: str = "angula"
    max_channels: int = 100
    max_message_bytes: int = 64 * 1024
    history_bytes: int = 256 * 1024
    config_ttl: int = 10
    idle_timeout: float = 300.0
