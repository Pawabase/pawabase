"""Channel rules, and how an environment's rules are fetched and trusted."""

import asyncio

import pytest
from sillo.exceptions import HTTPException

from app.realtime.rules import ChannelRule, build_config
from app.realtime.store import ConfigStore
from pawabase_core.clients import ServiceError
from tests.helpers import FakeApi


def test_a_templated_rule_claims_every_channel_of_its_shape_but_permits_only_the_owner():
    rule = ChannelRule("user:{{ auth.user_id }}")
    assert rule.claims("user:1") and rule.claims("user:anyone") and not rule.claims("room:1")
    assert rule.permits("user:1", {"user_id": "1"})
    assert not rule.permits("user:2", {"user_id": "1"})
    assert not rule.permits(
        "user:None", {}
    )  # an anonymous caller renders to "None" and must never match


def test_the_first_matching_rule_wins_and_the_default_fills_the_gaps():
    config = build_config(
        {
            "channels": [
                {"pattern": "a:*", "subscribe": "public"},
                {"pattern": "a:b", "subscribe": "role:admin"},
            ],
            "default_policy": "service",
        }
    )
    assert config.rule_for("a:b").subscribe == "public"
    assert config.rule_for("z").subscribe == "service" and config.rule_for("z").publish == "service"


def test_bad_rows_are_skipped_not_fatal():
    config = build_config(
        {
            "channels": [
                {"pattern": ""},
                {"nopattern": 1},
                "junk",
                {"pattern": "ok:*", "history": "lots"},
                {"pattern": "big:*", "history": 10**9},
            ],
            "policies": {
                "broken": {"condition": "((("},
                "fine": {"condition": "auth.user_id != null"},
            },
        }
    )
    assert [r.pattern for r in config.rules] == ["ok:*", "big:*"]
    assert config.rules[0].history == 50 and config.rules[1].history == 1000


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def parts():
    api, clock = FakeApi(), Clock()
    return api, clock, ConfigStore(api, ttl=10, stale_ttl=100, clock=clock)


async def test_rules_are_cached_until_the_ttl(parts):
    api, clock, store = parts
    await store.get("dev")
    await store.get("dev")
    assert api.calls == 1
    clock.now = 11
    await store.get("dev")
    assert api.calls == 2


async def test_a_burst_of_askers_costs_one_request(parts):
    api, _, store = parts
    results = await asyncio.gather(*(store.get("dev") for _ in range(25)))
    assert api.calls == 1 and all(r is results[0] for r in results)


async def test_when_the_api_is_down_the_last_good_rules_keep_serving_until_the_stale_limit(parts):
    api, clock, store = parts
    good = await store.get("dev")
    api.fail = True
    clock.now = 20
    assert await store.get("dev") is good and store.stale_served == 1
    clock.now = 500
    with pytest.raises(ServiceError):
        await store.get("dev")


async def test_with_no_rules_ever_loaded_an_outage_is_an_error(parts):
    api, _, store = parts
    api.fail = True
    with pytest.raises(ServiceError):
        await store.get("dev")


async def test_an_unknown_environment_is_a_404_remembered_for_a_moment(parts):
    api, clock, store = parts
    api.missing = True
    for _ in range(3):
        with pytest.raises(HTTPException) as caught:
            await store.get("nope")
        assert caught.value.status_code == 404
    assert api.calls == 1
    clock.now = 6
    api.missing = False
    assert (await store.get("nope")).version == 1


async def test_invalidate_forces_a_fresh_fetch(parts):
    api, _, store = parts
    await store.get("dev")
    store.invalidate("dev")
    assert store.version("dev") is None
    await store.get("dev")
    assert api.calls == 2
