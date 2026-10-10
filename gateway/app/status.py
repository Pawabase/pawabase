"""The public status page: probing the services for it, and drawing what the API reports."""

from __future__ import annotations

import asyncio
import logging
import time
from html import escape
from typing import Any

import httpx

from pawabase_core.clients import ServiceClient, ServiceError

log = logging.getLogger("pawabase.gateway.status")

#: Which upstream answers for each probed component.
PROBES = {"gateway": None, "auth": "akountz", "realtime": "angula"}
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


class StatusProber:
    """Checks each service now and then and reports the answers to the API, for every environment with a page."""

    def __init__(
        self, api: ServiceClient, clients: dict[str, httpx.AsyncClient], every: float = 300
    ) -> None:
        self.api = api
        self.clients = clients
        self.every = every
        self.task: asyncio.Task[None] | None = None

    async def check(self, upstream: str | None) -> bool:
        # The gateway answering at all is the "gateway" probe; it also needs the API behind it.
        client = self.clients["api" if upstream is None else upstream]
        try:
            response = await client.get("/health", timeout=3)
            return response.status_code == 200
        except Exception:
            return False

    async def probe_once(self) -> int:
        try:
            envs = (await self.api.get("/internal/v1/status-enabled")).get("data", [])
        except Exception as exc:
            log.debug("status probe skipped: %s", exc)
            return 0
        if not envs:
            return 0
        results = {name: await self.check(up) for name, up in PROBES.items()}
        samples = [{"component": name, "ok": ok} for name, ok in results.items()]
        for env in envs:
            try:
                await self.api.post(f"/internal/v1/status/{env}/samples", json={"samples": samples})
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


async def fetch(api: ServiceClient, env: str | None) -> dict[str, Any] | None:
    path = "/internal/v1/status-default" if env is None else f"/internal/v1/status/{env}"
    try:
        return await api.get(path)
    except ServiceError as exc:
        if exc.status == 404:
            return None
        raise


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
