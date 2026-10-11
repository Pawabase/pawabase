"""Rate limiting primitives that need no storage: a token bucket, and a bounded table of them keyed by who is asking.

They suit limits that are per process and may be lost on restart (a connection's message rate, a login form's guesses). Limits that
must hold across processes belong in Sillo's rate limiter with a Redis backend.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    """Allows ``rate`` actions a second on average and ``burst`` at once.

    The clock is injected so tests do not sleep, and a bucket never refills past ``burst``: a client idle for an hour does not get an hour
    of credit.
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

    def wait(self, cost: float = 1.0) -> float:
        """Seconds until *cost* tokens will be available (0 when they already are)."""
        missing = cost - self.available
        return max(0.0, missing / self.rate)

    @property
    def stamp(self) -> float:
        return self._stamp


class KeyedLimiter:
    """A token bucket per key, in a table that cannot grow without bound.

    Keys come from clients (an address, an email), so an attacker can mint endless ones. When the table is full, buckets idle for
    ``idle`` seconds are dropped first; if none are idle the oldest is. A dropped bucket simply starts fresh, which can only be more
    generous to a key that had gone quiet.
    """

    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        max_keys: int = 10_000,
        idle: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate
        self.burst = burst
        self.max_keys = max_keys
        self.idle = idle
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        return self._bucket(key).take(cost)

    def retry_after(self, key: str, cost: float = 1.0) -> float:
        return self._bucket(key).wait(cost)

    def __len__(self) -> int:
        return len(self._buckets)

    def _bucket(self, key: str) -> TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self.max_keys:
                self._evict()
            bucket = self._buckets[key] = TokenBucket(self.rate, self.burst, clock=self._clock)
        return bucket

    def _evict(self) -> None:
        now = self._clock()
        stale = [key for key, bucket in self._buckets.items() if now - bucket.stamp > self.idle]
        for key in stale:
            del self._buckets[key]
        if len(self._buckets) >= self.max_keys:
            del self._buckets[next(iter(self._buckets))]
