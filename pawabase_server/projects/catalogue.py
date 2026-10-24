"""Every limit a plan can set on a project, defined once.

The project manager's allow-list, the usage page's rows and labels, Pawabase Cloud's plan allow-list and the plan tables the Cloud
dashboard draws are all read from :data:`LIMITS`. Add a limit here and it is accepted, reported and shown everywhere; the tests fail
if a place keeps its own copy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Unit = Literal["count", "bytes", "seconds", "days"]
#: ``total``: how many exist at once; ``month``: used up over a month; ``item``: bounds one thing; ``rate``: a rate-limit setting;
#: ``retention``: days a kind of log is kept (``0``: the platform default, see ``observability/retention.py``).
Kind = Literal["total", "month", "item", "rate", "retention"]


@dataclass(frozen=True, slots=True)
class Limit:
    key: str
    label: str
    group: str
    unit: Unit = "count"
    kind: Kind = "total"
    #: Shown on the usage page (the rate settings have the gateway's own card).
    reported: bool = True


LIMITS: tuple[Limit, ...] = (
    Limit("monthly_request_limit", "API requests", "Traffic", kind="month"),
    Limit("monthly_bandwidth_bytes", "Bandwidth", "Traffic", "bytes", "month"),
    Limit("max_active_connections", "Realtime connections", "Traffic"),
    Limit("max_realtime_messages_per_month", "Realtime messages", "Traffic", kind="month"),
    Limit("max_realtime_channels", "Realtime channels", "Traffic"),
    Limit("rate_limit", "Requests per rate window", "Traffic", kind="rate", reported=False),
    Limit("rate_window", "Rate window", "Traffic", "seconds", "rate", reported=False),
    Limit("daily_request_limit", "API requests per day", "Traffic", kind="rate", reported=False),
    Limit("max_database_bytes", "PostgreSQL storage", "Data", "bytes"),
    Limit("max_storage_bytes", "Object storage", "Data", "bytes"),
    Limit("max_buckets", "Storage buckets", "Data"),
    Limit("max_upload_bytes", "Largest upload", "Data", "bytes", "item"),
    Limit("max_backups", "Backups", "Data"),
    Limit("max_users_per_month", "Monthly active users", "People", kind="month"),
    Limit("max_active_users", "Users", "People"),
    Limit("max_api_keys", "Environment API keys", "People"),
    Limit("max_user_api_keys_per_user", "API keys per user", "People", kind="item"),
    Limit("max_auth_sessions_per_user", "Sessions per user", "People", kind="item"),
    Limit("max_mfa_factors_per_user", "Second factors per user", "People", kind="item"),
    Limit("max_auth_providers", "Sign-in providers", "People", kind="item"),
    Limit("max_flow_runs_per_month", "Flow runs", "Automation", kind="month"),
    Limit("max_scheduled_jobs", "Scheduled tasks", "Automation"),
    Limit("max_emails_per_month", "Emails sent", "Automation", kind="month"),
    Limit("max_webhooks", "Outbound webhooks", "Automation"),
    Limit("max_inbound_hooks", "Inbound webhooks", "Automation"),
    Limit("max_queued_jobs", "Queued jobs", "Automation", reported=False),
    Limit("max_environments", "Environments", "Project"),
    Limit(
        "request_retention_days", "Request log history", "Logs", "days", "retention", reported=False
    ),
    Limit("audit_retention_days", "Audit log history", "Logs", "days", "retention", reported=False),
    Limit(
        "login_event_retention_days", "Sign-in history", "Logs", "days", "retention", reported=False
    ),
    Limit(
        "flow_run_retention_days", "Flow run history", "Logs", "days", "retention", reported=False
    ),
    Limit("event_log_retention_days", "Event history", "Logs", "days", "retention", reported=False),
    Limit(
        "webhook_delivery_retention_days",
        "Webhook delivery history",
        "Logs",
        "days",
        "retention",
        reported=False,
    ),
    Limit(
        "job_run_retention_days",
        "Background job history",
        "Logs",
        "days",
        "retention",
        reported=False,
    ),
    Limit("mail_log_retention_days", "Email history", "Logs", "days", "retention", reported=False),
    Limit(
        "firewall_event_retention_days",
        "Firewall event history",
        "Logs",
        "days",
        "retention",
        reported=False,
    ),
    Limit(
        "alarm_event_retention_days", "Alarm history", "Logs", "days", "retention", reported=False
    ),
)

KEYS = frozenset(limit.key for limit in LIMITS)
BY_KEY = {limit.key: limit for limit in LIMITS}


def catalogue() -> list[dict[str, object]]:
    """The limits as plain data, for a dashboard to draw."""
    return [asdict(limit) for limit in LIMITS]
