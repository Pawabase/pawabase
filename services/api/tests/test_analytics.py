"""Environment analytics: persisted request counts, events and flow runs over time."""

ENV = "/platform/v1/projects/acme/envs/development"


async def test_overview_charts_come_from_recorded_activity(api):
    await api.studio.post("/platform/v1/projects", json={"ref": "acme", "name": "Acme"})
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "notes",
            "fields": [{"name": "text", "type": "string", "required": True}],
            "operations": {"list": {"enabled": True, "policy": "public"}, "create": {"enabled": True, "policy": "public"}},
        },
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    anon = api.context_headers("acme", "development")
    for text in ("a", "b", "c"):
        assert (await api.http.post("/rest/v1/notes", json={"text": text}, headers=anon)).status_code == 201
    assert (await api.http.get("/rest/v1/notes", headers=anon)).status_code == 200
    assert (await api.http.get("/rest/v1/nothing-here", headers=anon)).status_code == 404
    await api.drain()
    await api.app.state["request_rollup"].flush()

    stats = await api.studio.get(f"{ENV}/analytics", params={"range": "1h"})
    assert stats["range"] == "1h" and len(stats["series"]) == 12
    assert stats["summary"]["requests"] == 5 and stats["summary"]["errors"] == 0
    assert sum(b["client_errors"] for b in stats["series"]) == 1
    assert stats["summary"]["avg_latency_ms"] > 0
    assert stats["summary"]["events"] == 3 == sum(b["events"] for b in stats["series"])
    assert stats["top_events"] == [{"name": "notes.created", "count": 3}]
    assert stats["previous"]["requests"] == 0

    # Studio's own management calls are not the environment's traffic.
    overview = await api.studio.get(f"{ENV}/overview")
    assert overview["analytics"]["range"] == "24h" and overview["analytics"]["summary"]["requests"] == 5
