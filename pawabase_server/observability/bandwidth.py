"""Bandwidth: how many bytes an environment received and sent, and what moved the most of them.

The totals and the time series come from the per-minute counters
(:data:`~pawabase_server.observability.request_metrics.BANDWIDTH_METRIC`); the rankings come from the
request history, which carries each request's own byte counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tortoise.functions import Count, Sum

from pawabase_server.db.models import MetricCounter, RequestLog
from pawabase_server.observability.request_metrics import BANDWIDTH_METRIC

#: window in minutes → bucket size in minutes, so a chart has a sensible number of bars.
STEPS = ((15, 1), (60, 5), (360, 15), (1440, 60), (10080, 360))
LONGEST_STEP = 1440


def step_for(minutes: int) -> int:
    for limit, step in STEPS:
        if minutes <= limit:
            return step
    return LONGEST_STEP


async def top_bandwidth(
    env: str, since: datetime, *, endpoints: int = 8, callers: int = 8
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The endpoints and the callers that moved the most bytes since *since*."""

    async def ranked(columns: list[str], limit: int) -> list[dict[str, Any]]:
        rows = (
            await RequestLog.filter(env=env, started_at__gte=since.isoformat())
            .annotate(sent=Sum("bytes_out"), received=Sum("bytes_in"), hits=Count("id"))
            .group_by(*columns)
            .values(*columns, "sent", "received", "hits")
        )
        out = []
        for row in rows:
            if not row[columns[-1]]:
                continue
            sent, received = int(row["sent"] or 0), int(row["received"] or 0)
            if sent + received <= 0:
                continue
            name = " ".join(str(row[column]) for column in columns)
            out.append(
                {
                    "endpoint" if "route" in columns else "caller": name,
                    "bytes_out": sent,
                    "bytes_in": received,
                    "requests": row["hits"],
                }
            )
        out.sort(key=lambda item: item["bytes_out"] + item["bytes_in"], reverse=True)
        return out[:limit]

    return await ranked(["method", "route"], endpoints), await ranked(["user"], callers)


async def report(env: str, minutes: int) -> dict[str, Any]:
    """Bandwidth over the last *minutes*: totals, a series, and the heaviest endpoints, callers and single requests."""
    step = timedelta(minutes=step_for(minutes))
    now = datetime.now(UTC)
    end = (
        datetime.fromtimestamp(
            (now.timestamp() // step.total_seconds()) * step.total_seconds(), UTC
        )
        + step
    )
    count = max(1, -(-timedelta(minutes=minutes) // step))
    start = end - step * count
    series = [
        {"t": (start + step * i).isoformat(), "bytes_in": 0, "bytes_out": 0} for i in range(count)
    ]
    totals = {"bytes_in": 0, "bytes_out": 0}
    rows = await MetricCounter.filter(env=env, name=BANDWIDTH_METRIC, window__gte=start).values(
        "window", "tags", "value", "count"
    )
    moved = 0
    for row in rows:
        window = row["window"] if row["window"].tzinfo else row["window"].replace(tzinfo=UTC)
        index = int((window - start) / step)
        if not 0 <= index < count:
            continue
        key = "bytes_in" if "dir=in" in row["tags"] else "bytes_out"
        series[index][key] += int(row["value"])
        totals[key] += int(row["value"])
        if key == "bytes_out":
            moved += row["count"]

    endpoints, callers = await top_bandwidth(env, start, endpoints=25, callers=15)
    largest = (
        await RequestLog.filter(env=env, started_at__gte=start.isoformat(), bytes_out__gt=0)
        .order_by("-bytes_out")
        .limit(10)
        .values("request_id", "method", "path", "status", "bytes_out", "bytes_in", "started_at")
    )
    return {
        "minutes": minutes,
        "step_seconds": int(step.total_seconds()),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "summary": {
            **totals,
            "total": totals["bytes_in"] + totals["bytes_out"],
            "responses": moved,
            "average_response": round(totals["bytes_out"] / moved) if moved else 0,
        },
        "series": series,
        "endpoints": endpoints,
        "callers": callers,
        "largest": [
            {**r, "bytes_in": int(r["bytes_in"] or 0), "bytes_out": int(r["bytes_out"] or 0)}
            for r in largest
        ],
    }
