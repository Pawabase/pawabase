"""The public status page: what it shows, how component health is worked out, and the history behind it.

A component is one thing people depend on. Four are watched on their own: ``gateway``, ``api``, ``auth`` and
``realtime`` are sampled by the gateway every few minutes, ``workers`` by the scheduler. A ``manual`` component
has no probe: it is only ever degraded by an incident someone writes. Samples roll up into a daily uptime for the
last ninety days.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from database.models import StatusIncident, StatusPage, StatusSample, WorkerHeartbeat

if TYPE_CHECKING:
    from app.platform import Platform

SOURCES = ("gateway", "api", "auth", "realtime", "workers", "manual")
DEFAULT_COMPONENTS = [
    {"name": "API", "source": "gateway", "description": "Requests to this project's API"},
    {"name": "Sign-in", "source": "auth", "description": "Accounts, sessions and sign-in"},
    {"name": "Realtime", "source": "realtime", "description": "Live channels"},
    {"name": "Background jobs", "source": "workers", "description": "Queues, schedules and flows"},
]
HISTORY_DAYS = 90
IMPACT_STATUS = {
    "none": "operational",
    "minor": "degraded",
    "major": "outage",
    "critical": "outage",
}
ORDER = {"operational": 0, "degraded": 1, "outage": 2}
WORKER_TTL = timedelta(seconds=45)


def aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def worse(a: str, b: str) -> str:
    return a if ORDER[a] >= ORDER[b] else b


async def workers_ok() -> bool:
    now = datetime.now(UTC)
    rows = await WorkerHeartbeat.filter(kind="worker", status="running").values_list(
        "last_seen", flat=True
    )
    return any(seen and aware(seen) >= now - WORKER_TTL for seen in rows)


async def sample_local(platform: Platform) -> None:
    """The API's own part of the history: workers, for every environment with a status page."""
    ok = await workers_ok()
    now = datetime.now(UTC)
    for page in await StatusPage.filter(enabled=True):
        sources = {c["source"] for c in (page.components or [])}
        if "workers" in sources:
            await StatusSample.create(env=page.env, component="workers", at=now, ok=ok)
        if "api" in sources:
            await StatusSample.create(env=page.env, component="api", at=now, ok=True)
    cutoff = now - timedelta(days=HISTORY_DAYS + 5)
    await StatusSample.filter(at__lt=cutoff).delete()


async def record(env: str, samples: list[dict[str, Any]]) -> int:
    now = datetime.now(UTC)
    kept = 0
    for item in samples[:20]:
        component = str(item.get("component", ""))[:64]
        if component in SOURCES and component != "manual":
            await StatusSample.create(env=env, component=component, at=now, ok=bool(item.get("ok")))
            kept += 1
    return kept


def _current(recent: list[bool]) -> str:
    """Operational when the last probes passed, degraded when some failed, an outage when the latest did."""
    if not recent:
        return "operational"
    if not recent[0]:
        return "outage" if not any(recent[1:3]) or len(recent) < 2 else "degraded"
    return "operational" if all(recent) else "degraded"


async def build(env: str, *, include_disabled: bool = False) -> dict[str, Any] | None:
    page = await StatusPage.get_or_none(env=env)
    if page is None or not (page.enabled or include_disabled):
        return None
    now = datetime.now(UTC)
    since = now - timedelta(days=HISTORY_DAYS)
    samples = await StatusSample.filter(env=env, at__gte=since).order_by("-at").limit(200_000)
    by_source: dict[str, list[StatusSample]] = defaultdict(list)
    for sample in samples:
        by_source[sample.component].append(sample)
    incidents = (
        await StatusIncident.filter(env=env, created_at__gte=now - timedelta(days=HISTORY_DAYS))
        .order_by("-id")
        .limit(200)
    )
    active = [i for i in incidents if i.status != "resolved"]

    components = []
    for item in page.components or []:
        source = item.get("source", "manual")
        rows = by_source.get(source, []) if source != "manual" else []
        status = _current([row.ok for row in rows[:3]])
        for incident in active:
            named = incident.components or []
            if item["name"] in named or not named:
                status = worse(status, IMPACT_STATUS.get(incident.impact, "degraded"))
        days = []
        today = now.date()
        buckets: dict[Any, list[bool]] = defaultdict(list)
        for row in rows:
            buckets[aware(row.at).date()].append(row.ok)
        down_days = defaultdict(int)
        for incident in incidents:
            named = incident.components or []
            if (item["name"] in named or not named) and incident.impact in ("major", "critical"):
                day = aware(incident.created_at).date()
                down_days[day] += 1
        for offset in range(HISTORY_DAYS - 1, -1, -1):
            day = today - timedelta(days=offset)
            oks = buckets.get(day)
            uptime = round(sum(oks) / len(oks) * 100, 2) if oks else None
            if down_days.get(day) and (uptime is None or uptime > 99):
                uptime = 99.0 if uptime is None else min(uptime, 99.0)
            days.append({"date": day.isoformat(), "uptime": uptime})
        measured = [d["uptime"] for d in days if d["uptime"] is not None]
        components.append(
            {
                "name": item["name"],
                "description": item.get("description", ""),
                "source": source,
                "status": status,
                "uptime": round(sum(measured) / len(measured), 2) if measured else None,
                "days": days,
            }
        )
    overall = "operational"
    for component in components:
        overall = worse(overall, component["status"])

    def view(incident: StatusIncident) -> dict[str, Any]:
        return {
            "id": incident.id,
            "title": incident.title,
            "status": incident.status,
            "impact": incident.impact,
            "components": incident.components or [],
            "updates": incident.updates or [],
            "started_at": incident.created_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        }

    return {
        "env": env,
        "enabled": page.enabled,
        "title": page.title,
        "description": page.description,
        "contact_url": page.contact_url,
        "overall": overall,
        "components": components,
        "active": [view(i) for i in active],
        "recent": [view(i) for i in incidents if i.status == "resolved"][:20],
        "generated_at": now.isoformat(),
    }
