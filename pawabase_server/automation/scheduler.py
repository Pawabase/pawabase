"""The scheduler: a task of the server process.

Runs Sillo's :class:`~sillo.work.scheduler.SchedulerManager`. Schedules live in
the database (Studio edits them, and flows can carry ``trigger.schedule``
nodes), so the process reconciles its registered jobs with the database every
few seconds: new schedules are added, changed ones re-registered, removed ones
dropped.

A firing decides *when*; the work itself is queued (a flow or function job) or
published (an event), so it survives a scheduler restart.

Any number of schedulers may run; one leads. Leadership is a database lease
(``core/leases.py``) renewed with the heartbeat: only the leader fires schedules
and runs platform housekeeping, and when it stops renewing, another takes over
within ``LEASE_SECONDS``. A schedule may skip a firing while its previous run is
still queued or running (``overlap="skip"``), read its cron in a time zone, and
catch up once on a slot that was missed while no scheduler led (``catch_up``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.work.scheduler import CronTrigger, IntervalTrigger, SchedulerManager

from pawabase_server.backup import snapshots
from pawabase_server.core import leases
from pawabase_server.db.models import Environment, FirewallEvent, JobRun, Schedule
from pawabase_server.observability import alarms, audit_log
from pawabase_server.observability import status as status_page
from pawabase_server.platform import Platform

HEARTBEAT_SECONDS = 10

logger = logging.getLogger("pawabase.scheduler")

RECONCILE_SECONDS = 15
LEASE = "scheduler"
LEASE_SECONDS = 30


class PlatformScheduler:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.manager = SchedulerManager()
        self._registered: dict[str, str] = {}
        self._last: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._beat_task: asyncio.Task | None = None
        self._name = f"scheduler:{socket.gethostname()}:{os.getpid()}"
        self._running = False
        #: Whether this process holds the scheduler lease and so fires schedules.
        self.leader = False

    # ── desired state ────────────────────────────────────────────────────

    async def desired(self) -> dict[str, dict[str, Any]]:
        """Every schedule that should be registered, by a key that changes with it."""
        wanted: dict[str, dict[str, Any]] = {}
        for schedule in await Schedule.filter(enabled=True).select_related("environment"):
            env = schedule.environment
            spec = {
                "kind": "schedule",
                "id": schedule.id,
                "env": env.name,
                "cron": schedule.cron,
                "every": schedule.interval_seconds,
                "target_type": schedule.target_type,
                "target": schedule.target,
                "payload": schedule.payload,
                "timezone": schedule.timezone,
                "overlap": schedule.overlap,
                "catch_up": schedule.catch_up,
                "name": f"{env.name}/{schedule.name}",
            }
            wanted[_key(spec)] = spec
        for env in await Environment.all():
            state = await self.platform.state(env.name)
            for flow in state.flows.values():
                if not flow.enabled:
                    continue
                for node in flow.definition.get("nodes", []):
                    data = node.get("data") or {}
                    if data.get("block") != "trigger.schedule":
                        continue
                    config = data.get("config") or {}
                    if not config.get("cron") and not config.get("every"):
                        continue
                    spec = {
                        "kind": "flow",
                        "env": env.name,
                        "cron": config.get("cron"),
                        "every": config.get("every"),
                        "flow": flow.name,
                        "node": node["id"],
                        "name": f"{env.name}/flow:{flow.name}",
                    }
                    wanted[_key(spec)] = spec
        return wanted

    async def reconcile(self) -> None:
        await self._expire_previews()
        wanted = await self.desired()
        for key in [k for k in self._registered if k not in wanted]:
            self.manager.remove(self._registered.pop(key))
        for key, spec in wanted.items():
            if key in self._registered:
                continue
            try:
                trigger = (
                    CronTrigger(spec["cron"], timezone=spec.get("timezone") or None)
                    if spec.get("cron")
                    else IntervalTrigger(float(spec["every"]))
                )
            except Exception as exc:
                logger.warning("schedule %s has an invalid trigger: %s", spec["name"], exc)
                continue
            job = self.manager.schedule(
                self.fire, trigger, name=spec["name"], kwargs={"spec": spec}, coalesce=True
            )
            self._registered[key] = job.id

    async def _expire_previews(self) -> None:
        """Remove expired preview environments and all their dependent rows."""
        now = datetime.now(UTC)
        expired = await Environment.filter(preview_expires_at__lte=now)
        for environment in expired:
            name = environment.name
            await environment.delete()
            self.platform.envs.forget(name)
            logger.info("expired deploy preview %s", name)

    # ── firing ───────────────────────────────────────────────────────────

    async def fire(self, spec: dict[str, Any], *, manual: bool = False) -> None:
        """Run one schedule now. A timed firing happens only on the leader; *manual* ("Run now" in Studio) runs wherever it is asked."""
        from pawabase_server.automation.jobs.flows import RunFlowJob
        from pawabase_server.automation.jobs.functions import RunFunctionJob

        if not self.leader and not manual:
            return  # another scheduler leads and fires this one
        platform = self.platform
        env = spec["env"]
        status = "queued"
        if not manual and spec.get("overlap") == "skip" and await self._still_running(spec):
            await self._record(spec, "skipped")
            logger.info("schedule %s skipped: its previous run is still going", spec["name"])
            return
        try:
            if spec["kind"] == "flow":
                await platform.dispatch(
                    RunFlowJob,
                    env=env,
                    target=spec["flow"],
                    source="schedule",
                    flow=spec["flow"],
                    input={"scheduled_at": _now()},
                    trigger="schedule",
                    entry=spec["node"],
                    auth={"authenticated": False, "kind": "system"},
                )
            elif spec["target_type"] == "flow":
                await platform.dispatch(
                    RunFlowJob,
                    env=env,
                    target=spec["target"],
                    source="schedule",
                    flow=spec["target"],
                    input=spec.get("payload") or {"scheduled_at": _now()},
                    trigger="schedule",
                    auth={"authenticated": False, "kind": "system"},
                )
            elif spec["target_type"] == "function":
                await platform.dispatch(
                    RunFunctionJob,
                    env=env,
                    target=spec["target"],
                    source="schedule",
                    function=spec["target"],
                    input=spec.get("payload") or {"scheduled_at": _now()},
                    trigger="schedule",
                    auth={"authenticated": False, "kind": "system"},
                )
            elif spec["target_type"] == "event":
                await platform.emit_event(
                    env, spec["target"], spec.get("payload"), actor="scheduler"
                )
                status = "emitted"
        except Exception:
            logger.exception("schedule %s failed to fire", spec["name"])
            status = "failed"
        await self._record(spec, status)

    async def _record(self, spec: dict[str, Any], status: str) -> None:
        if spec["kind"] != "schedule":
            return
        schedule = await Schedule.get_or_none(id=spec["id"])
        if schedule is not None:
            schedule.last_run_at = datetime.now(UTC)
            schedule.last_status = status
            schedule.run_count += 1
            await schedule.save(update_fields=["last_run_at", "last_status", "run_count"])

    async def _still_running(self, spec: dict[str, Any]) -> bool:
        """Whether the job a schedule queued last time is still waiting or running."""
        if spec.get("target_type") == "event":
            return False
        target = spec.get("flow") if spec["kind"] == "flow" else spec.get("target")
        return await JobRun.filter(
            env=spec["env"],
            source="schedule",
            target=target,
            status__in=["queued", "active", "retrying"],
        ).exists()

    async def catch_up(self) -> int:
        """Fire, once, each ``catch_up`` cron schedule whose last slot passed while no scheduler led. Returns how many fired."""
        fired = 0
        now = datetime.now(UTC)
        for schedule in await Schedule.filter(enabled=True, catch_up=True).select_related(
            "environment"
        ):
            if not schedule.cron or schedule.last_run_at is None:
                continue
            try:
                due = CronTrigger(schedule.cron, timezone=schedule.timezone or None).next_fire(
                    schedule.last_run_at.timestamp()
                )
            except Exception:
                logger.exception("schedule %s: cannot work out its missed slot", schedule.name)
                continue
            if due is None or due >= now.timestamp():
                continue
            spec = next(
                (s for s in (await self.desired()).values() if s.get("id") == schedule.id), None
            )
            if spec is not None:
                logger.info(
                    "schedule %s missed %s; catching up",
                    spec["name"],
                    datetime.fromtimestamp(due, UTC).isoformat(),
                )
                await self.fire(spec)
                fired += 1
        return fired

    async def lead(self) -> bool:
        """Take or renew the lease. On becoming leader, catch up on missed slots."""
        was = self.leader
        try:
            self.leader = await leases.acquire(LEASE, self._name, LEASE_SECONDS)
        except Exception:
            logger.exception("scheduler lease could not be renewed; not firing until it is")
            self.leader = False
        if self.leader and not was:
            logger.info("%s leads the scheduler", self._name)
            await self.catch_up()
        elif was and not self.leader:
            logger.warning("%s lost the scheduler lease", self._name)
        return self.leader

    # ── lifecycle ────────────────────────────────────────────────────────

    #: Platform housekeeping that runs on a timer: name, seconds between runs.
    TASKS = (("alarms", 60), ("status", 300), ("backups", 120), ("retention", 86400))

    async def tick(self) -> None:
        """Run whichever platform tasks are due. A failing task is logged and tried again next time. Only the leader runs them."""
        if not self.leader:
            return
        runners = {
            "alarms": lambda: alarms.evaluate_all(self.platform),
            "status": lambda: status_page.sample_local(self.platform),
            "backups": lambda: snapshots.run_due(self.platform),
            "retention": self._retention,
        }
        for name, every in self.TASKS:
            if time.monotonic() - self._last.get(name, -1e9) < every:
                continue
            self._last[name] = time.monotonic()
            try:
                await runners[name]()
            except Exception:
                logger.exception("platform task %s failed", name)

    async def _retention(self) -> None:
        """Purge what is kept for a limited time: audit entries and firewall events."""
        settings = self.platform.settings
        await audit_log.purge(settings.audit_retention_days)
        if settings.firewall_event_retention_days > 0:
            cutoff = datetime.now(UTC) - timedelta(days=settings.firewall_event_retention_days)
            await FirewallEvent.filter(created_at__lt=cutoff).delete()

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.reconcile()
            except Exception:
                logger.exception("schedule reconciliation failed")
            await self.tick()
            await asyncio.sleep(RECONCILE_SECONDS)

    async def _beat(self) -> None:
        """Report a heartbeat Studio shows under Workers, so a scheduler that is running is not shown as absent."""
        from pawabase_server.core.records import upsert
        from pawabase_server.db.models import WorkerHeartbeat

        started = datetime.now(UTC)
        while self._running:
            await self.lead()
            stats = self.manager.stats
            try:
                await upsert(
                    WorkerHeartbeat,
                    name=self._name,
                    defaults={
                        "kind": "scheduler",
                        "queues": [],
                        "status": "running" if self.leader else "standby",
                        "started_at": started,
                        "last_seen": datetime.now(UTC),
                        "processed": stats.runs_total,
                        "concurrency": stats.jobs_active,
                    },
                )
            except Exception:
                logger.debug("scheduler heartbeat skipped", exc_info=True)
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def start(self) -> None:
        self._running = True
        await self.lead()
        await self.reconcile()
        await self.manager.start()
        self._task = asyncio.create_task(self._loop(), name="pawabase-scheduler-reconcile")
        self._beat_task = asyncio.create_task(self._beat(), name="pawabase-scheduler-heartbeat")

    async def stop(self) -> None:
        from pawabase_server.db.models import WorkerHeartbeat

        self._running = False
        for task in (self._task, self._beat_task):
            if task is not None:
                task.cancel()
        if self.leader:
            try:
                await leases.release(LEASE, self._name)
            except Exception:
                logger.exception(
                    "scheduler lease not released; another scheduler takes over when it expires"
                )
            self.leader = False
        try:
            await WorkerHeartbeat.filter(name=self._name).update(status="stopped")
        except Exception:
            logger.debug("scheduler heartbeat not closed", exc_info=True)
        await self.manager.stop()

    def describe(self) -> list[dict[str, Any]]:
        jobs = []
        for job in self.manager.list():
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "status": getattr(job.status, "value", str(job.status)),
                    "next_run_time": job.next_run_time,
                    "runs": job._runs,
                    "errors": job._errors,
                }
            )
        return jobs


def _key(spec: dict[str, Any]) -> str:
    parts = [
        spec["kind"],
        spec["env"],
        str(spec.get("cron")),
        str(spec.get("every")),
    ]
    parts += [
        str(spec.get(k))
        for k in ("id", "target_type", "target", "flow", "node", "timezone", "overlap", "catch_up")
    ]
    parts.append(repr(spec.get("payload")))
    return "|".join(parts)


def _now() -> str:
    return datetime.now(UTC).isoformat()
