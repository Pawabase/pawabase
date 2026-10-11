"""Limits that keep one noisy client, person or environment from starving the rest."""

from __future__ import annotations

from collections import Counter

from pawabase_server.core.buckets import TokenBucket

__all__ = ["Quota", "Strikes", "TokenBucket"]


class Strikes:
    """Counts consecutive violations, so a client that keeps flooding is closed instead of being refused forever."""

    __slots__ = ("limit", "count")

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0

    def hit(self) -> bool:
        """Record a violation. Returns whether the limit has now been reached."""
        self.count += 1
        return self.limit > 0 and self.count >= self.limit

    def clear(self) -> None:
        self.count = 0


class Quota:
    """How many connections each key (an environment, a person) holds, against a ceiling. A ceiling of 0 means no limit."""

    def __init__(self, ceiling: int) -> None:
        self.ceiling = ceiling
        self._held: Counter[str] = Counter()

    def acquire(self, key: str) -> bool:
        if self.ceiling > 0 and self._held[key] >= self.ceiling:
            return False
        self._held[key] += 1
        return True

    def release(self, key: str) -> None:
        if self._held[key] > 1:
            self._held[key] -= 1
        else:
            self._held.pop(key, None)

    def held(self, key: str) -> int:
        return self._held.get(key, 0)
