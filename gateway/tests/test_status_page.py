"""The gateway's public status page and the probing behind it."""

from app.status import StatusProber

DATA = {
    "env": "main",
    "enabled": True,
    "title": "Acme status",
    "description": "How Acme is doing",
    "contact_url": "",
    "overall": "degraded",
    "components": [
        {
            "name": "API",
            "description": "",
            "source": "gateway",
            "status": "degraded",
            "uptime": 99.5,
            "days": [{"date": "2026-10-10", "uptime": 99.5}],
        }
    ],
    "active": [
        {
            "title": "Slow responses",
            "status": "investigating",
            "impact": "minor",
            "components": [],
            "updates": [
                {"at": "2026-10-10T10:00:00", "status": "investigating", "message": "Looking"}
            ],
        }
    ],
    "recent": [],
}


async def test_page_and_json_come_from_the_api(gateway, monkeypatch):
    seen = []

    async def get(path, **kw):
        seen.append(path)
        return DATA

    gateway.api.get = get
    page = await gateway.http.get("/status/main")
    assert page.status_code == 200 and "Acme status" in page.text and "Slow responses" in page.text
    assert "Some systems are degraded" in page.text
    assert (await gateway.http.get("/status/main.json")).json()["overall"] == "degraded"
    assert (await gateway.http.get("/status")).status_code == 200
    assert seen == [
        "/internal/v1/status/main",
        "/internal/v1/status/main",
        "/internal/v1/status-default",
    ]


async def test_missing_page_is_a_404(gateway):
    from pawabase_core.clients import ServiceError

    async def get(path, **kw):
        raise ServiceError(404, {"detail": "none"})

    gateway.api.get = get
    assert (await gateway.http.get("/status/nope")).status_code == 404


async def test_prober_reports_each_service_for_every_enabled_environment(gateway):
    posted = []

    async def get(path, **kw):
        return {"data": ["main", "staging"]}

    async def post(path, json):
        posted.append((path, json))

    gateway.api.get, gateway.api.post = get, post
    prober = StatusProber(gateway.api, gateway.app.state["proxy"].clients)
    assert await prober.probe_once() == 2
    assert [p for p, _ in posted] == [
        "/internal/v1/status/main/samples",
        "/internal/v1/status/staging/samples",
    ]
    assert {s["component"] for s in posted[0][1]["samples"]} == {"gateway", "auth", "realtime"}
