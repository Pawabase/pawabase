"""One shape of health for every background service: how much was done, how much failed, what is waiting and for how long.

Each service already records its work (job runs, webhook deliveries, event logs, mail logs), so the figures are read from those records
rather than counted a second time. Every service answers the same fields, so Studio, the Advisor and alarms read them one way:

``total``, ``succeeded``, ``failed``, ``retrying``, ``failure_rate`` (percent of finished work), ``waiting`` (queued and not started),
``oldest_waiting_seconds`` (the age of the oldest waiting item, the clearest sign of a stuck queue).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.db.models import EventLog, JobRun, MailLog, WebhookDelivery

#: Job queues reported as services, by the name people know them by.
QUEUES = {
    "events": "Event processing",
    "flows": "Flows",
    "webhooks": "Webhook jobs",
    "mail": "Mail jobs",
}


def _shape(
    name: str,
    label: str,
    counts: dict[str, int],
    waiting: int,
    oldest: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    succeeded, failed = counts.get("succeeded", 0), counts.get("failed", 0)
    finished = succeeded + failed
    return {
        "service": name,
        "label": label,
        "total": sum(counts.values()),
        "succeeded": succeeded,
        "failed": failed,
        "retrying": counts.get("retrying", 0),
        "failure_rate": round(100 * failed / finished, 1) if finished else 0.0,
        "waiting": waiting,
        "oldest_waiting_seconds": round((now - oldest).total_seconds()) if oldest else 0,
    }


async def _status_counts(query: Any, mapping: dict[str, str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in await query.values_list("status", flat=True):
        key = (mapping or {}).get(status, status)
        counts[key] = counts.get(key, 0) + 1
    return counts


async def service_health(env: str, minutes: int = 60) -> list[dict[str, Any]]:
    """Every service's health over the last *minutes* in *env*."""
    now = datetime.now(UTC)
    since = now - timedelta(minutes=minutes)
    out = []
    for queue, label in QUEUES.items():
        recent = JobRun.filter(env=env, queue=queue, created_at__gte=since)
        counts = await _status_counts(recent, {"completed": "succeeded"})
        waiting_rows = JobRun.filter(env=env, queue=queue, status__in=["queued", "retrying"])
        oldest = await waiting_rows.order_by("created_at").first()
        out.append(
            _shape(
                queue,
                label,
                counts,
                await waiting_rows.count(),
                oldest.created_at if oldest else None,
                now,
            )
        )

    deliveries = WebhookDelivery.filter(endpoint__environment__name=env, created_at__gte=since)
    counts = await _status_counts(deliveries, {"delivered": "succeeded"})
    pending = WebhookDelivery.filter(
        endpoint__environment__name=env, status__in=["pending", "retrying"]
    )
    oldest = await pending.order_by("created_at").first()
    out.append(
        _shape(
            "webhook_deliveries",
            "Webhook deliveries",
            counts,
            await pending.count(),
            oldest.created_at if oldest else None,
            now,
        )
    )

    events = EventLog.filter(env=env, created_at__gte=since)
    total = await events.count()
    unrouted = EventLog.filter(env=env, routed_at=None)
    oldest = await unrouted.order_by("created_at").first()
    routed = await events.exclude(routed_at=None).count()
    out.append(
        _shape(
            "events",
            "Events",
            {"succeeded": routed, "pending": total - routed},
            await unrouted.count(),
            oldest.created_at if oldest else None,
            now,
        )
    )

    mail = MailLog.filter(env=env, created_at__gte=since)
    out.append(
        _shape("mail", "Mail", await _status_counts(mail, {"sent": "succeeded"}), 0, None, now)
    )
    return out
