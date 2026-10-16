"""The advanced firewall: conditions, actions, bans, managed signatures, automatic bans, recording, and its Studio API."""

import pytest

from pawabase_server.core import firewall as fw
from pawabase_server.core.clients import ServiceError
from pawabase_server.db.models import FirewallBan, FirewallEvent, FirewallRule
from pawabase_server.edge.firewall import Firewall
from pawabase_server.edge.request import Request

ENV = "/platform/v1/envs/development"


def request(path="/rest/v1/notes", *, ip="203.0.113.7", method="GET", query="", headers=None):
    raw = [
        (k.encode(), v.encode()) for k, v in {"user-agent": "app/1.0", **(headers or {})}.items()
    ]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode(),
        "headers": raw,
    }
    return Request.of(scope, ip)


def test_conditions_cover_every_operator():
    facts = fw.Facts(
        ip="10.1.2.3",
        method="POST",
        path="/rest/v1/orders",
        query="a=1",
        headers={"user-agent": "Bot/2", "x-tenant": "acme"},
        params={"a": "1"},
        content_length=5000,
    )

    def hold(field, op, value=None):
        return fw.condition_holds({"field": field, "op": op, "value": value}, facts)

    assert (
        hold("path", "starts_with", "/rest/")
        and hold("path", "glob", "/rest/v1/*")
        and hold("path", "regex", r"orders$")
    )
    assert hold("ip", "in_cidr", ["10.0.0.0/8"]) and not hold("ip", "not_in_cidr", ["10.0.0.0/8"])
    assert hold("method", "in", ["post", "put"]) and hold("header:x-tenant", "equals", "ACME")
    assert (
        hold("query:a", "equals", "1")
        and hold("header:x-missing", "missing")
        and hold("user_agent", "contains", "bot")
    )
    assert hold("content_length", "gt", "4096") and not hold("content_length", "lt", "10")
    with pytest.raises(fw.RuleError):
        fw.clean_conditions([{"field": "nope", "op": "equals", "value": "x"}])
    with pytest.raises(fw.RuleError):
        fw.clean_conditions([{"field": "path", "op": "regex", "value": "("}])


def test_managed_signatures():
    on = {k: "block" for k in fw.MANAGED}
    hit = lambda **kw: fw.managed_hit(on, fw.Facts(**{"headers": {"user-agent": "app"}, **kw}))  # noqa: E731
    assert hit(path="/rest/v1/x", query="id=1%27%20OR%201%3D1")[0] == "sqli"
    assert hit(path="/rest/v1/x", query="q=<script>alert(1)</script>")[0] == "xss"
    assert hit(path="/files/../../etc/passwd")[0] == "traversal"
    assert hit(path="/.env")[0] == "sensitive_files"
    assert hit(path="/rest/v1/x", headers={"user-agent": "sqlmap/1.7"})[0] == "scanners"
    assert hit(path="/rest/v1/x", headers={})[0] == "no_user_agent"
    assert hit(path="/rest/v1/x", method="TRACE")[0] == "methods"
    assert hit(path="/rest/v1/notes", query="limit=10&order=created_at") is None


async def rule(**fields):
    count = await FirewallRule.filter(env="development").count()
    return await FirewallRule.create(
        env="development", name=fields.pop("name", f"r{count}"), position=count, **fields
    )


async def test_rules_bans_rate_limits_and_recording(api):
    engine = Firewall()
    await rule(
        name="log admin",
        action="log",
        conditions=[{"field": "path", "op": "starts_with", "value": "/rest/v1/admin"}],
    )
    await rule(
        name="watch",
        action="block",
        mode="simulate",
        conditions=[{"field": "header:x-test", "op": "exists", "value": None}],
    )
    await rule(
        name="no delete",
        action="block",
        conditions=[{"field": "method", "op": "equals", "value": "DELETE"}],
    )
    await rule(
        name="burst",
        action="rate_limit",
        conditions=[{"field": "path", "op": "starts_with", "value": "/rest/"}],
        rate_limit={"requests": 3, "window_seconds": 60, "by": "ip", "ban_seconds": 600},
    )
    assert (
        await engine.check(
            request("/rest/v1/admin/x", headers={"x-test": "1"}), None, "development"
        )
        is None
    )  # logged + simulated
    refused = await engine.check(request(method="DELETE"), None, "development")
    assert refused.status == 403 and refused.error == "firewall_blocked"
    # Counted: the admin request (1); the DELETE is blocked before reaching the limit; two more fill it (3); the next is over.
    assert await engine.check(request(), None, "development") is None
    assert await engine.check(request(), None, "development") is None
    limited = await engine.check(request(), None, "development")
    assert (
        limited.status == 429
    )  # admin(1) + delete(2, refused before counting? no: block decides first) + 2 more
    assert await FirewallBan.filter(
        env="development", ip="203.0.113.7", source="rate_limit"
    ).exists()
    banned = await engine.check(request("/rest/v1/other"), None, "development")
    assert banned.error == "ip_banned"
    other = await engine.check(request(ip="198.51.100.1"), None, "development")
    assert other is None  # a different address is unaffected
    await engine.flush()
    actions = sorted(await FirewallEvent.filter(env="development").values_list("action", flat=True))
    assert {"log", "block", "rate_limit"} <= set(actions)
    assert (await FirewallRule.get(name="no delete")).hits == 1


async def test_allow_rule_beats_a_ban_and_auto_ban(api):
    engine = Firewall()
    await api.studio.put(
        f"{ENV}/firewall-config",
        json={
            "auto_ban": {
                "enabled": True,
                "threshold": 3,
                "window_seconds": 60,
                "ban_seconds": 120,
                "statuses": [401],
            }
        },
    )
    for _ in range(3):
        await engine.observe("development", "192.0.2.9", 401)
    assert (await engine.check(request(ip="192.0.2.9"), None, "development")).error == "ip_banned"
    await rule(
        name="office",
        action="allow",
        conditions=[{"field": "ip", "op": "in_cidr", "value": ["192.0.2.0/24"]}],
    )
    engine.invalidate("development")
    assert await engine.check(request(ip="192.0.2.9"), None, "development") is None


async def test_studio_api_config_bans_stats_and_tester(api):
    config = await api.studio.put(
        f"{ENV}/firewall-config", json={"managed": {"sqli": "block", "scanners": "log"}}
    )
    actions = {m["key"]: m["action"] for m in config["managed"]}
    assert actions["sqli"] == "block" and actions["scanners"] == "log" and actions["xss"] == "off"
    ban = await api.studio.post(
        f"{ENV}/firewall-bans", json={"ip": "198.51.100.0/24", "seconds": 600, "reason": "abuse"}
    )
    assert [b["ip"] for b in (await api.studio.get(f"{ENV}/firewall-bans"))["data"]] == [
        "198.51.100.0/24"
    ]
    verdict = await api.studio.post(
        f"{ENV}/firewall-test", json={"ip": "198.51.100.4", "path": "/rest/v1/x"}
    )
    assert verdict["outcome"] == "block" and verdict["steps"][0]["step"] == "ban"
    await api.studio.delete(f"{ENV}/firewall-bans/{ban['id']}")
    sqli = await api.studio.post(
        f"{ENV}/firewall-test",
        json={"ip": "198.51.100.4", "path": "/rest/v1/x", "query": "id=1' OR 1=1"},
    )
    assert sqli["outcome"] == "block" and sqli["steps"][-1]["step"] == "managed"
    limited = await api.studio.post(
        f"{ENV}/firewall",
        json={
            "name": "api burst",
            "action": "rate_limit",
            "conditions": [{"field": "path", "op": "glob", "value": "/rest/*"}],
            "rate_limit": {"requests": 100, "window_seconds": 60},
        },
    )
    assert limited["rate_limit"]["by"] == "ip"
    with pytest.raises(ServiceError):
        await api.studio.post(
            f"{ENV}/firewall",
            json={
                "name": "bad",
                "action": "rate_limit",
                "conditions": [{"field": "path", "op": "glob", "value": "/x"}],
            },
        )
    await FirewallEvent.create(
        env="development",
        ip="1.2.3.4",
        method="GET",
        path="/x",
        source="rule",
        action="block",
        rule_name="r",
    )
    stats = await api.studio.get(f"{ENV}/firewall-stats?hours=1")
    assert stats["total"] == 1 and stats["top_ips"][0]["value"] == "1.2.3.4"
