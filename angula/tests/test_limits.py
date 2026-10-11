"""The limits: token bucket, strikes and connection quotas."""

import pytest

from app.realtime.limits import Quota, Strikes, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_a_bucket_allows_its_burst_then_refills_at_its_rate():
    clock = Clock()
    bucket = TokenBucket(rate=2, burst=3, clock=clock)
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]
    clock.now += 0.5  # one token
    assert bucket.take() and not bucket.take()
    clock.now += 10  # long idle never earns more than the burst
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]


def test_a_bucket_can_charge_more_than_one_token_and_reports_what_is_left():
    clock = Clock()
    bucket = TokenBucket(rate=1, burst=5, clock=clock)
    assert bucket.take(3) and not bucket.take(3)
    assert round(bucket.available, 2) == 2.0


def test_a_bucket_refuses_nonsense_settings():
    for rate, burst in ((0, 1), (-1, 1), (1, 0)):
        with pytest.raises(ValueError):
            TokenBucket(rate, burst)


def test_strikes_reach_the_limit_only_when_consecutive():
    strikes = Strikes(3)
    assert [strikes.hit(), strikes.hit()] == [False, False]
    strikes.clear()
    assert [strikes.hit(), strikes.hit(), strikes.hit()] == [False, False, True]


def test_strikes_with_no_limit_never_close_anyone():
    strikes = Strikes(0)
    assert not any(strikes.hit() for _ in range(100))


def test_a_quota_counts_per_key_and_frees_on_release():
    quota = Quota(2)
    assert quota.acquire("a") and quota.acquire("a") and not quota.acquire("a")
    assert quota.acquire("b")
    quota.release("a")
    assert quota.held("a") == 1 and quota.acquire("a")
    quota.release("b")
    assert quota.held("b") == 0


def test_a_quota_of_zero_is_unlimited():
    quota = Quota(0)
    assert all(quota.acquire("a") for _ in range(1000))
