"""Metrics, logs and alarms: what Studio's Observability reads and writes beyond the fixed reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app import alarms, insights
from app.platform import Platform
from database.models import AlarmEvent, AlarmRule
from routes.common import MANAGE, audit, dump, get_environment


class AlarmBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    metric: str
    comparison: Literal["gt", "gte", "lt", "lte"] = "gt"
    threshold: float
    window_minutes: int = Field(default=5, ge=1, le=1440)
    breaches_needed: int = Field(default=1, ge=1, le=60)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    renotify_minutes: int = Field(default=0, ge=0, le=10080)


class AlarmPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    metric: str | None = None
    comparison: Literal["gt", "gte", "lt", "lte"] | None = None
    threshold: float | None = None
    window_minutes: int | None = Field(default=None, ge=1, le=1440)
    breaches_needed: int | None = Field(default=None, ge=1, le=60)
    recipients: list[str] | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    renotify_minutes: int | None = Field(default=None, ge=0, le=10080)
    #: Silence notifications for this many minutes; 0 un-mutes.
    mute_minutes: int | None = Field(default=None, ge=0, le=43200)


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    def check_metric(name: str) -> None:
        if name not in insights.METRICS:
            raise HTTPException(
                status_code=422, detail=f"metric is one of {', '.join(insights.METRICS)}"
            )

    # ── metrics and logs ─────────────────────────────────────────────────

    @r.get(
        f"{base}/metric-catalog",
        auth=MANAGE,
        tags=["observability"],
        summary="The metrics you can chart or alarm on",
    )
    async def catalog(ctx: HttpContext, env: str):
        return {"data": [{"name": name, **spec} for name, spec in insights.METRICS.items()]}

    @r.get(
        f"{base}/metric-series", auth=MANAGE, tags=["observability"], summary="One metric over time"
    )
    async def metric(ctx: HttpContext, env: str):
        await get_environment(env)
        q = ctx.query_params
        name = q.get("metric", "requests")
        check_metric(name)
        minutes = max(5, min(int(q.get("minutes", 60)), 20160))
        period = max(1, min(int(q.get("period", max(1, minutes // 60))), 1440))
        result = await insights.series(env, name, minutes, period)
        if q.get("compare") == "previous" and not result["instant"]:
            before = await insights.series(env, name, minutes * 2, period)
            half = len(before["points"]) // 2
            result["previous"] = before["points"][:half]
        return result

    @r.get(
        f"{base}/logs",
        auth=MANAGE,
        tags=["observability"],
        summary="Search application and access logs",
    )
    async def logs(ctx: HttpContext, env: str):
        await get_environment(env)
        q = ctx.query_params
        minutes = max(1, min(int(q.get("minutes", 60)), 20160))
        limit = max(1, min(int(q.get("limit", 200)), 1000))
        after = None
        if q.get("after"):
            try:
                after = datetime.fromisoformat(q["after"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(
                    status_code=422, detail="after must be an ISO 8601 time"
                ) from exc
        return await insights.search_logs(env, minutes, q.get("q", ""), limit, after=after)

    # ── alarms ───────────────────────────────────────────────────────────

    @r.get(f"{base}/alarms", auth=MANAGE, tags=["alarms"], summary="Alarm rules and their state")
    async def list_alarms(ctx: HttpContext, env: str):
        await get_environment(env)
        return {"data": [alarms.public(rule) for rule in await AlarmRule.filter(env=env)]}

    @r.post(
        f"{base}/alarms",
        auth=MANAGE,
        tags=["alarms"],
        request_model=AlarmBody,
        summary="Create an alarm",
    )
    async def create_alarm(ctx: HttpContext, env: str, body: AlarmBody):
        await get_environment(env)
        check_metric(body.metric)
        if await AlarmRule.filter(env=env, name=body.name).exists():
            raise HTTPException(status_code=409, detail="an alarm with that name exists")
        rule = await AlarmRule.create(env=env, **body.model_dump())
        await audit(ctx, "alarm.created", env=env, target=rule.name)
        return created(alarms.public(rule))

    @r.patch(
        f"{base}/alarms/{{alarm_id}}",
        auth=MANAGE,
        tags=["alarms"],
        request_model=AlarmPatch,
        summary="Change an alarm",
    )
    async def update_alarm(ctx: HttpContext, env: str, alarm_id: str, body: AlarmPatch):
        rule = await AlarmRule.get_or_none(id=alarm_id, env=env)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such alarm")
        changes = body.model_dump(exclude_unset=True)
        if "metric" in changes:
            check_metric(changes["metric"])
        mute = changes.pop("mute_minutes", None)
        for key, value in changes.items():
            setattr(rule, key, value)
        if mute is not None:
            rule.muted_until = datetime.now(UTC) + timedelta(minutes=mute) if mute else None
        if changes.keys() & {
            "metric",
            "comparison",
            "threshold",
            "window_minutes",
            "breaches_needed",
        }:
            rule.breach_count = 0
        await rule.save()
        await audit(ctx, "alarm.updated", env=env, target=rule.name)
        return alarms.public(rule)

    @r.delete(
        f"{base}/alarms/{{alarm_id}}", auth=MANAGE, tags=["alarms"], summary="Delete an alarm"
    )
    async def delete_alarm(ctx: HttpContext, env: str, alarm_id: str):
        rule = await AlarmRule.get_or_none(id=alarm_id, env=env)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such alarm")
        await rule.delete()
        await audit(ctx, "alarm.deleted", env=env, target=rule.name)
        return no_content()

    @r.post(
        f"{base}/alarms/{{alarm_id}}/check",
        auth=MANAGE,
        tags=["alarms"],
        summary="Check an alarm now",
    )
    async def check_alarm(ctx: HttpContext, env: str, alarm_id: str):
        rule = await AlarmRule.get_or_none(id=alarm_id, env=env)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such alarm")
        event = await alarms.evaluate(platform, rule)
        return {"alarm": alarms.public(rule), "changed": dump(event) if event else None}

    @r.post(
        f"{base}/alarms/{{alarm_id}}/test",
        auth=MANAGE,
        tags=["alarms"],
        summary="Send the alarm's recipients a test email",
    )
    async def test_alarm(ctx: HttpContext, env: str, alarm_id: str):
        rule = await AlarmRule.get_or_none(id=alarm_id, env=env)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such alarm")
        sent, why = await alarms.notify(platform, rule, "alarm", rule.last_value)
        return {"sent": sent, "error": why}

    @r.get(
        f"{base}/alarms/{{alarm_id}}/events",
        auth=MANAGE,
        tags=["alarms"],
        summary="An alarm's history",
    )
    async def alarm_events(ctx: HttpContext, env: str, alarm_id: str):
        rule = await AlarmRule.get_or_none(id=alarm_id, env=env)
        if rule is None:
            raise HTTPException(status_code=404, detail="no such alarm")
        rows = await AlarmEvent.filter(rule=rule).order_by("-id").limit(100)
        return {"data": [dump(row) for row in rows]}

    @r.get(
        f"{base}/alarm-events",
        auth=MANAGE,
        tags=["alarms"],
        summary="Recent alarm changes across the environment",
    )
    async def environment_alarm_events(ctx: HttpContext, env: str):
        await get_environment(env)
        rows = await AlarmEvent.filter(env=env).order_by("-id").limit(100).prefetch_related("rule")
        return {"data": [{**dump(row), "alarm": row.rule.name} for row in rows]}
