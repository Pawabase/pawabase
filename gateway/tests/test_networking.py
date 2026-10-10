"""The gateway enforcing firewall rules and answering for verified custom domains."""

from app.domains import DomainSync
from tests.conftest import KEYS

RULES = [
    {
        "id": "r1",
        "name": "office",
        "action": "allow",
        "match": {"ips": ["10.0.0.0/8"], "paths": ["/rest/v1/admin/*"]},
    },
    {"id": "r2", "name": "no admin", "action": "block", "match": {"paths": ["/rest/v1/admin/*"]}},
    {"id": "r3", "name": "bots", "action": "block", "match": {"user_agents": ["badbot"]}},
]


def with_firewall(monkeypatch, rules):
    monkeypatch.setitem(KEYS, "pk_fw", {**KEYS["pk_anon"], "firewall": rules, "cors_origins": []})


async def test_first_matching_rule_decides(gateway, monkeypatch):
    with_firewall(monkeypatch, RULES)
    headers = {"apikey": "pk_fw"}
    blocked = await gateway.http.get("/rest/v1/admin/users", headers=headers)
    assert blocked.status_code == 403 and blocked.json()["error"] == "firewall_blocked"
    assert "no admin" in blocked.json()["message"]
    bot = await gateway.http.get("/rest/v1/notes", headers={**headers, "user-agent": "BadBot/1.0"})
    assert bot.status_code == 403 and "bots" in bot.json()["message"]
    assert (await gateway.http.get("/rest/v1/notes", headers=headers)).status_code == 200


async def test_allow_rule_wins_over_a_later_block(gateway, monkeypatch):
    everyone = {"user_agents": ["monitor"], "paths": ["/rest/v1/admin/*"]}
    with_firewall(monkeypatch, [RULES[0] | {"match": everyone}, RULES[1]])
    headers = {"apikey": "pk_fw", "user-agent": "Uptime-Monitor"}
    assert (await gateway.http.get("/rest/v1/admin/users", headers=headers)).status_code == 200
    other = await gateway.http.get("/rest/v1/admin/users", headers={"apikey": "pk_fw"})
    assert other.status_code == 403


async def test_no_rules_means_no_change(gateway):
    assert (
        await gateway.http.get("/rest/v1/notes", headers={"apikey": "pk_anon"})
    ).status_code == 200


async def test_verified_domains_pick_the_environment(gateway):
    async def get(path, **kw):
        assert path == "/internal/v1/domains"
        return {"data": [{"hostname": "API.Acme.com", "env": "other"}]}

    gateway.api.get = get
    sync = DomainSync(gateway.api, gateway.app.state["proxy"])
    assert await sync.refresh() == 1
    # The key belongs to "main", the hostname to "other": refused rather than served.
    refused = await gateway.http.get(
        "/rest/v1/notes", headers={"apikey": "pk_anon", "host": "api.acme.com"}
    )
    assert refused.status_code == 403 and refused.json()["error"] == "host_environment_mismatch"
    ok = await gateway.http.get(
        "/rest/v1/notes", headers={"apikey": "pk_anon", "host": "elsewhere.test"}
    )
    assert ok.status_code == 200


async def test_a_failed_refresh_keeps_what_it_had(gateway):
    proxy = gateway.app.state["proxy"]
    proxy.domains = {"keep.example.com": "main"}

    async def get(path, **kw):
        raise RuntimeError("api down")

    gateway.api.get = get
    assert await DomainSync(gateway.api, proxy).refresh() == 1
    assert proxy.domains == {"keep.example.com": "main"}
