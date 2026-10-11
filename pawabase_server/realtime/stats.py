"""Counters and recent activity for one instance."""

from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any

COUNTERS = (
    "published",
    "delivered",
    "dropped",
    "failed",
    "denied",
    "rate_limited",
    "oversized",
    "presence_diffs",
    "connections_total",
    "forwarded",
    "forward_dropped",
    "forward_failed",
    "closed_for_abuse",
    "channels_revoked",
)


class Stats:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter({name: 0 for name in COUNTERS})
        self.by_env: dict[str, Counter[str]] = {}
        self.recent: deque[dict[str, Any]] = deque(maxlen=500)
        self.errors: deque[dict[str, Any]] = deque(maxlen=200)
        self.started_at = time.time()

    def bump(self, name: str, amount: int = 1, *, env: str | None = None) -> None:
        self.counts[name] += amount
        if env is not None:
            self.by_env.setdefault(env, Counter())[name] += amount

    def error(self, env: str, channel: str, kind: str | None, message: str) -> None:
        self.errors.append({"env": env, "channel": channel, "type": kind, "error": message})

    def delivery(
        self,
        env: str,
        channel: str,
        event: str | None,
        delivered: int,
        dropped: int,
        failed: int,
        at: str | None,
    ) -> None:
        self.bump("delivered", delivered, env=env)
        self.bump("dropped", dropped, env=env)
        self.bump("failed", failed, env=env)
        self.recent.append(
            {
                "env": env,
                "channel": channel,
                "event": event,
                "delivered": delivered,
                "dropped": dropped,
                "failed": failed,
                "at": at,
            }
        )

    def for_env(self, env: str) -> dict[str, int]:
        return dict(self.by_env.get(env, {}))

    def uptime(self) -> float:
        return round(time.time() - self.started_at, 1)
