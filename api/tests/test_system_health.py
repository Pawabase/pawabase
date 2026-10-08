from datetime import UTC, datetime, timedelta

from app.system_health import Buckets, diagnose, percentile, step_for
from database.models import JobRun, MailLog

ENV = "/platform/v1/envs/development"


def test_step_and_percentile():
    assert step_for(60) == 1 and step_for(1440) == 30 and step_for(43200) == 720
    assert percentile([], 0.95) is None
    assert percentile([10, 20, 30, 40], 0.95) == 40


def test_grouped_failures_count_repeats():
    from app.system_health import Grouped

    group = Grouped(limit=2)
    for key in (("a", "x"), ("a", "x"), ("b", "y"), ("c", "z")):
        group.add(key, {"id": key[0]})
    assert [(i["id"], i["count"]) for i in group.top()] == [("a", 2), ("b", 1)]


def test_buckets_ignore_out_of_range():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    buckets = Buckets(start, start + timedelta(minutes=10), timedelta(minutes=5), ("a",))
    buckets.add(start + timedelta(minutes=6), "a")
    buckets.add(start - timedelta(minutes=1), "a")
    buckets.add(start + timedelta(minutes=99), "a")
    assert [row["a"] for row in buckets.rows] == [0, 1]


def test_diagnose_orders_by_severity():
    quiet = {
        "jobs": {"backlog": {"waiting": 0, "oldest_seconds": 0, "stuck": []}, "totals": {"total": 0, "success_rate": None}},
        "workers": {"alive": 1, "items": []},
        "schedules": [], "flows": {"totals": {"runs": 0, "failed": 0}},
        "webhooks": {"by_endpoint": []}, "mail": {"totals": {"failed": 0}},
    }
    assert diagnose(quiet) == []
    bad = {
        **quiet,
        "jobs": {"backlog": {"waiting": 4, "oldest_seconds": 400, "stuck": []}, "totals": {"total": 10, "success_rate": 50.0}},
        "workers": {"alive": 0, "items": [{"name": n, "alive": False, "status": "running"} for n in "abc"]},
        "mail": {"totals": {"failed": 2}},
    }
    found = diagnose(bad)
    assert found[0]["severity"] == "critical" and "no worker" in found[0]["message"]
    assert [f["area"] for f in found] == ["queues", "queues", "workers", "mail"]


async def test_report_endpoint(api):
    now = datetime.now(UTC)
    await JobRun.create(id="j1", env="development", queue="mail", job="send", status="succeeded", duration_ms=40.0, finished_at=now)
    await JobRun.create(id="j2", env="development", queue="mail", job="send", status="failed", error="smtp down", duration_ms=90.0, finished_at=now)
    await JobRun.create(id="j3", env="development", queue="default", job="work", status="queued", available_at=now - timedelta(minutes=10))
    await MailLog.create(env="development", to=["a@example.com"], subject="Hi", status="failed", error="refused")
    report = await api.studio.get(f"{ENV}/observability/system", params={"minutes": 60})
    assert report["window"]["minutes"] == 60 and len(report["jobs"]["series"]) >= 30
    mail_queue = next(q for q in report["jobs"]["queues"] if q["queue"] == "mail")
    assert mail_queue["total"] == 2 and mail_queue["failed"] == 1 and mail_queue["success_rate"] == 50.0
    assert report["jobs"]["backlog"]["waiting"] == 1 and report["jobs"]["backlog"]["oldest_seconds"] >= 600
    assert report["jobs"]["recent_failures"][0]["error"] == "smtp down"
    assert report["jobs"]["recent_failures"][0]["count"] == 1
    assert report["mail"]["totals"]["failed"] == 1
    assert len(report["traffic"]["series"]) == len(report["jobs"]["series"])
    assert report["traffic"]["totals"]["requests"] == 0
    assert any(p["area"] == "mail" for p in report["problems"])
