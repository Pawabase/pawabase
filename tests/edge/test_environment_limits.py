"""Project policies sit inside deployment-wide limits and are cached by version."""

from pawabase_server.edge.limits import EnvironmentLimits
from pawabase_server.edge.quotas import RequestQuota


def build(settings):
    return EnvironmentLimits(settings, key_func=lambda ctx: "k")


def test_a_project_with_no_policy_adds_no_limits(settings):
    assert build(settings).for_project("project-a", 1, {}) is None


def test_a_policy_builds_a_quota_limiter_and_connection_cap(settings):
    limit = build(settings).for_project(
        "project-a", 3, {"daily_request_limit": 10, "rate_limit": 2, "max_active_connections": 3}
    )
    assert limit.quota.daily_limit == 10 and limit.quota.scope == "project:project-a"
    assert limit.limiter is not None and limit.rate_limit == 2
    assert limit.rate_window == 0  # deployment window is used when omitted
    assert limit.connections._value == 3


def test_limits_are_shared_by_project_and_rebuilt_when_the_version_changes(settings):
    limits = build(settings)
    first = limits.for_project("project-a", 1, {"daily_request_limit": 10})
    assert first is limits.for_project("project-a", 1, {"daily_request_limit": 10})
    assert limits.for_project("project-b", 1, {"daily_request_limit": 10}) is not first
    changed = limits.for_project("project-a", 2, {"daily_request_limit": 4})
    assert changed is not first and changed.quota.daily_limit == 4


def test_bad_persisted_values_are_ignored(settings, caplog):
    limit = build(settings).for_project(
        "project-a", 1, {"daily_request_limit": "lots", "rate_limit": 4}
    )
    assert "invalid project limit" in caplog.text
    assert limit.quota is None and limit.limiter is not None


async def test_scoped_quotas_count_separately_from_the_deployment_wide_one():
    everything = RequestQuota(daily_limit=2, reservation=1)
    project = RequestQuota(daily_limit=1, reservation=1, scope="project:staging")
    assert await project.acquire() and not await project.acquire()
    assert await everything.acquire() and await everything.acquire()
    assert not await everything.acquire()


async def test_usage_lists_cached_projects(settings):
    limits = build(settings)
    limits.for_project("project-a", 7, {"daily_request_limit": 5, "max_active_connections": 2})
    report = await limits.usage()
    assert list(report) == ["project-a"]
    assert report["project-a"]["version"] == 7
    assert report["project-a"]["requests"]["daily"]["limit"] == 5


async def test_a_projects_request_quota_is_enforced(gateway, monkeypatch):
    monkeypatch.setitem(
        gateway.api.lookup.__globals__["KEYS"]["pk_anon"],
        "project_limits",
        {"daily_request_limit": 3},
    )
    statuses = [
        (await gateway.http.get("/rest/v1/orders", headers={"apikey": "pk_anon"})).status_code
        for _ in range(4)
    ]
    assert statuses == [200, 200, 200, 429]
    refused = await gateway.http.get("/rest/v1/orders", headers={"apikey": "pk_anon"})
    assert refused.json()["error"] == "request_quota_exceeded"
    assert "project" in refused.json()["message"]


async def test_a_projects_rate_limit_replaces_the_deployment_one(gateway, monkeypatch):
    monkeypatch.setitem(
        gateway.api.lookup.__globals__["KEYS"]["sk_service"], "project_limits", {"rate_limit": 2}
    )
    statuses = [
        (await gateway.http.get("/rest/v1/orders", headers={"apikey": "sk_service"})).status_code
        for _ in range(3)
    ]
    assert statuses == [200, 200, 429]


async def test_the_deployment_wide_rate_limit_still_applies_without_a_project_policy(gateway):
    statuses = [
        (await gateway.http.get("/rest/v1/orders", headers={"apikey": "sk_service"})).status_code
        for _ in range(7)
    ]
    assert statuses[:5] == [200] * 5 and statuses[5] == 429


async def test_gateway_usage_reports_project_counters(gateway, monkeypatch):
    monkeypatch.setitem(
        gateway.api.lookup.__globals__["KEYS"]["pk_anon"],
        "project_limits",
        {"daily_request_limit": 10},
    )
    await gateway.http.get("/rest/v1/orders", headers={"apikey": "pk_anon"})
    daily = (await gateway.edge.env_limits.usage())["project-main"]["requests"]["daily"]
    assert daily["limit"] == 10 and 1 <= daily["used"] <= 10
