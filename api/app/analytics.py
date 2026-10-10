"""An environment's traffic and activity over time, for Studio's overview.

Everything here is read from what the platform already records: request
counters (:mod:`app.request_metrics`), the event log and flow runs. Each range
is cut into equal buckets aligned to the step, and the same span before it is
summed for the "vs previous period" comparisons.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tortoise.functions import Count

from app.request_metrics import REQUESTS_METRIC
from database.models import EventLog, FlowRun, MetricCounter

#: range → (span, bucket step)
RANGES: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=5)),
    "24h": (timedelta(days=1), timedelta(hours=1)),
    "7d": (timedelta(days=7), timedelta(hours=6)),
    "30d": (timedelta(days=30), timedelta(days=1)),
}
DEFAULT_RANGE = "24h"
#: The longest custom window, so one request cannot ask for years of buckets.
MAX_WINDOW = timedelta(days=90)


def window_step(span: timedelta) -> timedelta:
    """The bucket size for a custom window: fine for short spans, a day for long ones."""
    if span <= timedelta(hours=3):
        return timedelta(minutes=5)
    if span <= timedelta(days=2):
        return timedelta(hours=1)
    if span <= timedelta(days=21):
        return timedelta(hours=6)
    return timedelta(days=1)


def _floor(moment: datetime, step: timedelta) -> datetime:
    seconds = step.total_seconds()
    return datetime.fromtimestamp((moment.timestamp() // seconds) * seconds, UTC)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _totals() -> dict[str, float]:
    return {
        "requests": 0,
        "errors": 0,
        "latency_total": 0.0,
        "events": 0,
        "flow_runs": 0,
        "flow_failures": 0,
    }


def _summary(totals: dict[str, float]) -> dict[str, Any]:
    requests = int(totals["requests"])
    runs = int(totals["flow_runs"])
    return {
        "requests": requests,
        "errors": int(totals["errors"]),
        "error_rate": round(totals["errors"] / requests * 100, 2) if requests else 0.0,
        "avg_latency_ms": round(totals["latency_total"] / requests, 1) if requests else 0.0,
        "events": int(totals["events"]),
        "flow_runs": runs,
        "flow_failures": int(totals["flow_failures"]),
        "flow_success_rate": round((runs - totals["flow_failures"]) / runs * 100, 1)
        if runs
        else None,
    }


async def environment_analytics(
    env: str,
    range_name: str = DEFAULT_RANGE,
    window: tuple[datetime, datetime] | None = None,
) -> dict[str, Any]:
    """Analytics for a named range, or for a custom ``window`` of (from, to)."""
    if window is not None:
        first, last = _aware(window[0]), _aware(window[1])
        span = min(max(last - first, timedelta(hours=1)), MAX_WINDOW)
        step = window_step(span)
        range_name = "custom"
        end = _floor(last, step) + step
        span = step * max(1, -(-span // step))
    else:
        span, step = RANGES.get(range_name, RANGES[DEFAULT_RANGE])
        range_name = range_name if range_name in RANGES else DEFAULT_RANGE
        end = _floor(datetime.now(UTC), step) + step
    start = end - span
    since = start - span
    count = int(span / step)
    series = [
        {
            "t": (start + step * i).isoformat(),
            "requests": 0,
            "errors": 0,
            "client_errors": 0,
            "latency_ms": None,
            "events": 0,
            "flow_runs": 0,
            "flow_failures": 0,
        }
        for i in range(count)
    ]
    current, previous = _totals(), _totals()

    def place(moment: datetime) -> tuple[dict[str, Any] | None, dict[str, float] | None]:
        moment = _aware(moment)
        if moment >= start:
            index = int((moment - start) / step)
            return (series[index] if 0 <= index < count else None), current
        if moment >= since:
            return None, previous
        return None, None

    latency: list[float] = [0.0] * count
    rows = await MetricCounter.filter(env=env, name=REQUESTS_METRIC, window__gte=since).values(
        "window", "tags", "value", "count"
    )
    for row in rows:
        bucket, totals = place(row["window"])
        if totals is None:
            continue
        server_error = "status=5xx" in row["tags"]
        totals["requests"] += row["count"]
        totals["latency_total"] += row["value"]
        if server_error:
            totals["errors"] += row["count"]
        if bucket is not None:
            bucket["requests"] += row["count"]
            if server_error:
                bucket["errors"] += row["count"]
            if "status=4xx" in row["tags"]:
                bucket["client_errors"] += row["count"]
            latency[series.index(bucket)] += row["value"]
    for index, bucket in enumerate(series):
        if bucket["requests"]:
            bucket["latency_ms"] = round(latency[index] / bucket["requests"], 1)

    for moment in await EventLog.filter(env=env, created_at__gte=since).values_list(
        "created_at", flat=True
    ):
        bucket, totals = place(moment)
        if totals is not None:
            totals["events"] += 1
        if bucket is not None:
            bucket["events"] += 1

    for moment, status in await FlowRun.filter(env=env, created_at__gte=since).values_list(
        "created_at", "status"
    ):
        bucket, totals = place(moment)
        failed = status == "failed"
        if totals is not None:
            totals["flow_runs"] += 1
            totals["flow_failures"] += failed
        if bucket is not None:
            bucket["flow_runs"] += 1
            bucket["flow_failures"] += failed

    top_events = (
        await EventLog.filter(env=env, created_at__gte=start)
        .annotate(total=Count("id"))
        .group_by("name")
        .order_by("-total")
        .limit(6)
        .values("name", "total")
    )
    top_flows = (
        await FlowRun.filter(env=env, created_at__gte=start)
        .annotate(total=Count("id"))
        .group_by("flow")
        .order_by("-total")
        .limit(6)
        .values("flow", "total")
    )
    failures = (
        await FlowRun.filter(env=env, created_at__gte=start, status="failed")
        .order_by("-created_at")
        .limit(5)
        .values("id", "flow", "error", "created_at")
    )
    return {
        "range": range_name,
        "step_seconds": int(step.total_seconds()),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "summary": _summary(current),
        "previous": _summary(previous),
        "series": series,
        "top_events": [{"name": r["name"], "count": r["total"]} for r in top_events],
        "top_flows": [{"name": r["flow"], "count": r["total"]} for r in top_flows],
        "recent_failures": [
            {
                "id": r["id"],
                "flow": r["flow"],
                "error": (r["error"] or "")[:200],
                "at": _aware(r["created_at"]).isoformat(),
            }
            for r in failures
        ],
    }
