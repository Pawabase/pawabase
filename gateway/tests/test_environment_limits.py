"""An environment's own `<ENV>_*` limits sit inside the deployment-wide ones."""

import logging

from app.limits import EnvironmentLimits
from app.quotas import RequestQuota


def build(settings, **environ):
    return EnvironmentLimits(settings, key_func=lambda ctx: "k", environ=environ)


def test_an_environment_with_no_variables_adds_no_limits(settings):
    limit = build(settings).for_environment("main")
    assert limit.quota is None and limit.limiter is None and limit.connections is None


def test_variables_build_a_quota_a_limiter_and_a_connection_cap(settings):
    limit = build(
        settings,
        MAIN_DAILY_REQUEST_LIMIT="10",
        MAIN_RATE_LIMIT="2",
        MAIN_MAX_ACTIVE_CONNECTIONS="3",
    ).for_environment("main")
    assert limit.quota.daily_limit == 10 and limit.quota.scope == "main"
    assert limit.limiter is not None and limit.rate_limit == 2
    assert limit.rate_window == 0  # unset: the deployment's window is used
    assert limit.connections._value == 3


def test_limits_belong_to_their_environment_only(settings):
    limits = build(settings, MAIN_DAILY_REQUEST_LIMIT="10")
    assert limits.for_environment("main").quota is not None
    assert limits.for_environment("staging").quota is None
    assert limits.for_environment("my-env").quota is None


def test_hyphenated_names_read_underscored_variables(settings):
    limit = build(settings, MY_ENV_MONTHLY_REQUEST_LIMIT="7").for_environment("my-env")
    assert limit.quota.monthly_limit == 7


def test_a_bad_value_is_logged_and_ignored_not_fatal(settings, caplog):
    with caplog.at_level(logging.ERROR, logger="pawabase.gateway"):
        limit = build(
            settings, MAIN_DAILY_REQUEST_LIMIT="lots", MAIN_RATE_LIMIT="4"
        ).for_environment("main")
    assert "MAIN_DAILY_REQUEST_LIMIT must be a whole number" in caplog.text
    assert limit.quota is None  # the bad one is dropped
    assert limit.limiter is not None  # a good one beside it still applies


def test_limits_are_built_once_per_environment(settings):
    limits = build(settings, MAIN_RATE_LIMIT="2")
    assert limits.for_environment("main") is limits.for_environment("main")


async def test_scoped_quotas_count_separately_from_the_deployment_wide_one():
    everything = RequestQuota(daily_limit=2, reservation=1)
    staging = RequestQuota(daily_limit=1, reservation=1, scope="staging")
    assert await staging.acquire() and not await staging.acquire()
    assert await everything.acquire() and await everything.acquire()
    assert not await everything.acquire()
    assert (await staging.usage())["requests"]["daily"]["used"] == 1


async def test_usage_lists_environments_that_have_limits(settings):
    limits = build(settings, MAIN_DAILY_REQUEST_LIMIT="5", MAIN_MAX_ACTIVE_CONNECTIONS="2")
    limits.for_environment("main")
    limits.for_environment("quiet")
    report = await limits.usage()
    assert list(report) == ["main"]
    assert report["main"]["requests"]["daily"]["limit"] == 5
    assert report["main"]["connections"] == {"active": 0, "limit": 2}


# ── through the proxy ────────────────────────────────────────────────────


async def test_an_environments_request_quota_is_enforced_and_names_the_environment(
    gateway, monkeypatch
):
    monkeypatch.setenv("MAIN_DAILY_REQUEST_LIMIT", "3")
    statuses = [
        (await gateway.http.get("/rest/v1/orders", headers={"apikey": "pk_anon"})).status_code
        for _ in range(4)
    ]
    assert statuses == [200, 200, 200, 429]
    refused = await gateway.http.get("/rest/v1/orders", headers={"apikey": "pk_anon"})
    assert refused.json()["error"] == "request_quota_exceeded"
    assert "environment" in refused.json()["message"]


async def test_an_environments_rate_limit_replaces_the_deployment_one_for_that_environment(
    gateway, monkeypatch
):
    # The deployment allows 5 per window per key; this environment allows 2.
    monkeypatch.setenv("MAIN_RATE_LIMIT", "2")
    statuses = [
        (await gateway.http.get("/rest/v1/orders", headers={"apikey": "sk_service"})).status_code
        for _ in range(3)
    ]
    assert statuses == [200, 200, 429]


async def test_the_deployment_wide_rate_limit_still_applies_without_an_override(gateway):
    statuses = [
        (await gateway.http.get("/rest/v1/orders", headers={"apikey": "sk_service"})).status_code
        for _ in range(7)
    ]
    assert statuses[:5] == [200] * 5 and statuses[5] == 429


async def test_gateway_usage_reports_the_environments_counters(gateway, monkeypatch):
    monkeypatch.setenv("MAIN_DAILY_REQUEST_LIMIT", "10")
    await gateway.http.get("/rest/v1/orders", headers={"apikey": "pk_anon"})
    usage = await gateway.app.state["proxy"].env_limits.usage()
    daily = usage["main"]["requests"]["daily"]
    assert daily["limit"] == 10
    # Usage counts what the gateway has leased, which is at least what was served.
    assert 1 <= daily["used"] <= 10
