"""Custom domains and firewall rules: management, verification and what the gateway reads."""

import pytest

from app import networking
from pawabase_core.clients import ServiceClient

ENV = "/platform/v1/envs/development"


@pytest.fixture
async def gateway(api):
    client = ServiceClient(
        "http://x",
        secret=api.settings.internal_secret,
        issuer="gateway",
        audience="api",
        app=api.app,
    )
    yield client
    await client.close()


async def test_a_domain_is_verified_by_its_txt_record(api, gateway, monkeypatch):
    domain = await api.studio.post(f"{ENV}/domains", json={"hostname": "API.Acme.com."})
    assert domain["hostname"] == "api.acme.com" and domain["status"] == "pending"
    assert domain["record"]["name"] == "_pawabase.api.acme.com"
    assert (await gateway.get("/internal/v1/domains"))["data"] == []

    async def none(name):
        return ["v=spf1 -all"]

    monkeypatch.setattr(networking, "txt_records", none)
    failed = await api.studio.post(f"{ENV}/domains/{domain['id']}/verify")
    assert failed["status"] == "failed" and "found: v=spf1" in failed["last_error"]

    async def right(name):
        assert name == "_pawabase.api.acme.com"
        return [domain["record"]["value"]]

    monkeypatch.setattr(networking, "txt_records", right)
    ok = await api.studio.post(f"{ENV}/domains/{domain['id']}/verify")
    assert ok["status"] == "verified" and ok["verified_at"]
    assert (await gateway.get("/internal/v1/domains"))["data"] == [
        {"hostname": "api.acme.com", "env": "development"}
    ]

    async def broken(name):
        raise OSError("no route")

    monkeypatch.setattr(networking, "txt_records", broken)
    again = await api.studio.post(f"{ENV}/domains/{domain['id']}/verify")
    assert again["status"] == "verified" and "lookup failed" in again["last_error"]


async def test_hostnames_are_checked_and_unique(api):
    for bad in ("localhost", "10.0.0.1", "-bad.example.com", "has space.com"):
        with pytest.raises(Exception) as refused:
            await api.studio.post(f"{ENV}/domains", json={"hostname": bad})
        assert "422" in str(refused.value)
    await api.studio.post(f"{ENV}/domains", json={"hostname": "a.example.com"})
    with pytest.raises(Exception) as dup:
        await api.studio.post(f"{ENV}/domains", json={"hostname": "a.example.com"})
    assert "409" in str(dup.value)


async def test_rules_are_validated_ordered_tested_and_served_to_the_gateway(api, gateway):
    for bad in ({}, {"ips": ["not-an-ip"]}, {"colour": ["red"]}):
        with pytest.raises(Exception) as refused:
            await api.studio.post(f"{ENV}/firewall", json={"name": "x", "match": bad})
        assert "422" in str(refused.value)

    block = await api.studio.post(
        f"{ENV}/firewall", json={"name": "no admin", "match": {"paths": ["/rest/v1/admin/*"]}}
    )
    allow = await api.studio.post(
        f"{ENV}/firewall",
        json={
            "name": "office",
            "action": "allow",
            "match": {"ips": ["10.0.0.0/8"], "paths": ["/rest/v1/admin/*"]},
        },
    )
    probe = {"ip": "10.1.2.3", "path": "/rest/v1/admin/x", "method": "GET"}
    assert (await api.studio.post(f"{ENV}/firewall-test", json=probe))["rule"]["name"] == "no admin"

    ordered = await api.studio.put(
        f"{ENV}/firewall-order", json={"ids": [allow["id"], block["id"]]}
    )
    assert [r["name"] for r in ordered["data"]] == ["office", "no admin"]
    verdict = await api.studio.post(f"{ENV}/firewall-test", json=probe)
    assert verdict["outcome"] == "allow" and verdict["rule"]["name"] == "office"
    outside = await api.studio.post(f"{ENV}/firewall-test", json={**probe, "ip": "8.8.8.8"})
    assert outside["outcome"] == "block"
    assert (
        await api.studio.post(f"{ENV}/firewall-test", json={**probe, "path": "/rest/v1/notes"})
    )["rule"] is None

    with pytest.raises(Exception) as partial:
        await api.studio.put(f"{ENV}/firewall-order", json={"ids": [allow["id"]]})
    assert "422" in str(partial.value)

    key = await api.studio.post(f"{ENV}/keys", json={"name": "browser", "role": "publishable"})
    resolved = await gateway.post("/internal/v1/keys/resolve", json={"key": key["key"]})
    assert [r["name"] for r in resolved["firewall"]] == ["office", "no admin"]

    await api.studio.patch(f"{ENV}/firewall/{block['id']}", json={"enabled": False})
    resolved = await gateway.post("/internal/v1/keys/resolve", json={"key": key["key"]})
    assert [r["name"] for r in resolved["firewall"]] == ["office"]
    await api.studio.delete(f"{ENV}/firewall/{allow['id']}")
    assert len((await api.studio.get(f"{ENV}/firewall"))["data"]) == 1
