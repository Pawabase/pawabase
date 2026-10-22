"""The advisor: what the people who build on an environment should know about it, in their terms.

It answers "is my application healthy, and am I about to hit a limit?": requests failing, background work failing,
webhooks and mail not getting through, and usage close to a limit. It deliberately says nothing about Pawabase's own
processes (workers, the scheduler, queue plumbing); those belong to whoever operates the installation and are reported in
Observability's system view.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.db.models import (
    ApiKey,
    AuthUser,
    Environment,
    FunctionRun,
    Project,
    RequestLog,
)
from pawabase_server.observability.system_health import system_report

logger = logging.getLogger("pawabase.api.advisor")

#: How close to a limit is worth a mention, and when it is urgent.
NEAR = 0.8
AT = 1.0


def _finding(severity: str, area: str, title: str, message: str, page: str) -> dict[str, str]:
    return {"severity": severity, "area": area, "title": title, "message": message, "page": page}


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _limit_entry(
    key: str, label: str, used: int | None, limit: int | None, page: str
) -> dict[str, Any] | None:
    if not limit or used is None:
        return None
    return {
        "key": key,
        "label": label,
        "used": used,
        "limit": limit,
        "percent": round(100 * used / limit, 1),
        "page": page,
    }


async def _limits(platform: Any, env: str, environment: Environment) -> list[dict[str, Any]]:
    """Usage against every limit this environment runs under: its own variables, and its project's plan."""
    keys = await ApiKey.filter(environment=environment, revoked_at=None).count()
    users = await AuthUser.filter(env=env, deleted_at=None).count()
    found = [
        _limit_entry(
            "api_keys",
            "API keys",
            keys,
            platform.limit(
                env, "MAX_API_KEYS_PER_ENVIRONMENT", platform.settings.max_api_keys_per_environment
            ),
            "keys",
        ),
        _limit_entry(
            "users",
            "Users",
            users,
            platform.limit(env, "MAX_USERS", platform.settings.max_users),
            "users",
        ),
    ]
    project = (
        await Project.get_or_none(id=environment.project_id) if environment.project_id else None
    )
    plan = (project.limits or {}) if project else {}
    if plan:
        found.append(
            _limit_entry("plan_users", "Users (plan)", users, plan.get("max_active_users"), "users")
        )
        found.append(
            _limit_entry("plan_api_keys", "API keys (plan)", keys, plan.get("max_api_keys"), "keys")
        )
    # The public entry point keeps each project's request counters; it is absent when only the internal app runs.
    app = getattr(platform, "app", None)
    edge = app.state.get("edge") if app is not None else None
    if edge is not None and project is not None:
        try:
            usage = (await edge.env_limits.usage()).get(project.id, {})
        except Exception:  # noqa: BLE001 - counters are a nicety; the advisor still answers without them
            usage = {}
        requests = usage.get("requests") or {}
        for window, label in (("daily", "Requests today"), ("monthly", "Requests this month")):
            counter = requests.get(window) or {}
            found.append(
                _limit_entry(
                    f"requests_{window}", label, counter.get("used"), counter.get("limit"), "usage"
                )
            )
    return [entry for entry in found if entry]


async def _requests(env: str, since: datetime, window: str) -> list[dict[str, str]]:
    """The API the application's users call."""
    found: list[dict[str, str]] = []
    requests = RequestLog.filter(env=env, started_at__gte=since.isoformat())
    total = await requests.count()
    if total >= 20:
        failed = await requests.filter(status__gte=500).count()
        limited = await requests.filter(status=429).count()
        rate = _rate(failed, total)
        if rate >= 0.05:
            found.append(
                _finding(
                    "critical" if rate >= 0.2 else "warning",
                    "requests",
                    "Requests are failing",
                    f"{rate:.0%} of {total:,} requests in {window} ended in a server error.",
                    "observability#errors",
                )
            )
        if limited >= 10:
            found.append(
                _finding(
                    "warning",
                    "requests",
                    "Requests are being rate limited",
                    f"{limited:,} requests in {window} were refused with 429 Too Many Requests.",
                    "observability#requests",
                )
            )
    return found


async def _functions(env: str, since: datetime, window: str) -> list[dict[str, str]]:
    """Functions the application wrote."""
    runs = FunctionRun.filter(env=env, created_at__gte=since)
    calls = await runs.count()
    if calls < 5:
        return []
    errored = await runs.filter(status__in=["failed", "error"]).count()
    if _rate(errored, calls) < 0.1:
        return []
    return [
        _finding(
            "critical" if _rate(errored, calls) >= 0.3 else "warning",
            "functions",
            "Functions are failing",
            f"{errored:,} of {calls:,} function runs in {window} failed.",
            "functions",
        )
    ]


async def _system(platform: Any, env: str, minutes: int, window: str) -> list[dict[str, str]]:
    """Background work, flows, webhooks and mail, from the same report Observability uses."""
    found: list[dict[str, str]] = []
    report = await system_report(platform, env, minutes)
    jobs = report["jobs"]["totals"]
    if jobs["total"] >= 5 and jobs["success_rate"] is not None:
        failed_rate = (100 - jobs["success_rate"]) / 100
        if failed_rate >= 0.1:
            found.append(
                _finding(
                    "critical" if failed_rate >= 0.3 else "warning",
                    "jobs",
                    "Background jobs are failing",
                    f"{failed_rate:.0%} of {jobs['total']:,} jobs in {window} failed.",
                    "jobs",
                )
            )
    flows = report["flows"]["totals"]
    if flows["runs"] >= 5 and _rate(flows["failed"], flows["runs"]) >= 0.1:
        found.append(
            _finding(
                "warning",
                "flows",
                "Flows are failing",
                f"{flows['failed']:,} of {flows['runs']:,} flow runs in {window} failed.",
                "flows",
            )
        )
    for schedule in report["schedules"]:
        if schedule.get("enabled") and schedule.get("last_status") == "failed":
            found.append(
                _finding(
                    "warning",
                    "schedules",
                    "A schedule failed",
                    f"Schedule {schedule['name']} failed on its last run.",
                    "schedules",
                )
            )
    for endpoint in report["webhooks"]["by_endpoint"]:
        if endpoint["total"] >= 3 and _rate(endpoint["failed"], endpoint["total"]) >= 0.5:
            found.append(
                _finding(
                    "warning",
                    "webhooks",
                    "A webhook is not being delivered",
                    f"{endpoint['endpoint']} failed {endpoint['failed']:,} of {endpoint['total']:,} deliveries in {window}.",
                    "webhooks",
                )
            )
    mail = report["mail"]["totals"]
    if mail.get("failed"):
        found.append(
            _finding(
                "warning",
                "mail",
                "Email is not being sent",
                f"{mail['failed']:,} emails failed to send in {window}.",
                "mail",
            )
        )
    return found


def _limit_findings(limits: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Usage close to, or at, what the environment or its plan allows."""
    found: list[dict[str, str]] = []
    for entry in limits:
        share = entry["used"] / entry["limit"]
        if share >= AT:
            found.append(
                _finding(
                    "critical",
                    "limits",
                    f"{entry['label']} limit reached",
                    f"{entry['used']:,} of {entry['limit']:,} used. New ones will be refused until the limit is raised.",
                    entry["page"],
                )
            )
        elif share >= NEAR:
            found.append(
                _finding(
                    "warning",
                    "limits",
                    f"{entry['label']} close to the limit",
                    f"{entry['used']:,} of {entry['limit']:,} used ({share:.0%}).",
                    entry["page"],
                )
            )
    return found


async def advise(platform: Any, env: str, minutes: int = 60) -> dict[str, Any]:
    """Findings for one environment over the last *minutes*, most urgent first, and its usage against its limits.

    Each source is read on its own: one that cannot be read is named in ``unavailable`` and the rest still answer,
    so a broken table or an unreachable worker never turns the dashboard's advisor into an error.
    """
    environment = await Environment.get(name=env)
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    window = "the last hour" if minutes == 60 else f"the last {minutes} minutes"
    found: list[dict[str, str]] = []
    unavailable: list[str] = []

    for area, section in (
        ("requests", _requests(env, since, window)),
        ("functions", _functions(env, since, window)),
        ("system", _system(platform, env, minutes, window)),
    ):
        try:
            found.extend(await section)
        except Exception:  # noqa: BLE001 - see the docstring
            logger.exception("advisor: could not read %s for %s", area, env)
            unavailable.append(area)

    limits: list[dict[str, Any]] = []
    try:
        limits = await _limits(platform, env, environment)
        found.extend(_limit_findings(limits))
    except Exception:  # noqa: BLE001
        logger.exception("advisor: could not read limits for %s", env)
        unavailable.append("limits")

    order = {"critical": 0, "warning": 1}
    return {
        "window_minutes": minutes,
        "findings": sorted(found, key=lambda f: order[f["severity"]]),
        "limits": limits,
        "unavailable": unavailable,
    }
