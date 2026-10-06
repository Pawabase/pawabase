"""Request quota leasing is local on the hot path and exact within one worker."""

from __future__ import annotations

from app.quotas import RequestQuota


async def test_daily_quota_is_reserved_in_blocks_without_overshooting():
    quota = RequestQuota(daily_limit=5, reservation=3)

    assert [await quota.acquire() for _ in range(6)] == [True, True, True, True, True, False]


async def test_monthly_quota_can_limit_an_unlimited_day():
    quota = RequestQuota(monthly_limit=2, reservation=64)

    assert await quota.acquire()
    assert await quota.acquire()
    assert not await quota.acquire()


async def test_disabled_quota_never_reserves_tokens():
    quota = RequestQuota()

    assert await quota.acquire()
    assert quota._available == 0
