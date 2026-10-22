"""The advisor reports what an application owner can act on, never the installation's own processes."""

from datetime import UTC, datetime

from pawabase_server.db.models import ApiKey, Environment, JobRun, RequestLog, WorkerHeartbeat

ENV = "/platform/v1/envs/development"


async def test_a_healthy_environment_is_all_clear(api):
    report = await api.studio.get(f"{ENV}/advisor")
    assert report["findings"] == []
    assert report["window_minutes"] == 60


async def test_failing_requests_and_jobs_are_reported(api):
    now = datetime.now(UTC)
    for i in range(30):
        await RequestLog.create(
            request_id=f"r{i}",
            service="api",
            env="development",
            method="GET",
            path="/rest/v1/orders",
            status=500 if i < 10 else 200,
            started_at=now.isoformat(),
        )
    for i in range(10):
        await JobRun.create(
            id=f"job{i}",
            env="development",
            queue="default",
            job="SendMail",
            status="failed" if i < 5 else "completed",
        )
    report = await api.studio.get(f"{ENV}/advisor")
    areas = {f["area"]: f for f in report["findings"]}
    assert areas["requests"]["severity"] == "critical" and "33%" in areas["requests"]["message"]
    assert areas["jobs"]["severity"] == "critical"
    assert areas["requests"]["page"].startswith("observability")


async def test_installation_processes_are_not_findings(api):
    # A worker that stopped reporting is the operator's business, not the application owner's.
    await WorkerHeartbeat.create(
        name="worker:gone",
        kind="worker",
        status="running",
        last_seen=datetime(2020, 1, 1, tzinfo=UTC),
    )
    system = await api.studio.get(f"{ENV}/observability/system")
    assert any(
        p["area"] == "workers" for p in system["problems"]
    )  # the operator's view still has it
    report = await api.studio.get(f"{ENV}/advisor")
    assert all(f["area"] not in ("workers", "queues") for f in report["findings"])


async def test_limits_close_to_full_are_reported(api, monkeypatch):
    monkeypatch.setenv("DEVELOPMENT_MAX_API_KEYS_PER_ENVIRONMENT", "2")
    environment = await Environment.get(name="development")
    active = await ApiKey.filter(environment=environment, revoked_at=None).count()
    report = await api.studio.get(f"{ENV}/advisor")
    keys = next(entry for entry in report["limits"] if entry["key"] == "api_keys")
    assert keys["limit"] == 2 and keys["used"] == active
    if active >= 2:
        finding = next(f for f in report["findings"] if f["area"] == "limits")
        assert finding["severity"] == "critical" and "API keys" in finding["title"]


async def test_one_unreadable_source_does_not_fail_the_advisor(api, monkeypatch):
    from pawabase_server.observability import advisor

    async def broken(*args, **kwargs):
        raise RuntimeError("worker table is unreachable")

    monkeypatch.setattr(advisor, "system_report", broken)
    report = await api.studio.get(f"{ENV}/advisor")
    assert report["unavailable"] == ["system"]
    assert report["findings"] == []
    assert "limits" in report
