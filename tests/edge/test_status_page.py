"""The status page's drawing and the probing behind it."""

from pawabase_server.db.models import StatusPage
from pawabase_server.observability import status

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


def test_the_page_names_the_incident_and_the_overall_state():
    page = status.render(DATA)
    assert "Acme status" in page and "Slow responses" in page
    assert "Some systems are degraded" in page


async def test_a_prober_records_each_component_for_every_enabled_environment(monkeypatch):
    recorded = []

    class Rows:
        def __init__(self, envs):
            self.envs = envs

        def __await__(self):
            async def rows():
                return [type("Row", (), {"env": env}) for env in self.envs]

            return rows().__await__()

    async def record(env, samples):
        recorded.append((env, samples))
        return len(samples)

    monkeypatch.setattr(StatusPage, "filter", lambda **kw: Rows(["main", "staging"]))
    monkeypatch.setattr(status, "record", record)

    async def up():
        return True

    async def down():
        return False

    prober = status.StatusProber({"gateway": up, "auth": up, "realtime": down})
    assert await prober.probe_once() == 2
    assert [env for env, _ in recorded] == ["main", "staging"]
    assert {s["component"]: s["ok"] for s in recorded[0][1]} == {
        "gateway": True,
        "auth": True,
        "realtime": False,
    }
