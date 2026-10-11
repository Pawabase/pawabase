"""Angula settings (``PAWABASE_*`` environment variables)."""

from __future__ import annotations

from pawabase_core.settings import PlatformSettings


class AngulaSettings(PlatformSettings):
    """Angula settings.

    Attributes:
        max_channels: Channels one connection may join.
        max_message_bytes: Largest client message accepted.
        max_payload_bytes: Largest publication payload, from a client or over HTTP.
        max_presence_bytes: Largest presence ``meta`` a member may carry.
        max_history: The most messages one history request may return.
        history_bytes: Payload bytes retained per channel for replay.
        config_ttl: Seconds an environment's channel rules are cached.
        stale_config_ttl: Seconds the last good rules are still used when the API is unreachable.
        idle_timeout: Seconds without traffic before a peer is pruned.
        max_connections_per_env: Connections one environment may hold at once; 0 for no limit.
        max_connections_per_identity: Connections one signed-in person may hold at once; 0 for no limit.
        rate_per_second: Messages one connection may send per second, on average.
        rate_burst: Messages one connection may send in a burst.
        abuse_strikes: Rate-limit violations in a row before a connection is closed.
        presence_ttl: Seconds an instance may be silent before its presence entries are dropped.
        heartbeat_interval: Seconds between an instance's presence heartbeats.
        sweep_interval: Seconds between housekeeping passes (idle peers, expired tokens, stale presence).
        forward_queue_size: Client publications waiting to be handed to flows before new ones are dropped.
        drain_grace: Seconds peers are given to disconnect on shutdown.
    """

    service_name: str = "angula"
    max_channels: int = 100
    max_message_bytes: int = 64 * 1024
    max_payload_bytes: int = 32 * 1024
    max_presence_bytes: int = 2 * 1024
    max_history: int = 1000
    history_bytes: int = 256 * 1024
    config_ttl: int = 10
    stale_config_ttl: int = 300
    idle_timeout: float = 300.0
    max_connections_per_env: int = 0
    max_connections_per_identity: int = 20
    rate_per_second: float = 20.0
    rate_burst: int = 40
    abuse_strikes: int = 5
    presence_ttl: float = 45.0
    heartbeat_interval: float = 10.0
    sweep_interval: float = 15.0
    forward_queue_size: int = 1000
    drain_grace: float = 5.0
