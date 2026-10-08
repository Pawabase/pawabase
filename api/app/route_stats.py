"""Which routes are failing, slow or busy, and why.

Reads the persisted request history (:class:`~database.models.RequestLog`) and
groups it by route. The grouping itself is pure (:func:`aggregate_routes`,
:func:`route_breakdown`, :func:`error_groups`), so it is tested without a
database; the ``*_for`` coroutines only fetch a window of rows and call it.

Latencies are exact percentiles over the fetched window, not estimates. The
window is capped at :data:`MAX_ROWS` of the newest requests; when a busy
environment exceeds it the answer says so (``truncated``), rather than
silently reporting on a subset.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from database.models import RequestLog

MAX_ROWS = 50_000
NO_ROUTE = "(no route)"
SORTS = ("errors", "error_rate", "slow", "traffic", "time")
FIELDS = (
    "request_id", "method", "path", "route", "status", "duration_ms",
    "started_at", "role", "user", "error", "notes",
)  # fmt: skip

_NOISE = re.compile(r"\b[0-9a-f]{8,}(?:-[0-9a-f]{4,})*\b|\d+")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def percentile(sorted_values: list[float], p: float) -> float:
    """The *p* quantile (0-1) of an already sorted list; ``0.0`` when empty."""
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(p * (len(sorted_values) - 1))))
    return sorted_values[index]


def signature(message: str | None) -> str:
    """An error message with its variable parts removed, so one bug is one group."""
    if not message:
        return "(no message)"
    return _NOISE.sub("#", _QUOTED.sub("'…'", message)).strip()[:200] or "(no message)"


def _round(value: float) -> float:
    return round(value, 1)


def _latency(durations: list[float]) -> dict[str, float]:
    ordered = sorted(durations)
    return {
        "avg_ms": _round(sum(ordered) / len(ordered)) if ordered else 0.0,
        "p50_ms": _round(percentile(ordered, 0.5)),
        "p95_ms": _round(percentile(ordered, 0.95)),
        "p99_ms": _round(percentile(ordered, 0.99)),
        "max_ms": _round(ordered[-1]) if ordered else 0.0,
    }


def _route_of(row: dict[str, Any]) -> str:
    return row.get("route") or NO_ROUTE


def _spans(row: dict[str, Any]) -> list[dict[str, Any]]:
    notes = row.get("notes")
    spans = notes.get("spans") if isinstance(notes, dict) else None
    return spans if isinstance(spans, list) else []


def _by_kind(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where requests spend their time, by span kind (db, cache, http, ...).

    Nested spans are not double counted: only top-level spans (no parent) add
    to a kind's time, while the count includes every span.
    """
    time_ms: dict[str, float] = defaultdict(float)
    count: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for row in rows:
        for item in _spans(row):
            kind = str(item.get("kind") or "other")
            count[kind] += 1
            if item.get("status") == "error":
                errors[kind] += 1
            if item.get("parent") is None:
                time_ms[kind] += float(item.get("duration_ms") or 0)
    total = sum(time_ms.values()) or 1.0
    return sorted(
        (
            {
                "kind": kind,
                "calls": count[kind],
                "errors": errors[kind],
                "total_ms": _round(time_ms[kind]),
                "avg_ms": _round(time_ms[kind] / count[kind]) if count[kind] else 0.0,
                "share": round(time_ms[kind] / total, 3),
            }
            for kind in count
        ),
        key=lambda item: -item["total_ms"],
    )


def aggregate_routes(
    rows: list[dict[str, Any]],
    *,
    window_seconds: float,
    sort: str = "errors",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """One entry per ``METHOD route``: volume, failures, latency and top errors.

    ``server_errors`` are 5xx (the platform or a handler failed); ``client_errors``
    are 4xx (the caller was refused or wrong). ``error_rate`` is the 5xx share, the
    number to alert on; ``failure_rate`` adds the 4xx.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["method"], _route_of(row))].append(row)
    out = []
    for (method, route), items in groups.items():
        total = len(items)
        server = [r for r in items if r["status"] >= 500]
        client = [r for r in items if 400 <= r["status"] < 500]
        durations = [float(r["duration_ms"] or 0) for r in items]
        failing = server or client
        latest_failure = max(failing, key=lambda r: r["started_at"]) if failing else None
        problems = Counter(
            signature(r.get("error")) if r.get("error") else f"HTTP {r['status']}"
            for r in server + client
        )
        out.append(
            {
                "method": method,
                "route": route,
                "requests": total,
                "per_minute": round(total / max(window_seconds / 60, 1e-9), 2),
                "server_errors": len(server),
                "client_errors": len(client),
                "error_rate": round(len(server) / total * 100, 2),
                "failure_rate": round((len(server) + len(client)) / total * 100, 2),
                "total_ms": _round(sum(durations)),
                **_latency(durations),
                "last_failure_at": latest_failure["started_at"] if latest_failure else None,
                "last_failure_status": latest_failure["status"] if latest_failure else None,
                "last_failure_request_id": latest_failure["request_id"] if latest_failure else None,
                "top_problems": [
                    {"problem": text, "count": n} for text, n in problems.most_common(3)
                ],
                "callers": len({r.get("user") or r.get("role") or "anonymous" for r in items}),
            }
        )
    keys = {
        "errors": lambda r: (-r["server_errors"], -r["error_rate"], -r["requests"]),
        "error_rate": lambda r: (-r["error_rate"], -r["server_errors"], -r["requests"]),
        "slow": lambda r: (-r["p95_ms"], -r["requests"]),
        "traffic": lambda r: (-r["requests"], -r["server_errors"]),
        "time": lambda r: (-r["total_ms"], -r["requests"]),
    }
    out.sort(key=keys.get(sort, keys["errors"]))
    return out[: max(1, limit)]


def route_breakdown(
    rows: list[dict[str, Any]],
    *,
    since: datetime,
    until: datetime,
    buckets: int = 48,
) -> dict[str, Any]:
    """Everything about one route's rows: a time series, statuses, errors, slowest, time by kind."""
    span_seconds = max((until - since).total_seconds(), 1.0)
    step = max(span_seconds / buckets, 60.0)
    series: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        moment = datetime.fromisoformat(row["started_at"])
        series[int((moment - since).total_seconds() // step)].append(row)
    timeline = []
    for index in range(int(span_seconds // step) + 1):
        items = series.get(index, [])
        durations = sorted(float(r["duration_ms"] or 0) for r in items)
        timeline.append(
            {
                "at": (since + timedelta(seconds=index * step)).isoformat(),
                "requests": len(items),
                "server_errors": sum(1 for r in items if r["status"] >= 500),
                "client_errors": sum(1 for r in items if 400 <= r["status"] < 500),
                "p95_ms": _round(percentile(durations, 0.95)),
            }
        )
    statuses = Counter(r["status"] for r in rows)
    callers = Counter(r.get("user") or r.get("role") or "anonymous" for r in rows)
    failed = [r for r in rows if r["status"] >= 400]

    def brief(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: row.get(key)
            for key in (
                "request_id",
                "status",
                "duration_ms",
                "started_at",
                "path",
                "user",
                "role",
                "error",
            )
        }

    return {
        "summary": {
            **_latency([float(r["duration_ms"] or 0) for r in rows]),
            "requests": len(rows),
        },
        "timeline": timeline,
        "step_seconds": step,
        "statuses": [{"status": s, "count": n} for s, n in sorted(statuses.items())],
        "callers": [{"caller": c, "count": n} for c, n in callers.most_common(8)],
        "errors": error_groups(failed),
        "slowest": [
            brief(r) for r in sorted(rows, key=lambda r: -float(r["duration_ms"] or 0))[:10]
        ],
        "recent_failures": [
            brief(r) for r in sorted(failed, key=lambda r: r["started_at"], reverse=True)[:10]
        ],
        "time_by_kind": _by_kind(rows),
    }


def error_groups(rows: list[dict[str, Any]], *, limit: int = 50) -> list[dict[str, Any]]:
    """Failures (4xx and 5xx) grouped by what went wrong and where.

    A group is one error signature (numbers, ids and quoted values removed) on
    one route and status, so ``order 17 not found`` and ``order 903 not found``
    are one problem with a count.
    """
    groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["status"] < 400:
            continue
        text = signature(row.get("error")) if row.get("error") else f"HTTP {row['status']}"
        groups[(text, row["method"], _route_of(row), row["status"])].append(row)
    out = []
    for (text, method, route, status), items in groups.items():
        items.sort(key=lambda r: r["started_at"])
        out.append(
            {
                "problem": text,
                "method": method,
                "route": route,
                "status": status,
                "count": len(items),
                "users": len({r.get("user") for r in items if r.get("user")}),
                "first_seen": items[0]["started_at"],
                "last_seen": items[-1]["started_at"],
                "sample_request_id": items[-1]["request_id"],
                "sample_error": items[-1].get("error"),
            }
        )
    out.sort(key=lambda g: (-(g["status"] >= 500), -g["count"], g["last_seen"]))
    return out[:limit]


# ── fetching ─────────────────────────────────────────────────────────────


def window(minutes: int) -> tuple[datetime, datetime]:
    until = datetime.now(UTC)
    return until - timedelta(minutes=max(1, minutes)), until


async def fetch(env: str, since: datetime, **filters: Any) -> tuple[list[dict[str, Any]], bool]:
    """The newest requests since *since* as dicts, and whether the cap cut the window short."""
    rows = (
        await RequestLog.filter(env=env, started_at__gte=since.isoformat(), **filters)
        .order_by("-id")
        .limit(MAX_ROWS + 1)
        .values(*FIELDS)
    )
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


async def routes_for(
    env: str, *, minutes: int = 60, sort: str = "errors", limit: int = 50
) -> dict[str, Any]:
    since, until = window(minutes)
    rows, truncated = await fetch(env, since)
    return {
        "minutes": minutes,
        "sort": sort if sort in SORTS else "errors",
        "requests": len(rows),
        "truncated": truncated,
        "routes": aggregate_routes(
            rows, window_seconds=(until - since).total_seconds(), sort=sort, limit=limit
        ),
    }


async def route_detail_for(
    env: str, method: str, route: str, *, minutes: int = 60
) -> dict[str, Any]:
    since, until = window(minutes)
    filters: dict[str, Any] = {"method": method.upper()}
    if route == NO_ROUTE:
        filters["route__isnull"] = True
    else:
        filters["route"] = route
    rows, truncated = await fetch(env, since, **filters)
    return {
        "method": method.upper(),
        "route": route,
        "minutes": minutes,
        "truncated": truncated,
        **route_breakdown(rows, since=since, until=until),
    }


async def errors_for(env: str, *, minutes: int = 60, limit: int = 50) -> dict[str, Any]:
    since, _ = window(minutes)
    rows, truncated = await fetch(env, since, status__gte=400)
    return {
        "minutes": minutes,
        "truncated": truncated,
        "failures": len(rows),
        "groups": error_groups(rows, limit=limit),
    }
