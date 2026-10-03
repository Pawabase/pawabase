"""Route statistics, error groups, request spans and captured logs."""

from datetime import datetime

import pytest
from app.route_stats import NO_ROUTE, aggregate_routes, error_groups, percentile, route_breakdown, signature

from pawabase_core.clients import ServiceError

ENV = "/platform/v1/projects/acme/envs/development"


def row(i, status=200, ms=10.0, route="/o/{id}", method="GET", error=None, user=None, spans=()):
    return {
        "request_id": f"r{i}",
        "method": method,
        "path": f"/o/{i}",
        "route": route,
        "status": status,
        "duration_ms": ms,
        "started_at": f"2026-10-03T10:{i % 60:02d}:00+00:00",
        "role": "anon",
        "user": user,
        "error": error,
        "notes": {"spans": list(spans)},
    }


def test_percentiles_are_exact_over_the_window():
    values = [float(n) for n in range(1, 101)]
    assert percentile(values, 0.5) in (50.0, 51.0)
    assert percentile(values, 0.99) == 99.0 or percentile(values, 0.99) == 100.0
    assert percentile([], 0.5) == 0.0


def test_signatures_fold_ids_numbers_and_quoted_values():
    assert signature("order 17 not found") == signature("order 903 not found")
    assert signature("no user 'ada@x.com'") == signature("no user 'bob@y.org'")
    assert signature("deadbeef12345678 failed") == signature("cafebabe87654321 failed")
    assert signature(None) == "(no message)"


def test_routes_rank_by_server_errors_then_rate():
    rows = [row(i) for i in range(20)]  # a busy, healthy route
    rows += [row(100 + i, status=500, route="/pay", method="POST", error="Timeout 30s") for i in range(3)]
    rows += [row(200 + i, route="/pay", method="POST") for i in range(3)]
    rows += [row(300, status=500, route="/rare")]
    ranked = aggregate_routes(rows, window_seconds=3600)
    assert [(r["method"], r["route"]) for r in ranked][:2] == [("POST", "/pay"), ("GET", "/rare")]
    pay = ranked[0]
    assert pay["server_errors"] == 3 and pay["requests"] == 6 and pay["error_rate"] == 50.0
    assert pay["top_problems"] == [{"problem": "Timeout #s", "count": 3}]
    assert pay["last_failure_request_id"] == "r102"
    by_rate = aggregate_routes(rows, window_seconds=3600, sort="error_rate")
    assert by_rate[0]["route"] == "/rare"  # 100% of one request outranks 50% of six
    by_traffic = aggregate_routes(rows, window_seconds=3600, sort="traffic")
    assert by_traffic[0]["route"] == "/o/{id}"


def test_slow_sort_uses_p95_and_time_sort_uses_total():
    rows = [row(i, ms=5) for i in range(30)] + [row(100 + i, ms=900, route="/export") for i in range(2)]
    assert aggregate_routes(rows, window_seconds=60, sort="slow")[0]["route"] == "/export"
    assert aggregate_routes(rows, window_seconds=60, sort="time")[0]["route"] == "/export"
    assert aggregate_routes(rows, window_seconds=60, sort="traffic")[0]["route"] == "/o/{id}"


def test_unmatched_requests_are_grouped_not_dropped():
    ranked = aggregate_routes([row(1, status=404, route=None)], window_seconds=60)
    assert ranked[0]["route"] == NO_ROUTE and ranked[0]["client_errors"] == 1


def test_error_groups_put_server_errors_first_and_count_variants_once():
    rows = [
        row(1, 404, error="order 5 not found"),
        row(2, 404, error="order 6 not found"),
        row(3, 404, error="order 7 not found"),
        row(4, 500, error="KeyError: 'sku'", user="u1"),
        row(5, 200),
    ]
    groups = error_groups(rows)
    assert [g["status"] for g in groups] == [500, 404]
    assert groups[1]["count"] == 3 and groups[1]["problem"] == "order # not found"
    assert groups[0]["users"] == 1 and groups[0]["sample_request_id"] == "r4"


def test_route_breakdown_shows_where_the_time_goes_without_double_counting():
    spans = [
        {"id": 1, "parent": None, "kind": "db", "duration_ms": 40.0, "status": "ok"},
        {"id": 2, "parent": 1, "kind": "cache", "duration_ms": 10.0, "status": "ok"},
        {"id": 3, "parent": None, "kind": "http", "duration_ms": 60.0, "status": "error"},
    ]
    rows = [row(1, ms=120, spans=spans), row(2, 500, ms=300, error="boom")]
    detail = route_breakdown(
        rows,
        since=datetime.fromisoformat("2026-10-03T10:00:00+00:00"),
        until=datetime.fromisoformat("2026-10-03T11:00:00+00:00"),
    )
    kinds = {k["kind"]: k for k in detail["time_by_kind"]}
    assert kinds["db"]["total_ms"] == 40.0 and kinds["http"]["errors"] == 1
    assert kinds["cache"]["total_ms"] == 0.0  # nested under db, already inside its 40 ms
    assert detail["slowest"][0]["request_id"] == "r2"
    assert detail["recent_failures"][0]["request_id"] == "r2"
    assert sum(point["requests"] for point in detail["timeline"]) == 2


async def seed(api):
    await api.studio.post("/platform/v1/projects", json={"ref": "acme", "name": "Acme"})
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
    return api.context_headers("acme", "development")


async def test_a_request_trace_has_timed_spans_for_what_it_did(api):
    anon = await seed(api)
    assert (await api.http.post("/rest/v1/notes", json={"text": "a"}, headers=anon)).status_code == 201
    await api.drain()
    await api.app.state["request_rollup"].flush()

    history = await api.studio.get(f"{ENV}/requests", params={"search": "/rest/v1/notes"})
    created = next(r for r in history["data"] if r["method"] == "POST")
    spans = created["notes"]["spans"]
    kinds = {s["kind"] for s in spans}
    assert {"db", "policy", "event"} <= kinds
    assert all(s["duration_ms"] >= 0 and s["start_ms"] >= 0 for s in spans)
    assert any(s["kind"] == "db" and "notes" in s["name"] and s["attrs"].get("op") for s in spans)
    # SQL spans carry the statement and a row count, never parameter values.
    insert = next(s for s in spans if s["kind"] == "db" and s["attrs"].get("op") == "insert")
    assert "params" in insert["attrs"] and "a" not in str(insert["attrs"].get("params"))


async def test_routes_and_errors_are_ranked_from_real_traffic(api):
    anon = await seed(api)
    for text in ("a", "b"):
        await api.http.post("/rest/v1/notes", json={"text": text}, headers=anon)
    await api.http.get("/rest/v1/notes", headers=anon)
    for _ in range(3):
        assert (await api.http.post("/rest/v1/notes", json={}, headers=anon)).status_code == 422
    await api.drain()

    ranking = await api.studio.get(f"{ENV}/observability/routes", params={"minutes": 5, "sort": "traffic"})
    assert ranking["requests"] == 6 and not ranking["truncated"]
    top = ranking["routes"][0]
    assert (top["method"], top["requests"]) == ("POST", 5) and top["client_errors"] == 3
    assert top["failure_rate"] == 60.0 and top["p95_ms"] > 0

    detail = await api.studio.get(
        f"{ENV}/observability/routes/detail",
        params={"method": top["method"], "route": top["route"], "minutes": 5},
    )
    assert {s["status"]: s["count"] for s in detail["statuses"]} == {201: 2, 422: 3}
    assert detail["errors"][0]["count"] == 3 and detail["time_by_kind"]

    errors = await api.studio.get(f"{ENV}/observability/errors", params={"minutes": 5})
    assert errors["failures"] == 3 and errors["groups"][0]["status"] == 422

    assert top["route"] == "/rest/v1/notes"  # the template, not each request's own path

    with pytest.raises(ServiceError, match="method and route are required"):
        await api.studio.get(f"{ENV}/observability/routes/detail", params={"method": "GET"})


async def test_the_request_trace_merges_spans_logs_and_summary(api):
    import logging

    anon = await seed(api)
    assert (await api.http.post("/rest/v1/notes", json={"text": "a"}, headers=anon)).status_code == 201
    await api.drain()
    await api.app.state["request_rollup"].flush()
    history = await api.studio.get(f"{ENV}/requests", params={"search": "/rest/v1/notes"})
    request_id = next(r for r in history["data"] if r["method"] == "POST")["request_id"]

    trace = await api.studio.get(f"{ENV}/requests/{request_id}")
    summary = trace["summary"]
    assert summary["spans"] == len(trace["spans"]) > 0
    assert summary["db_queries"] >= 1 and summary["db_ms"] >= 0
    assert summary["slowest_span"]["duration_ms"] == max(s["duration_ms"] for s in trace["spans"])
    assert summary["failed"] is False and trace["error"] is None
    assert trace["requests"][0]["route"] == "/rest/v1/notes"
    assert logging.getLogger().handlers  # the capture handler is installed
