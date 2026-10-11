"""One client connection: who it is, what it joined, and how much it is allowed to say."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sillo_wire import Peer

from app.realtime.limits import Strikes, TokenBucket

EXPIRY_WARNING = 30.0


@dataclass
class Connection:
    """A connected client, for delivery, inspection and presence."""

    peer: Peer
    env: str
    user_id: str | None
    role: str
    auth: dict[str, Any]
    credential: dict[str, Any] = field(default_factory=dict)
    ip: str | None = None
    user_agent: str | None = None
    connected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    received: int = 0
    #: When the access token behind this connection stops being valid (epoch seconds), or ``None`` for an anonymous or service connection.
    token_exp: float | None = None
    bucket: TokenBucket | None = None
    strikes: Strikes | None = None
    warned_expiry: bool = False
    held_identity: str | None = None

    @property
    def id(self) -> str:
        return str(self.peer.id)

    def allow_message(self) -> bool:
        """Spend one message from this connection's budget. Connections with no budget configured are never limited."""
        return self.bucket is None or self.bucket.take()

    def expires_in(self, now: float | None = None) -> float | None:
        if self.token_exp is None:
            return None
        return self.token_exp - (time.time() if now is None else now)

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "env": self.env,
            "user_id": self.user_id,
            "role": self.role,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "connected_at": self.connected_at,
            "channels": sorted(self.channels),
            "pending": self.peer.pending,
            "received": self.received,
            "token_expires_in": None
            if self.token_exp is None
            else max(0, round(self.expires_in() or 0)),
        }
