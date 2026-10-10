"""Alarms, metrics and log search."""

from datetime import UTC, datetime, timedelta

from database.models import FunctionRun, JobRun, MailLog

ENV = "/platform/v1/envs/development"


async def _failed_job(job_id):
    await JobRun.create(id=job_id, env="development", queue="default", job="X", status="failed")


async def _alarm(api, **extra):
    body = {
        "name": "jobs failing",
        "metric": "jobs_failed",
        "comparison": "gt",
        "threshold": 0,
        "window_minutes": 60,
        "recipients": ["ops@example.com"],
        **extra,
    }
    return await api.studio.post(f"{ENV}/alarms", json=body)


async def test_an_alarm_fires_mails_the_recipients_and_recovers(api):
    rule = await _alarm(api)
    first = await api.studio.post(f"{ENV}/alarms/{rule['id']}/check")
    assert first["alarm"]["state"] == "ok" and first["changed"]["to_state"] == "ok"
    assert await MailLog.filter(env="development").count() == 0  # the first look is not news

    await _failed_job("a")
    fired = await api.studio.post(f"{ENV}/alarms/{rule['id']}/check")
    assert fired["alarm"]["state"] == "alarm" and fired["changed"]["notified"] is True
    mail = await MailLog.filter(env="development").first()
    assert mail.to == ["ops@example.com"] and "[ALARM] jobs failing" in mail.subject

    await JobRun.filter(id="a").delete()
    healed = await api.studio.post(f"{ENV}/alarms/{rule['id']}/check")
    assert healed["alarm"]["state"] == "ok"
    assert await MailLog.filter(env="development").count() == 2
    history = (await api.studio.get(f"{ENV}/alarms/{rule['id']}/events"))["data"]
    assert [e["to_state"] for e in history] == ["ok", "alarm", "ok"]


async def test_several_breaches_in_a_row_are_needed_when_asked(api):
    rule = await _alarm(api, name="patient", breaches_needed=2)
    await _failed_job("b")
    one = await api.studio.post(f"{ENV}/alarms/{rule['id']}/check")
    assert one["alarm"]["state"] == "ok" and one["alarm"]["breach_count"] == 1
    two = await api.studio.post(f"{ENV}/alarms/{rule['id']}/check")
    assert two["alarm"]["state"] == "alarm"


async def test_a_muted_alarm_changes_state_without_mailing(api):
    rule = await _alarm(api, name="quiet")
    await api.studio.patch(f"{ENV}/alarms/{rule['id']}", json={"mute_minutes": 30})
    await _failed_job("c")
    result = await api.studio.post(f"{ENV}/alarms/{rule['id']}/check")
    assert result["alarm"]["state"] == "alarm" and result["alarm"]["muted"] is True
    assert result["changed"]["notified"] is False
    assert await MailLog.filter(env="development").count() == 0


async def test_metric_series_and_the_catalog(api):
    await _failed_job("d")
    series = await api.studio.get(
        f"{ENV}/metric-series", params={"metric": "jobs_failed", "minutes": 60, "period": 10}
    )
    assert sum(p["value"] for p in series["points"]) == 1 and series["unit"] == "count"
    instant = await api.studio.get(f"{ENV}/metric-series", params={"metric": "jobs_waiting"})
    assert instant["instant"] is True and instant["latest"] == 0
    names = {m["name"] for m in (await api.studio.get(f"{ENV}/metric-catalog"))["data"]}
    assert {"requests", "error_rate", "latency_p95", "secrets_due"} <= names


async def test_log_search_reads_function_logs_and_filters_them(api):
    now = datetime.now(UTC)
    await FunctionRun.create(
        id="r1",
        env="development",
        function="orders.pay",
        trigger="http",
        status="failed",
        logs=[
            {
                "level": "info",
                "message": "charging card",
                "timestamp": (now - timedelta(seconds=5)).isoformat(),
            },
            {"level": "error", "message": "card declined by bank", "timestamp": now.isoformat()},
        ],
    )
    everything = await api.studio.get(f"{ENV}/logs", params={"q": "source:function"})
    assert everything["matched"] == 2 and everything["levels"] == {"info": 1, "error": 1}
    errors = await api.studio.get(f"{ENV}/logs", params={"q": 'level:error "declined"'})
    assert [e["message"] for e in errors["data"]] == ["card declined by bank"]
    none = await api.studio.get(f"{ENV}/logs", params={"q": "declined -bank"})
    assert none["matched"] == 0
