"""Limits that keep one noisy client, person or environment from starving the rest."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable


class TokenBucket:
    """A rate limiter that allows ``rate`` actions a second on average and ``burst`` at once.

    The clock is injected so tests do not sleep, and a bucket never refills past ``burst``: a client idle for an hour does not get an
    hour of credit.
    """

    __slots__ = ("rate", "burst", "_tokens", "_stamp", "_clock")

    def __init__(
        self, rate: float, burst: int, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be positive and burst at least 1")
        self.rate = rate
        self.burst = burst
        self._clock = clock
        self._tokens = float(burst)
        self._stamp = clock()

    def take(self, cost: float = 1.0) -> bool:
        """Spend *cost* if the bucket holds it. Returns whether the action is allowed."""
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._stamp) * self.rate)
        self._stamp = now
        if self._tokens >= cost:
            self._tokens -= cost
            return True
        return False

    @property
    def available(self) -> float:
        return min(self.burst, self._tokens + (self._clock() - self._stamp) * self.rate)


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
