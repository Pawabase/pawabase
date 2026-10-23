"""Service health in one shape; audit filters, export and retention."""

from datetime import UTC, datetime, timedelta

from pawabase_server.db.models import AuditEntry, JobRun
from pawabase_server.observability import audit_log

ENV = "/platform/v1/envs/development"


async def test_every_service_reports_the_same_fields(api):
    await JobRun.create(
        id="j1", env="development", queue="flows", job="RunFlowJob", status="failed"
    )
    await JobRun.create(
        id="j2", env="development", queue="flows", job="RunFlowJob", status="completed"
    )
    await JobRun.create(
        id="j3", env="development", queue="flows", job="RunFlowJob", status="queued"
    )
    body = await api.studio.get(f"{ENV}/service-health?minutes=30")
    services = {s["service"]: s for s in body["data"]}
    assert set(services) >= {"events", "flows", "webhook_deliveries", "mail"}
    fn = services["flows"]
    assert (fn["failed"], fn["succeeded"], fn["waiting"], fn["failure_rate"]) == (1, 1, 1, 50.0)
    assert all(
        set(s) >= {"total", "failure_rate", "waiting", "oldest_waiting_seconds"}
        for s in body["data"]
    )


async def test_audit_filters_export_and_retention(api):
    await AuditEntry.create(
        env="development", actor="ada", action="webhook.redelivered", target="d1", details={}
    )
    await AuditEntry.create(
        env="development", actor="bob", action="database.write", target="", details={"sql": "x"}
    )
    rows = (await api.studio.get("/platform/v1/audit?action=webhook.*"))["data"]
    assert [r["action"] for r in rows] == ["webhook.redelivered"]
    assert [r["actor"] for r in (await api.studio.get("/platform/v1/audit?actor=bob"))["data"]] == [
        "bob"
    ]
    body, kind = await audit_log.export({"action": "database.*"}, "csv")
    assert kind.startswith("text/csv") and "database.write" in body and "webhook" not in body
    lines, _ = await audit_log.export({}, "jsonl")
    assert len([line for line in lines.splitlines() if line]) >= 2
    await AuditEntry.filter(actor="ada").update(created_at=datetime.now(UTC) - timedelta(days=400))
    assert await audit_log.purge(365) == 1
    assert not await AuditEntry.filter(actor="ada").exists()
