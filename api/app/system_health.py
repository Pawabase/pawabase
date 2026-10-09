"""One report on every moving part of an environment, for Studio's Observability.

Request traffic has its own endpoints (routes, errors, traces). This covers the
rest of the system: queues and workers, schedules, flows, events, outbound
webhooks and mail. Everything is read from what the platform already records;
nothing here writes. :func:`diagnose` turns the numbers into a short list of
things worth looking at, each pointing at the Studio section that explains it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.platform import PLATFORM_QUEUES
from app.request_metrics import REQUESTS_METRIC
from database.models import (
    EventLog,
    FlowRun,
    JobRun,
    MailLog,
    MetricCounter,
    Schedule,
    WebhookDelivery,
    WorkerHeartbeat,
)

#: Rows read per table. A busy environment over a long window is summarised
#: from its newest rows rather than loaded whole.
ROW_CAP = 20000
STEPS = (1, 2, 5, 10, 15, 30, 60, 180, 360, 720, 1440)
WORKER_TTL = timedelta(seconds=45)
STUCK_AFTER = timedelta(minutes=15)


def aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def step_for(minutes: int) -> int:
    """The bucket size, in minutes, that gives roughly 30–60 points."""
    return next((s for s in STEPS if minutes / s <= 60), STEPS[-1])


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 1)


def rate(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


class Grouped:
    """Repeated failures collapsed into one entry with a count.

    Rows arrive newest first, so the first row of a group is its latest.
    """

    def __init__(self, limit: int = 10) -> None:
        self.limit, self.items = limit, {}

    def add(self, key: tuple, item: dict[str, Any]) -> None:
        if key in self.items:
            self.items[key]["count"] += 1
        elif len(self.items) < self.limit:
            self.items[key] = {**item, "count": 1}

    def top(self) -> list[dict[str, Any]]:
        return list(self.items.values())


class Buckets:
    """Equal time buckets, each holding counters named up front."""

    def __init__(
        self, start: datetime, end: datetime, step: timedelta, keys: tuple[str, ...]
    ) -> None:
        self.start, self.step, self.keys = start, step, keys
        count = max(1, int((end - start) / step))
        self.rows = [
            {"t": (start + step * i).isoformat(), **dict.fromkeys(keys, 0)} for i in range(count)
        ]

    def add(self, moment: datetime | None, key: str, amount: int = 1) -> None:
        moment = aware(moment)
        if moment is None or moment < self.start:
            return
        index = int((moment - self.start) / self.step)
        if 0 <= index < len(self.rows):
            self.rows[index][key] += amount


async def system_report(platform: Any, env: str, minutes: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    step = timedelta(minutes=step_for(minutes))
    end = datetime.fromtimestamp(
        (now.timestamp() // step.total_seconds() + 1) * step.total_seconds(), UTC
    )
    start = end - step * max(1, int(timedelta(minutes=minutes) / step))
    window = {
        "minutes": minutes,
        "step_seconds": int(step.total_seconds()),
        "start": start.isoformat(),
        "end": end.isoformat(),
    }

    traffic = await _traffic(env, start, end, step)
    jobs = await _jobs(platform, env, start, end, step, now)
    workers = await _workers(platform)
    schedules = await _schedules(env, now)
    flows = await _flows(env, start, end, step)
    events = await _events(env, start, end, step)
    webhooks = await _webhooks(env, start, end, step)
    mail = await _mail(env, start, end, step)
    report = {
        "window": window,
        "traffic": traffic,
        "jobs": jobs,
        "workers": workers,
        "schedules": schedules,
        "flows": flows,
        "events": events,
        "webhooks": webhooks,
        "mail": mail,
    }
    report["problems"] = diagnose(report)
    return report


async def _traffic(env: str, start: datetime, end: datetime, step: timedelta) -> dict[str, Any]:
    """Gateway traffic on the same buckets as everything else, so charts line up."""
    rows = await MetricCounter.filter(env=env, name=REQUESTS_METRIC, window__gte=start).values(
        "window", "tags", "value", "count"
    )
    series = Buckets(start, end, step, ("requests", "errors", "client_errors"))
    latency = [0.0] * len(series.rows)
    totals = {"requests": 0, "errors": 0, "latency": 0.0}
    for row in rows:
        moment = aware(row["window"])
        index = int((moment - start) / step) if moment else -1
        if not 0 <= index < len(series.rows):
            continue
        series.add(moment, "requests", row["count"])
        if "status=5xx" in row["tags"]:
            series.add(moment, "errors", row["count"])
            totals["errors"] += row["count"]
        elif "status=4xx" in row["tags"]:
            series.add(moment, "client_errors", row["count"])
        latency[index] += row["value"]
        totals["requests"] += row["count"]
        totals["latency"] += row["value"]
    for index, bucket in enumerate(series.rows):
        bucket["latency_ms"] = (
            round(latency[index] / bucket["requests"], 1) if bucket["requests"] else None
        )
    return {
        "series": series.rows,
        "totals": {
            "requests": totals["requests"],
            "errors": totals["errors"],
            "error_rate": rate(totals["errors"], totals["requests"]) or 0.0,
            "avg_latency_ms": round(totals["latency"] / totals["requests"], 1)
            if totals["requests"]
            else 0.0,
        },
    }


async def _jobs(
    platform: Any, env: str, start: datetime, end: datetime, step: timedelta, now: datetime
) -> dict[str, Any]:
    rows = (
        await JobRun.filter(env=env, created_at__gte=start)
        .order_by("-created_at")
        .limit(ROW_CAP)
        .values(
            "id",
            "queue",
            "job",
            "status",
            "attempts",
            "error",
            "created_at",
            "available_at",
            "started_at",
            "finished_at",
            "duration_ms",
        )
    )
    series = Buckets(start, end, step, ("succeeded", "failed", "retrying", "queued"))
    by_queue: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "failed": 0, "durations": []}
    )
    by_job: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "failed": 0, "durations": []}
    )
    failures = Grouped()
    for row in rows:
        status = row["status"]
        key = status if status in ("succeeded", "failed", "retrying") else "queued"
        series.add(
            row["finished_at"] if status in ("succeeded", "failed") else row["created_at"], key
        )
        for group, name in ((by_queue, row["queue"]), (by_job, row["job"])):
            entry = group[name]
            entry["total"] += 1
            entry["failed"] += status == "failed"
            if row["duration_ms"] is not None:
                entry["durations"].append(row["duration_ms"])
        if status == "failed":
            error = (row["error"] or "")[:240]
            failures.add(
                (row["job"], row["queue"], error),
                {
                    "id": row["id"],
                    "job": row["job"],
                    "queue": row["queue"],
                    "attempts": row["attempts"],
                    "error": error,
                    "at": (aware(row["finished_at"] or row["created_at"]) or now).isoformat(),
                },
            )

    waiting = await JobRun.filter(env=env, status="queued").values_list(
        "available_at", "created_at"
    )
    oldest = max((now - (aware(a or c) or now) for a, c in waiting), default=timedelta())
    active = await JobRun.filter(env=env, status="active").values_list("id", "job", "started_at")
    stuck = [
        {"id": i, "job": j, "running_seconds": int((now - s).total_seconds())}
        for i, j, s in ((i, j, aware(s)) for i, j, s in active)
        if s and now - s > STUCK_AFTER
    ]

    queues = []
    names = sorted(set(by_queue) | set(PLATFORM_QUEUES))
    for name in names:
        entry = by_queue[name]
        depth = await platform.queue.size(name)
        queues.append(
            {
                "queue": name,
                "depth": depth,
                "total": entry["total"],
                "failed": entry["failed"],
                "success_rate": rate(entry["total"] - entry["failed"], entry["total"]),
                "avg_ms": round(sum(entry["durations"]) / len(entry["durations"]), 1)
                if entry["durations"]
                else None,
                "p95_ms": percentile(entry["durations"], 0.95),
            }
        )
    top_jobs = sorted(by_job.items(), key=lambda kv: -kv[1]["total"])[:10]
    totals = {"total": len(rows), "failed": sum(e["failed"] for e in by_queue.values())}
    return {
        "series": series.rows,
        "totals": {
            **totals,
            "success_rate": rate(totals["total"] - totals["failed"], totals["total"]),
        },
        "queues": queues,
        "by_job": [
            {
                "job": name,
                "total": e["total"],
                "failed": e["failed"],
                "avg_ms": round(sum(e["durations"]) / len(e["durations"]), 1)
                if e["durations"]
                else None,
                "p95_ms": percentile(e["durations"], 0.95),
                "max_ms": round(max(e["durations"]), 1) if e["durations"] else None,
            }
            for name, e in top_jobs
        ],
        "backlog": {
            "waiting": len(waiting),
            "oldest_seconds": int(oldest.total_seconds()),
            "stuck": stuck,
        },
        "recent_failures": failures.top(),
        "truncated": len(rows) >= ROW_CAP,
    }


async def _workers(platform: Any) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - WORKER_TTL
    rows = await WorkerHeartbeat.all()
    items = []
    for row in rows:
        seen = aware(row.last_seen)
        items.append(
            {
                "name": row.name,
                "kind": row.kind,
                "status": row.status,
                "processed": row.processed,
                "concurrency": row.concurrency,
                "last_seen": seen.isoformat() if seen else None,
                "alive": row.status == "running" and seen is not None and seen >= cutoff,
            }
        )
    inline = platform.app.state.get("inline_worker")
    if inline is not None:
        items.append(
            {
                "name": "inline (api process)",
                "kind": "worker",
                "status": "running",
                "alive": True,
                "processed": inline.worker._jobs_processed,
                "concurrency": inline.worker.options.concurrency,
                "last_seen": None,
            }
        )
    return {
        "items": items,
        "alive": sum(i["alive"] for i in items if i["kind"] == "worker"),
        "schedulers_alive": sum(i["alive"] for i in items if i["kind"] != "worker"),
    }


async def _schedules(env: str, now: datetime) -> list[dict[str, Any]]:
    rows = await Schedule.filter(environment__name=env).values(
        "name",
        "cron",
        "interval_seconds",
        "target_type",
        "target",
        "enabled",
        "last_run_at",
        "last_status",
        "run_count",
    )
    out = []
    for row in rows:
        last = aware(row["last_run_at"])
        interval = row["interval_seconds"]
        overdue = bool(
            row["enabled"]
            and interval
            and last
            and (now - last).total_seconds() > interval * 2 + 60
        )
        out.append({**row, "last_run_at": last.isoformat() if last else None, "overdue": overdue})
    return sorted(out, key=lambda r: r["name"])


async def _flows(env: str, start: datetime, end: datetime, step: timedelta) -> dict[str, Any]:
    rows = (
        await FlowRun.filter(env=env, created_at__gte=start)
        .order_by("-created_at")
        .limit(ROW_CAP)
        .values("id", "flow", "status", "error", "duration_ms", "trigger", "created_at")
    )
    series = Buckets(start, end, step, ("succeeded", "failed"))
    by_flow: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"runs": 0, "failed": 0, "durations": []}
    )
    failures = Grouped()
    for row in rows:
        failed = row["status"] == "failed"
        series.add(row["created_at"], "failed" if failed else "succeeded")
        entry = by_flow[row["flow"]]
        entry["runs"] += 1
        entry["failed"] += failed
        entry["durations"].append(row["duration_ms"] or 0)
        if failed:
            error = (row["error"] or "")[:240]
            failures.add(
                (row["flow"], error),
                {
                    "id": row["id"],
                    "flow": row["flow"],
                    "trigger": row["trigger"],
                    "error": error,
                    "at": aware(row["created_at"]).isoformat(),
                },
            )
    return {
        "series": series.rows,
        "totals": {"runs": len(rows), "failed": sum(e["failed"] for e in by_flow.values())},
        "by_flow": sorted(
            (
                {
                    "flow": name,
                    "runs": e["runs"],
                    "failed": e["failed"],
                    "success_rate": rate(e["runs"] - e["failed"], e["runs"]),
                    "avg_ms": round(sum(e["durations"]) / len(e["durations"]), 1),
                    "p95_ms": percentile(e["durations"], 0.95),
                }
                for name, e in by_flow.items()
            ),
            key=lambda r: -r["runs"],
        )[:15],
        "recent_failures": failures.top(),
    }


async def _events(env: str, start: datetime, end: datetime, step: timedelta) -> dict[str, Any]:
    rows = (
        await EventLog.filter(env=env, created_at__gte=start)
        .order_by("-created_at")
        .limit(ROW_CAP)
        .values("name", "source", "created_at")
    )
    series = Buckets(start, end, step, ("events",))
    by_name: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    for row in rows:
        series.add(row["created_at"], "events")
        by_name[row["name"]] += 1
        by_source[row["source"]] += 1
    return {
        "series": series.rows,
        "total": len(rows),
        "top": [
            {"name": n, "count": c} for n, c in sorted(by_name.items(), key=lambda kv: -kv[1])[:10]
        ],
        "sources": [
            {"name": n, "count": c} for n, c in sorted(by_source.items(), key=lambda kv: -kv[1])
        ],
    }


async def _webhooks(env: str, start: datetime, end: datetime, step: timedelta) -> dict[str, Any]:
    rows = (
        await WebhookDelivery.filter(endpoint__environment__name=env, created_at__gte=start)
        .order_by("-created_at")
        .limit(ROW_CAP)
        .values(
            "id",
            "status",
            "attempts",
            "response_status",
            "error",
            "duration_ms",
            "event",
            "created_at",
            "endpoint__name",
        )
    )
    series = Buckets(start, end, step, ("delivered", "failed", "pending"))
    by_endpoint: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "failed": 0, "durations": []}
    )
    failures = Grouped()
    for row in rows:
        status = row["status"]
        key = (
            "delivered"
            if status in ("delivered", "succeeded", "ok")
            else "failed"
            if status in ("failed", "dead")
            else "pending"
        )
        series.add(row["created_at"], key)
        entry = by_endpoint[row["endpoint__name"]]
        entry["total"] += 1
        entry["failed"] += key == "failed"
        if row["duration_ms"] is not None:
            entry["durations"].append(row["duration_ms"])
        if key == "failed":
            error = (row["error"] or "")[:240]
            failures.add(
                (row["endpoint__name"], row["event"], error),
                {
                    "id": row["id"],
                    "endpoint": row["endpoint__name"],
                    "event": row["event"],
                    "attempts": row["attempts"],
                    "status_code": row["response_status"],
                    "error": error,
                    "at": aware(row["created_at"]).isoformat(),
                },
            )
    return {
        "series": series.rows,
        "totals": {"total": len(rows), "failed": sum(e["failed"] for e in by_endpoint.values())},
        "by_endpoint": sorted(
            (
                {
                    "endpoint": n,
                    "total": e["total"],
                    "failed": e["failed"],
                    "success_rate": rate(e["total"] - e["failed"], e["total"]),
                    "avg_ms": round(sum(e["durations"]) / len(e["durations"]), 1)
                    if e["durations"]
                    else None,
                    "p95_ms": percentile(e["durations"], 0.95),
                }
                for n, e in by_endpoint.items()
            ),
            key=lambda r: -r["total"],
        ),
        "recent_failures": failures.top(),
    }


async def _mail(env: str, start: datetime, end: datetime, step: timedelta) -> dict[str, Any]:
    rows = (
        await MailLog.filter(env=env, created_at__gte=start)
        .order_by("-created_at")
        .limit(ROW_CAP)
        .values("id", "subject", "template", "status", "error", "source", "created_at")
    )
    series = Buckets(start, end, step, ("sent", "suppressed", "failed"))
    by_template: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "failed": 0})
    failures = Grouped()
    counts = {"sent": 0, "suppressed": 0, "failed": 0}
    for row in rows:
        status = row["status"] if row["status"] in counts else "sent"
        counts[status] += 1
        series.add(row["created_at"], status)
        entry = by_template[row["template"] or row["subject"] or "(untitled)"]
        entry["total"] += 1
        entry["failed"] += status == "failed"
        if status == "failed":
            error = (row["error"] or "")[:240]
            failures.add(
                (row["subject"], error),
                {
                    "id": row["id"],
                    "subject": row["subject"],
                    "error": error,
                    "at": aware(row["created_at"]).isoformat(),
                },
            )
    return {
        "series": series.rows,
        "totals": {"total": len(rows), **counts},
        "by_template": sorted(
            ({"template": n, **e} for n, e in by_template.items()), key=lambda r: -r["total"]
        )[:10],
        "recent_failures": failures.top(),
    }


def diagnose(report: dict[str, Any]) -> list[dict[str, str]]:
    """Plain-language findings, most severe first. Empty means nothing stands out."""
    found: list[dict[str, str]] = []

    def add(severity: str, area: str, message: str) -> None:
        found.append({"severity": severity, "area": area, "message": message})

    jobs, workers = report["jobs"], report["workers"]
    backlog = jobs["backlog"]
    if backlog["waiting"] and workers["alive"] == 0:
        add(
            "critical",
            "queues",
            f"{backlog['waiting']} jobs are waiting and no worker is running. Start one with `python -m app.worker`.",
        )
    elif backlog["oldest_seconds"] > 1800:
        add(
            "critical",
            "queues",
            f"The oldest waiting job has been queued for {backlog['oldest_seconds'] // 60} minutes.",
        )
    elif backlog["oldest_seconds"] > 300:
        add(
            "warning",
            "queues",
            f"The oldest waiting job has been queued for {backlog['oldest_seconds'] // 60} minutes.",
        )
    for item in backlog["stuck"][:3]:
        add(
            "warning",
            "queues",
            f"Job {item['job']} has been running for {item['running_seconds'] // 60} minutes.",
        )
    stale = [i["name"] for i in workers["items"] if not i["alive"] and i["status"] == "running"]
    if stale:
        shown = ", ".join(stale[:2]) + (f" and {len(stale) - 2} more" if len(stale) > 2 else "")
        add(
            "warning",
            "workers",
            f"{len(stale)} worker or scheduler process{'es' if len(stale) > 1 else ''} stopped reporting in ({shown}).",
        )
    totals = jobs["totals"]
    if totals["total"] >= 5 and totals["success_rate"] is not None:
        failed_rate = 100 - totals["success_rate"]
        if failed_rate >= 30:
            add("critical", "queues", f"{failed_rate:.0f}% of jobs in this window failed.")
        elif failed_rate >= 10:
            add("warning", "queues", f"{failed_rate:.0f}% of jobs in this window failed.")
    for schedule in report["schedules"]:
        if schedule["overdue"]:
            add(
                "warning",
                "schedules",
                f"Schedule {schedule['name']} has not run on time (last run {schedule['last_run_at'][:16].replace('T', ' ')} UTC).",
            )
        elif schedule["enabled"] and schedule["last_status"] == "failed":
            add("warning", "schedules", f"Schedule {schedule['name']} failed on its last run.")
    flows = report["flows"]["totals"]
    if flows["runs"] >= 5 and flows["failed"] / flows["runs"] >= 0.1:
        add("warning", "flows", f"{flows['failed']} of {flows['runs']} flow runs failed.")
    for endpoint in report["webhooks"]["by_endpoint"]:
        if endpoint["total"] >= 3 and endpoint["failed"] / endpoint["total"] >= 0.5:
            add(
                "warning",
                "webhooks",
                f"Webhook {endpoint['endpoint']} failed {endpoint['failed']} of {endpoint['total']} deliveries.",
            )
    mail = report["mail"]["totals"]
    if mail["failed"]:
        add("warning", "mail", f"{mail['failed']} emails failed to send.")
    order = {"critical": 0, "warning": 1}
    return sorted(found, key=lambda f: order[f["severity"]])
