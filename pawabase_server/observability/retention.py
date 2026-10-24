"""How long each kind of log is kept.

A project's plan sets the days for each kind (a limit named like the setting: ``flow_run_retention_days``). Where the plan says nothing the
platform's setting of the same name applies, and ``0`` there keeps the log for ever. Once a day the leading scheduler calls :func:`sweep`,
which deletes what is older than its days: for each kind, the environments of projects with a limit by that limit, everything else by the
setting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.db.models import (
    AlarmEvent,
    AuditEntry,
    Environment,
    EventLog,
    FirewallEvent,
    FlowRun,
    JobRun,
    LoginEvent,
    MailLog,
    Project,
    RequestLog,
    WebhookDelivery,
)

logger = logging.getLogger("pawabase.retention")


@dataclass(frozen=True, slots=True)
class Kind:
    #: The plan limit and the setting, which share a name.
    name: str
    model: Any
    #: How the row names its environment, and when it was written (``text``: an ISO string, not a timestamp).
    env: str = "env"
    at: str = "created_at"
    text: bool = False


KINDS = (
    Kind("request_retention_days", RequestLog, at="started_at", text=True),
    Kind("audit_retention_days", AuditEntry),
    Kind("login_event_retention_days", LoginEvent),
    Kind("flow_run_retention_days", FlowRun),
    Kind("event_log_retention_days", EventLog, at="occurred_at", text=True),
    Kind("webhook_delivery_retention_days", WebhookDelivery, env="endpoint__environment__name"),
    Kind("job_run_retention_days", JobRun),
    Kind("mail_log_retention_days", MailLog),
    Kind("firewall_event_retention_days", FirewallEvent),
    Kind("alarm_event_retention_days", AlarmEvent),
)


def _cutoff(kind: Kind, days: int, now: datetime) -> Any:
    cutoff = now - timedelta(days=days)
    return cutoff.isoformat() if kind.text else cutoff


async def sweep(settings: Any, *, now: datetime | None = None) -> dict[str, int]:
    """Delete every log older than its retention. Returns how many rows went, by kind."""
    now = now or datetime.now(UTC)
    limits = {
        project.id: project.limits or {} for project in await Project.all().only("id", "limits")
    }
    project_of = dict(await Environment.all().values_list("name", "project_id"))
    removed: dict[str, int] = {}
    for kind in KINDS:
        by_days: dict[int, list[str]] = {}
        for env, project in project_of.items():
            days = int(limits.get(project, {}).get(kind.name) or 0)
            if days > 0:
                by_days.setdefault(days, []).append(env)
        count = 0
        try:
            for days, names in by_days.items():
                count += await kind.model.filter(
                    **{f"{kind.env}__in": names, f"{kind.at}__lt": _cutoff(kind, days, now)}
                ).delete()
            default = int(getattr(settings, kind.name, 0) or 0)
            if default > 0:
                scoped = [name for names in by_days.values() for name in names]
                rows = kind.model.filter(**{f"{kind.at}__lt": _cutoff(kind, default, now)})
                if scoped:
                    rows = rows.exclude(**{f"{kind.env}__in": scoped})
                count += await rows.delete()
        except Exception:
            logger.exception("could not sweep %s", kind.name)
        if count:
            removed[kind.name] = count
    return removed
