"""The public status page: settings, incidents, samples and what the gateway reads."""

import pytest

from pawabase_core.clients import ServiceClient, ServiceError

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


async def test_a_page_is_hidden_until_it_is_enabled(api, gateway):
    settings = (await api.studio.get(f"{ENV}/status-page"))["settings"]
    assert settings["enabled"] is False and len(settings["components"]) == 4
    with pytest.raises(ServiceError) as hidden:
        await gateway.get("/internal/v1/status/development")
    assert hidden.value.status == 404

    await api.studio.put(f"{ENV}/status-page", json={**settings, "enabled": True, "title": "Acme"})
    page = await gateway.get("/internal/v1/status/development")
    assert page["title"] == "Acme" and page["overall"] == "operational"
    assert (await gateway.get("/internal/v1/status-enabled"))["data"] == ["development"]


async def test_samples_decide_a_components_state_and_uptime(api, gateway):
    settings = (await api.studio.get(f"{ENV}/status-page"))["settings"]
    await api.studio.put(f"{ENV}/status-page", json={**settings, "enabled": True})
    for ok in (True, True, True):
        await gateway.post(
            "/internal/v1/status/development/samples",
            json={"samples": [{"component": "auth", "ok": ok}, {"component": "bogus", "ok": ok}]},
        )
    page = await gateway.get("/internal/v1/status/development")
    auth = next(c for c in page["components"] if c["source"] == "auth")
    assert auth["status"] == "operational" and auth["uptime"] == 100.0 and len(auth["days"]) == 90

    await gateway.post(
        "/internal/v1/status/development/samples",
        json={"samples": [{"component": "auth", "ok": False}]},
    )
    for _ in range(2):
        await gateway.post(
            "/internal/v1/status/development/samples",
            json={"samples": [{"component": "auth", "ok": False}]},
        )
    page = await gateway.get("/internal/v1/status/development")
    auth = next(c for c in page["components"] if c["source"] == "auth")
    assert auth["status"] == "outage" and page["overall"] == "outage"


async def test_incidents_degrade_components_until_resolved(api, gateway):
    settings = (await api.studio.get(f"{ENV}/status-page"))["settings"]
    await api.studio.put(f"{ENV}/status-page", json={**settings, "enabled": True})
    incident = await api.studio.post(
        f"{ENV}/incidents",
        json={
            "title": "Slow sign-in",
            "impact": "minor",
            "components": ["Sign-in"],
            "message": "Looking",
        },
    )
    page = await gateway.get("/internal/v1/status/development")
    states = {c["name"]: c["status"] for c in page["components"]}
    assert states["Sign-in"] == "degraded" and states["API"] == "operational"
    assert page["overall"] == "degraded" and page["active"][0]["title"] == "Slow sign-in"

    await api.studio.post(
        f"{ENV}/incidents/{incident['id']}/updates",
        json={"status": "resolved", "message": "Fixed"},
    )
    page = await gateway.get("/internal/v1/status/development")
    assert page["overall"] == "operational" and page["active"] == []
    assert page["recent"][0]["updates"][-1]["message"] == "Fixed"
    assert (await api.studio.get(f"{ENV}/incidents"))["data"][0]["resolved_at"]
