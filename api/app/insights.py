"""Metrics you can ask for by name, and logs you can search: what Studio's Metrics, Logs and Alarms read.

Everything here reads what the platform already records; nothing writes. A metric is a number over a window of
time (``requests`` in the last hour), or the same number bucketed into a chart. Alarms use the first form.
"""

from __future__ import annotations

import fnmatch
import shlex
from datetime import UTC, datetime, timedelta
from typing import Any

from app.request_metrics import REQUESTS_METRIC
from app.system_health import aware, percentile
from database.models import (
    EventLog,
    FlowRun,
    FunctionRun,
    JobRun,
    MailLog,
    MetricCounter,
    RequestLog,
    Secret,
    WebhookDelivery,
    WorkerHeartbeat,
)

ROW_CAP = 20000
WORKER_TTL = timedelta(seconds=45)


def _spec(label: str, unit: str, description: str, *, instant: bool = False) -> dict[str, Any]:
    return {"label": label, "unit": unit, "description": description, "instant": instant}


#: Every metric by name. ``instant`` ones describe right now and have no history to chart.
METRICS: dict[str, dict[str, Any]] = {
    "requests": _spec("Requests", "count", "Requests that reached the gateway."),
    "errors_5xx": _spec("Server errors", "count", "Responses with a 5xx status."),
    "errors_4xx": _spec("Client errors", "count", "Responses with a 4xx status."),
    "error_rate": _spec("Error rate", "%", "Share of requests that were 5xx."),
    "latency_avg": _spec("Latency, average", "ms", "Average time to answer a request."),
    "latency_p95": _spec(
        "Latency, 95th percentile", "ms", "Time under which 95% of requests were answered."
    ),
    "jobs_failed": _spec("Failed jobs", "count", "Background jobs that failed."),
    "job_failure_rate": _spec("Job failure rate", "%", "Share of finished jobs that failed."),
    "jobs_waiting": _spec(
        "Jobs waiting", "count", "Jobs queued or waiting to retry, right now.", instant=True
    ),
    "flow_runs": _spec("Flow runs", "count", "Flow executions."),
    "flow_failures": _spec("Failed flow runs", "count", "Flow executions that failed."),
    "function_failures": _spec(
        "Failed function runs", "count", "Function invocations that failed."
    ),
    "events": _spec("Events", "count", "Events published."),
    "mail_sent": _spec("Mail sent", "count", "Messages handed to the mail provider."),
    "mail_failed": _spec("Mail failed", "count", "Messages the provider refused."),
    "webhook_failures": _spec(
        "Failed webhook deliveries", "count", "Outbound deliveries that gave up."
    ),
    "workers_alive": _spec(
        "Workers alive", "count", "Queue workers that reported in recently.", instant=True
    ),
    "secrets_due": _spec(
        "Secrets due for rotation",
        "count",
        "Secrets older than their rotation reminder.",
        instant=True,
    ),
}


def aware_now() -> datetime:
    return datetime.now(UTC)


def _bucket(
    points: list[tuple[datetime | None, float]], start: datetime, period: timedelta, count: int
) -> list[float]:
    out = [0.0] * count
    for moment, amount in points:
        moment = aware(moment)
        if moment is None or moment < start:
            continue
        index = int((moment - start) / period)
        if 0 <= index < count:
            out[index] += amount
    return out


async def _points(
    env: str, metric: str, start: datetime
) -> list[tuple[datetime | None, float, float]]:
    """Raw (time, amount, weight) rows behind a metric. The weight is what an average is divided by."""
    if metric in ("requests", "errors_5xx", "errors_4xx", "error_rate", "latency_avg"):
        rows = await MetricCounter.filter(env=env, name=REQUESTS_METRIC, window__gte=start).values(
            "window", "tags", "value", "count"
        )
        out = []
        for row in rows:
            tags = row["tags"]
            if (
                metric == "requests"
                or metric == "errors_5xx"
                and "status=5xx" in tags
                or metric == "errors_4xx"
                and "status=4xx" in tags
            ):
                out.append((row["window"], row["count"], 0))
            elif metric == "error_rate":
                out.append(
                    (row["window"], row["count"] if "status=5xx" in tags else 0, row["count"])
                )
            elif metric == "latency_avg":
                out.append((row["window"], row["value"], row["count"]))
        return out
    if metric == "latency_p95":
        rows = (
            await RequestLog.filter(env=env, started_at__gte=start.isoformat())
            .order_by("-id")
            .limit(ROW_CAP)
            .values("started_at", "duration_ms")
        )
        out = []
        for row in rows:
            try:
                out.append((datetime.fromisoformat(row["started_at"]), row["duration_ms"], 0))
            except ValueError:
                continue
        return out
    if metric in ("jobs_failed", "job_failure_rate"):
        rows = (
            await JobRun.filter(env=env, created_at__gte=start, status__in=["failed", "succeeded"])
            .limit(ROW_CAP)
            .values("created_at", "status")
        )
        return [(r["created_at"], 1 if r["status"] == "failed" else 0, 1) for r in rows]
    if metric in ("flow_runs", "flow_failures"):
        rows = (
            await FlowRun.filter(env=env, created_at__gte=start)
            .limit(ROW_CAP)
            .values("created_at", "status")
        )
        return [
            (r["created_at"], 1 if metric == "flow_runs" or r["status"] == "failed" else 0, 0)
            for r in rows
        ]
    if metric == "function_failures":
        rows = (
            await FunctionRun.filter(env=env, created_at__gte=start, status="failed")
            .limit(ROW_CAP)
            .values("created_at")
        )
        return [(r["created_at"], 1, 0) for r in rows]
    if metric == "events":
        rows = (
            await EventLog.filter(env=env, created_at__gte=start)
            .limit(ROW_CAP)
            .values("created_at")
        )
        return [(r["created_at"], 1, 0) for r in rows]
    if metric in ("mail_sent", "mail_failed"):
        want = "sent" if metric == "mail_sent" else "failed"
        rows = (
            await MailLog.filter(env=env, created_at__gte=start, status=want)
            .limit(ROW_CAP)
            .values("created_at")
        )
        return [(r["created_at"], 1, 0) for r in rows]
    if metric == "webhook_failures":
        rows = (
            await WebhookDelivery.filter(
                endpoint__environment__name=env, created_at__gte=start, status="failed"
            )
            .limit(ROW_CAP)
            .values("created_at")
        )
        return [(r["created_at"], 1, 0) for r in rows]
    return []


async def instant(env: str, metric: str) -> float | None:
    now = aware_now()
    if metric == "jobs_waiting":
        return float(await JobRun.filter(env=env, status__in=["queued", "retrying"]).count())
    if metric == "workers_alive":
        rows = await WorkerHeartbeat.filter(kind="worker", status="running").values_list(
            "last_seen", flat=True
        )
        return float(sum(1 for seen in rows if seen and aware(seen) >= now - WORKER_TTL))
    if metric == "secrets_due":
        due = 0
        for secret in await Secret.filter(environment__name=env):
            since = secret.rotated_at or secret.created_at
            if (
                secret.rotate_every_days
                and since
                and (now - aware(since)).days >= secret.rotate_every_days
            ):
                due += 1
        return float(due)
    return None


def _combine(
    metric: str,
    points: list[tuple[datetime | None, float, float]],
    start: datetime,
    period: timedelta,
    count: int,
) -> list[float | None]:
    """One value per bucket, or None where a bucket has nothing to average."""
    amounts = _bucket([(t, a) for t, a, _ in points], start, period, count)
    weights = _bucket([(t, w) for t, _, w in points], start, period, count)
    if metric in ("error_rate", "latency_avg", "job_failure_rate"):
        return [
            round(a / w * (100 if metric != "latency_avg" else 1), 2) if w else None
            for a, w in zip(amounts, weights, strict=True)
        ]
    if metric == "latency_p95":
        groups: list[list[float]] = [[] for _ in range(count)]
        for moment, amount, _ in points:
            moment = aware(moment)
            if moment is None or moment < start:
                continue
            index = int((moment - start) / period)
            if 0 <= index < count:
                groups[index].append(amount)
        return [percentile(g, 0.95) for g in groups]
    return [round(a, 2) for a in amounts]


async def series(env: str, metric: str, minutes: int, period_minutes: int) -> dict[str, Any]:
    """A metric over the last ``minutes``, one value per ``period_minutes``."""
    if metric not in METRICS:
        raise KeyError(metric)
    period = timedelta(minutes=max(1, period_minutes))
    count = max(1, min(int(minutes / max(1, period_minutes)), 500))
    end = aware_now().replace(second=0, microsecond=0)
    start = end - period * count + timedelta(minutes=1)
    spec = METRICS[metric]
    if spec["instant"]:
        value = await instant(env, metric)
        return {
            "metric": metric,
            **spec,
            "points": [],
            "latest": value,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    values = _combine(metric, await _points(env, metric, start), start, period, count)
    points = [{"t": (start + period * i).isoformat(), "value": v} for i, v in enumerate(values)]
    present = [v for v in values if v is not None]
    return {
        "metric": metric,
        **spec,
        "period_minutes": period_minutes,
        "points": points,
        "latest": next((v for v in reversed(values) if v is not None), None),
        "max": max(present) if present else None,
        "min": min(present) if present else None,
        "average": round(sum(present) / len(present), 2) if present else None,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


async def value(env: str, metric: str, minutes: int) -> float | None:
    """One number for the last ``minutes``: the sum, or the average, rate or percentile the metric means."""
    if METRICS[metric]["instant"]:
        return await instant(env, metric)
    start = aware_now() - timedelta(minutes=minutes)
    points = await _points(env, metric, start)
    if not points:
        return (
            0.0
            if metric not in ("latency_avg", "latency_p95", "error_rate", "job_failure_rate")
            else None
        )
    amounts = sum(a for _, a, _ in points)
    weights = sum(w for _, _, w in points)
    if metric in ("error_rate", "job_failure_rate"):
        return round(amounts / weights * 100, 2) if weights else None
    if metric == "latency_avg":
        return round(amounts / weights, 2) if weights else None
    if metric == "latency_p95":
        return percentile([a for _, a, _ in points], 0.95)
    return round(amounts, 2)


# ── logs ─────────────────────────────────────────────────────────────────

FIELDS = ("level", "source", "status", "route", "user", "ip", "method", "request")


def parse_query(text: str) -> dict[str, Any]:
    """``level:error source:function:orders.pay "timed out" -healthcheck`` into filters and terms."""
    filters: dict[str, str] = {}
    terms: list[str] = []
    excluded: list[str] = []
    try:
        tokens = shlex.split(text or "")
    except ValueError:
        tokens = (text or "").split()
    for token in tokens:
        negative = token.startswith("-") and len(token) > 1
        body = token[1:] if negative else token
        name, sep, rest = body.partition(":")
        if sep and name.lower() in FIELDS and rest and not negative:
            filters[name.lower()] = rest.lower()
        elif negative:
            excluded.append(body.lower())
        else:
            terms.append(body.lower())
    return {"filters": filters, "terms": terms, "excluded": excluded}


def _level_of_status(status: int) -> str:
    return "error" if status >= 500 else "warning" if status >= 400 else "info"


def _matches(entry: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, wanted in query["filters"].items():
        have = str(entry.get(key, "")).lower()
        if key == "status" and wanted.endswith("xx"):
            if not have.startswith(wanted[0]):
                return False
        elif key in ("source", "route", "user") and ("*" in wanted):
            if not fnmatch.fnmatch(have, wanted):
                return False
        elif key in ("source", "route", "user"):
            if wanted not in have:
                return False
        elif have != wanted:
            return False
    haystack = " ".join(
        str(entry.get(k, ""))
        for k in ("message", "source", "route", "user", "ip", "path", "request")
    ).lower()
    return all(t in haystack for t in query["terms"]) and not any(
        x in haystack for x in query["excluded"]
    )


async def search_logs(
    env: str, minutes: int, text: str, limit: int, *, after: datetime | None = None
) -> dict[str, Any]:
    """Application logs and access logs from the last ``minutes`` that match a query, newest first."""
    query = parse_query(text)
    start = after or (aware_now() - timedelta(minutes=minutes))
    entries: list[dict[str, Any]] = []
    for run in (
        await FunctionRun.filter(env=env, created_at__gte=start).order_by("-created_at").limit(2000)
    ):
        for item in run.logs or []:
            entry = dict(item) if isinstance(item, dict) else {"message": str(item)}
            entries.append(
                {
                    "at": str(
                        entry.get("timestamp") or entry.get("at") or run.created_at.isoformat()
                    ),
                    "level": str(entry.get("level") or "info").lower(),
                    "message": str(entry.get("message", "")),
                    "source": f"function:{run.function}",
                    "request": run.request_id or "",
                }
            )
    for run in (
        await FlowRun.filter(env=env, created_at__gte=start).order_by("-created_at").limit(2000)
    ):
        for item in run.logs or []:
            entry = dict(item) if isinstance(item, dict) else {"message": str(item)}
            entries.append(
                {
                    "at": str(
                        entry.get("timestamp") or entry.get("at") or run.created_at.isoformat()
                    ),
                    "level": str(entry.get("level") or "info").lower(),
                    "message": str(entry.get("message", "")),
                    "source": f"flow:{run.flow}",
                    "request": run.request_id or "",
                }
            )
    for row in (
        await RequestLog.filter(env=env, started_at__gte=start.isoformat())
        .order_by("-id")
        .limit(5000)
    ):
        entries.append(
            {
                "at": row.started_at,
                "level": _level_of_status(row.status),
                "message": f"{row.method} {row.path} {row.status} {round(row.duration_ms)}ms"
                + (f" {row.error}" if row.error else ""),
                "source": "access",
                "status": row.status,
                "method": row.method,
                "route": row.route or "",
                "user": row.user or "",
                "ip": row.ip or "",
                "path": row.path,
                "request": row.request_id,
            }
        )
        for item in (row.notes or {}).get("logs", []):
            entry = dict(item) if isinstance(item, dict) else {"message": str(item)}
            entries.append(
                {
                    "at": str(entry.get("timestamp") or entry.get("at") or row.started_at),
                    "level": str(entry.get("level") or "info").lower(),
                    "message": str(entry.get("message", "")),
                    "source": "request",
                    "route": row.route or "",
                    "request": row.request_id,
                }
            )
    seen: set[tuple[str, str, str]] = set()
    kept = []
    for entry in sorted(entries, key=lambda e: e["at"], reverse=True):
        key = (entry["at"], entry["level"], entry["message"])
        if key in seen or not _matches(entry, query):
            continue
        seen.add(key)
        kept.append(entry)
    counts: dict[str, int] = {}
    for entry in kept:
        counts[entry["level"]] = counts.get(entry["level"], 0) + 1
    return {"data": kept[:limit], "matched": len(kept), "levels": counts, "query": query}
