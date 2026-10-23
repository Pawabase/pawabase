"""Environment analytics: persisted request counts, events and flow runs over time."""

ENV = "/platform/v1/envs/development"


async def test_overview_charts_come_from_recorded_activity(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "notes",
            "fields": [{"name": "text", "type": "string", "required": True}],
            "operations": {
                "list": {"enabled": True, "policy": "public"},
                "create": {"enabled": True, "policy": "public"},
            },
        },
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    anon = api.context_headers("development")
    for text in ("a", "b", "c"):
        assert (
            await api.http.post("/rest/v1/notes", json={"text": text}, headers=anon)
        ).status_code == 201
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

    # Bandwidth: what came in and went out, in total, over time and by endpoint.
    assert stats["summary"]["bytes_in"] > 0 and stats["summary"]["bytes_out"] > 0
    assert sum(b["bytes_out"] for b in stats["series"]) == stats["summary"]["bytes_out"]


    # Studio's own management calls are not the environment's traffic.
    overview = await api.studio.get(f"{ENV}/overview")
    assert (
        overview["analytics"]["range"] == "24h"
        and overview["analytics"]["summary"]["requests"] == 5
    )


async def test_custom_window_picks_its_bucket_size_from_its_span(api):
    from datetime import UTC, datetime, timedelta

    end = datetime.now(UTC)
    day = await api.studio.get(
        f"{ENV}/analytics",
        params={"from": (end - timedelta(days=1)).isoformat(), "to": end.isoformat()},
    )
    assert day["range"] == "custom" and day["step_seconds"] == 3600
    assert len(day["series"]) == 24
    month = await api.studio.get(
        f"{ENV}/analytics",
        params={"from": (end - timedelta(days=30)).isoformat(), "to": end.isoformat()},
    )
    assert month["step_seconds"] == 86400 and len(month["series"]) == 30


async def test_bandwidth_report_ranks_what_moved_the_most(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "notes",
            "fields": [{"name": "text", "type": "string", "required": True}],
            "operations": {
                "list": {"enabled": True, "policy": "public"},
                "create": {"enabled": True, "policy": "public"},
            },
        },
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    anon = api.context_headers("development")
    await api.http.post("/rest/v1/notes", json={"text": "x" * 4000}, headers=anon)
    for _ in range(2):
        await api.http.get("/rest/v1/notes", headers=anon)

    report = await api.studio.get(f"{ENV}/observability/bandwidth", params={"minutes": 60})
    assert report["summary"]["bytes_in"] >= 4000 and report["summary"]["bytes_out"] > 0
    assert report["summary"]["total"] == report["summary"]["bytes_in"] + report["summary"]["bytes_out"]
    assert sum(b["bytes_out"] for b in report["series"]) == report["summary"]["bytes_out"]
    ranked = {e["endpoint"]: e for e in report["endpoints"]}
    assert ranked["POST /rest/v1/notes"]["bytes_in"] >= 4000
    assert ranked["GET /rest/v1/notes"]["requests"] == 2
    assert report["largest"] and report["largest"][0]["bytes_out"] >= report["largest"][-1]["bytes_out"]
