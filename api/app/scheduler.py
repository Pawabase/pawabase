"""The scheduler process: ``python -m app.scheduler``.

Runs Sillo's :class:`~sillo.work.scheduler.SchedulerManager`. Schedules live in
the database (Studio edits them, and flows can carry ``trigger.schedule``
nodes), so the process reconciles its registered jobs with the database every
few seconds: new schedules are added, changed ones re-registered, removed ones
dropped.

A firing decides *when*; the work itself is queued (a flow or function job) or
published (an event), so it survives a scheduler restart.

Run exactly one scheduler. Sillo's scheduler keeps its schedule in memory, and
two of them would each fire every job.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import time
from datetime import UTC, datetime
from typing import Any

from sillo.work.scheduler import CronTrigger, IntervalTrigger, SchedulerManager

from app import alarms
from app import status as status_page
from app.platform import Platform
from database.models import Environment, Schedule
from pawabase_core.records import upsert

logger = logging.getLogger("pawabase.scheduler")

RECONCILE_SECONDS = 15


class PlatformScheduler:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.manager = SchedulerManager()
        self._registered: dict[str, str] = {}
        self._last: dict[str, float] = {}
        self._task: asyncio.Task | None = None
        self._running = False

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
                    CronTrigger(spec["cron"])
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

    async def fire(self, spec: dict[str, Any]) -> None:
        from app.jobs.flows import RunFlowJob
        from app.jobs.functions import RunFunctionJob

        platform = self.platform
        env = spec["env"]
        status = "queued"
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
                await platform.bus.emit(
                    spec["target"],
                    env=env,
                    payload=spec.get("payload"),
                    actor="scheduler",
                )
                status = "emitted"
        except Exception:
            logger.exception("schedule %s failed to fire", spec["name"])
            status = "failed"
        if spec["kind"] == "schedule":
            schedule = await Schedule.get_or_none(id=spec["id"])
            if schedule is not None:
                schedule.last_run_at = datetime.now(UTC)
                schedule.last_status = status
                schedule.run_count += 1
                await schedule.save(update_fields=["last_run_at", "last_status", "run_count"])

    # ── lifecycle ────────────────────────────────────────────────────────

    #: Platform housekeeping that runs on a timer: name, seconds between runs.
    TASKS = (("alarms", 60), ("status", 300))

    async def tick(self) -> None:
        """Run whichever platform tasks are due. A failing task is logged and tried again next time."""
        runners = {
            "alarms": lambda: alarms.evaluate_all(self.platform),
            "status": lambda: status_page.sample_local(self.platform),
        }
        for name, every in self.TASKS:
            if time.monotonic() - self._last.get(name, -1e9) < every:
                continue
            self._last[name] = time.monotonic()
            try:
                await runners[name]()
            except Exception:
                logger.exception("platform task %s failed", name)

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.reconcile()
            except Exception:
                logger.exception("schedule reconciliation failed")
            await self.tick()
            await asyncio.sleep(RECONCILE_SECONDS)

    async def start(self) -> None:
        self._running = True
        await self.reconcile()
        await self.manager.start()
        self._task = asyncio.create_task(self._loop(), name="pawabase-scheduler-reconcile")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
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
    parts += [str(spec.get(k)) for k in ("id", "target_type", "target", "flow", "node")]
    parts.append(repr(spec.get("payload")))
    return "|".join(parts)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def main(**overrides: Any) -> None:
    from sillo.record import DatabaseManager

    from app.config import ApiSettings
    from database.config import MODEL_MODULES, database_config
    from database.models import WorkerHeartbeat

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = ApiSettings(**overrides)
    database = DatabaseManager(database_config(settings)).register_models(*MODEL_MODULES)
    await database.init()
    platform = Platform(settings)
    await platform.start()
    scheduler = PlatformScheduler(platform)
    await scheduler.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    name = f"scheduler:{socket.gethostname()}:{os.getpid()}"
    started = datetime.now(UTC)
    logger.info("scheduler %s running", name)
    try:
        while not stop.is_set():
            stats = scheduler.manager.stats
            await upsert(
                WorkerHeartbeat,
                name=name,
                defaults={
                    "kind": "scheduler",
                    "queues": [],
                    "status": "running",
                    "started_at": started,
                    "last_seen": datetime.now(UTC),
                    "processed": stats.runs_total,
                    "concurrency": stats.jobs_active,
                },
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except TimeoutError:
                pass
    finally:
        await WorkerHeartbeat.filter(name=name).update(status="stopped")
        await scheduler.stop()
        await platform.stop()
        await database.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
