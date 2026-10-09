"""Low-overhead installation request quotas.

The gateway reserves a small lease of requests from Redis, then consumes that
lease in process memory.  Normal requests therefore do not perform a Redis or
database operation.  The only trade-off is bounded unused reservations when a
gateway process exits (at most ``request_quota_reservation`` per process).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

_RESERVE_LUA = """
local amount = tonumber(ARGV[1])
local granted = amount
for index, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[index + 1])
    if limit > 0 then
        local current = tonumber(redis.call('GET', key) or '0')
        granted = math.min(granted, limit - current)
    end
end
if granted <= 0 then return 0 end
for index, key in ipairs(KEYS) do
    local limit = tonumber(ARGV[index + 1])
    if limit > 0 then
        redis.call('INCRBY', key, granted)
        local ttl = tonumber(ARGV[index + 1 + #KEYS])
        if ttl > 0 then redis.call('EXPIRE', key, ttl) end
    end
end
return granted
"""


def _windows(now: datetime) -> tuple[str, int, str, int]:
    """Return Redis suffixes and expiration seconds for UTC day and month."""
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    next_day = day_start + timedelta(days=1)
    next_month = datetime(
        now.year + (now.month == 12), 1 if now.month == 12 else now.month + 1, 1, tzinfo=UTC
    )
    return (
        now.strftime("%Y-%m-%d"),
        max(1, int((next_day - now).total_seconds())),
        now.strftime("%Y-%m"),
        max(1, int((next_month - now).total_seconds())),
    )


class RequestQuota:
    """Lease request capacity globally with Redis or locally without it."""

    def __init__(
        self,
        *,
        daily_limit: int = 0,
        monthly_limit: int = 0,
        reservation: int = 64,
        redis_url: str = "",
        scope: str = "",
    ) -> None:
        #: Counts one environment's requests when set; empty counts the whole deployment.
        self.scope = scope
        self.daily_limit = max(daily_limit, 0)
        self.monthly_limit = max(monthly_limit, 0)
        self.reservation = max(reservation, 1)
        self._available = 0
        self._lock = asyncio.Lock()
        self._local: dict[str, int] = {}
        self._redis = None
        if redis_url and (self.daily_limit or self.monthly_limit):
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(redis_url, decode_responses=True)

    @property
    def _local_prefix(self) -> str:
        return f"{self.scope}:" if self.scope else ""

    @property
    def _redis_prefix(self) -> str:
        # The deployment-wide counters keep the names they have always had.
        return (
            f"pawabase:quota:requests:{self.scope}:" if self.scope else "pawabase:quota:requests:"
        )

    @property
    def enabled(self) -> bool:
        return bool(self.daily_limit or self.monthly_limit)

    async def acquire(self) -> bool:
        """Consume one locally leased request token, reserving only as needed."""
        if not self.enabled:
            return True
        async with self._lock:
            if self._available:
                self._available -= 1
                return True
            granted = await self._reserve()
            if not granted:
                return False
            self._available = granted - 1
            return True

    async def _reserve(self) -> int:
        now = datetime.now(UTC)
        day, day_ttl, month, month_ttl = _windows(now)
        if self._redis is None:
            keys = (f"{self._local_prefix}day:{day}", f"{self._local_prefix}month:{month}")
            limits = (self.daily_limit, self.monthly_limit)
            granted = self.reservation
            for key, limit in zip(keys, limits, strict=True):
                if limit:
                    granted = min(granted, limit - self._local.get(key, 0))
            if granted <= 0:
                return 0
            for key, limit in zip(keys, limits, strict=True):
                if limit:
                    self._local[key] = self._local.get(key, 0) + granted
            return granted
        return int(
            await self._redis.eval(
                _RESERVE_LUA,
                2,
                f"{self._redis_prefix}day:{day}",
                f"{self._redis_prefix}month:{month}",
                self.reservation,
                self.daily_limit,
                self.monthly_limit,
                day_ttl,
                month_ttl,
            )
        )

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def usage(self) -> dict[str, object]:
        """Return the current UTC-window counters without reserving capacity."""
        now = datetime.now(UTC)
        day, day_ttl, month, month_ttl = _windows(now)
        if self._redis is None:
            daily = self._local.get(f"{self._local_prefix}day:{day}", 0)
            monthly = self._local.get(f"{self._local_prefix}month:{month}", 0)
        else:
            daily, monthly = await self._redis.mget(
                f"{self._redis_prefix}day:{day}",
                f"{self._redis_prefix}month:{month}",
            )
            daily, monthly = int(daily or 0), int(monthly or 0)
        return {
            "requests": {
                "daily": {"used": daily, "limit": self.daily_limit, "resets_in": day_ttl},
                "monthly": {"used": monthly, "limit": self.monthly_limit, "resets_in": month_ttl},
            },
            "reservation": self.reservation,
        }
