"""The public status page: what it shows, how component health is worked out, and the history behind it.

A component is one thing people depend on. Four are watched on their own: ``gateway``, ``api``, ``auth`` and
``realtime`` are sampled by the gateway every few minutes, ``workers`` by the scheduler. A ``manual`` component
has no probe: it is only ever degraded by an incident someone writes. Samples roll up into a daily uptime for the
last ninety days.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any

from pawabase_server.db.models import (
    Environment,
    StatusIncident,
    StatusPage,
    StatusSample,
    WorkerHeartbeat,
)

if TYPE_CHECKING:
    from pawabase_server.platform import Platform

log = logging.getLogger("pawabase.status")

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


# ── probing ─────────────────────────────────────────────────────────────────

Check = Callable[[], Awaitable[bool]]


async def page(env: str | None = None) -> dict[str, Any] | None:
    """The public page's data for *env*, or for the default environment; ``None`` when there is no page."""
    if env is None:
        default = await Environment.filter(is_default=True).first()
        return await build(default.name) if default else None
    return await build(env)


class StatusProber:
    """Runs each component's check now and then and records the answers for every environment with a status page.

    Args:
        checks: Component name to an async callable returning whether it is healthy.
        every: Seconds between rounds; ``0`` never starts the loop.
    """

    def __init__(self, checks: dict[str, Check], every: float = 300) -> None:
        self.checks = checks
        self.every = every
        self.task: asyncio.Task[None] | None = None

    async def check(self, name: str) -> bool:
        try:
            return bool(await self.checks[name]())
        except Exception:
            return False

    async def probe_once(self) -> int:
        envs = [row.env for row in await StatusPage.filter(enabled=True)]
        if not envs:
            return 0
        samples = [{"component": name, "ok": await self.check(name)} for name in self.checks]
        for env in envs:
            try:
                await record(env, samples)
            except Exception as exc:
                log.warning("could not record status for %s: %s", env, exc)
        return len(envs)

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            started = time.monotonic()
            try:
                await self.probe_once()
            except Exception:
                log.exception("status probe failed")
            await asyncio.sleep(max(1.0, self.every - (time.monotonic() - started)))

    def start(self) -> None:
        if self.task is None and self.every > 0:
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


# ── the page ────────────────────────────────────────────────────────────────

COLORS = {"operational": "#2e8a5e", "degraded": "#c98a1b", "outage": "#c4453a"}
LABELS = {
    "operational": "Operational",
    "degraded": "Degraded performance",
    "outage": "Outage",
}
HEADLINES = {
    "operational": "All systems operational",
    "degraded": "Some systems are degraded",
    "outage": "Some systems are down",
}


def _bar(days: list[dict[str, Any]]) -> str:
    cells = []
    for day in days:
        up = day["uptime"]
        color = (
            "#8a8a8a55"
            if up is None
            else COLORS["operational" if up >= 99.9 else "degraded" if up >= 98 else "outage"]
        )
        label = "No data" if up is None else f"{up}% uptime"
        cells.append(f'<i style="background:{color}" title="{escape(day["date"])} · {label}"></i>')
    return "".join(cells)


def _incident(item: dict[str, Any]) -> str:
    updates = "".join(
        f"<li><b>{escape(u['status'].title())}</b> — {escape(u['message'])}<time> {escape(u['at'][:16].replace('T', ' '))} UTC</time></li>"
        for u in reversed(item["updates"])
    )
    return f"<article><h3>{escape(item['title'])}</h3><ul>{updates}</ul></article>"


def _component(c: dict[str, Any]) -> str:
    name, status = escape(c["name"]), c["status"]
    note = "<small>" + escape(c["description"]) + "</small>" if c["description"] else ""
    uptime = "—" if c["uptime"] is None else str(c["uptime"]) + "% uptime"
    return (
        f'<section><header><b>{name}</b><span style="color:{COLORS[status]}">{LABELS[status]}</span></header>'
        f'{note}<div class="bar">{_bar(c["days"])}</div>'
        f"<footer><span>90 days ago</span><span>{uptime}</span><span>Today</span></footer></section>"
    )


def render(data: dict[str, Any]) -> str:
    overall = data["overall"]
    components = "".join(_component(c) for c in data["components"])
    active = "".join(_incident(i) for i in data["active"])
    past = (
        "".join(_incident(i) for i in data["recent"])
        or "<p class=m>No incidents in the last 90 days.</p>"
    )
    contact = (
        f'<a href="{escape(data["contact_url"], quote=True)}">Contact support</a>'
        if data["contact_url"]
        else ""
    )
    title = escape(data["title"])
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>:root{{color-scheme:light dark}}body{{margin:0;font:15px/1.55 system-ui,sans-serif}}
main{{max-width:44rem;margin:0 auto;padding:2.5rem 1.25rem 4rem}}h1{{margin:0 0 .25rem;font-size:1.5rem}}.m,small,time{{color:gray}}time{{margin-left:.5rem;font-size:.85em}}
.banner{{margin:1.5rem 0;padding:1rem 1.25rem;border-radius:12px;color:#fff;font-weight:600;background:{COLORS[overall]}}}
section{{border:1px solid rgba(127,127,127,.3);border-radius:12px;padding:1rem 1.25rem;margin:.75rem 0}}
header{{display:flex;justify-content:space-between}}footer{{display:flex;justify-content:space-between;font-size:.8em;color:gray}}
.bar{{display:flex;gap:2px;margin:.6rem 0 .3rem}}.bar i{{flex:1;height:2rem;border-radius:2px}}
h2{{margin-top:2.5rem;font-size:1.1rem}}article{{border-left:3px solid rgba(127,127,127,.4);padding-left:1rem;margin:1rem 0}}article h3{{margin:0 0 .3rem;font-size:1rem}}
ul{{list-style:none;padding:0;margin:0}}li{{margin:.3rem 0}}a{{color:inherit}}</style></head>
<body><main><h1>{title}</h1><p class="m">{escape(data["description"])}</p>
<div class="banner">{HEADLINES[overall]}</div>{active}{components}
<h2>Past incidents</h2>{past}<p class="m">{contact}</p></main></body></html>"""
