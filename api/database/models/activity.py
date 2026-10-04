"""What happened: runs, events, deliveries, jobs and metrics, for inspection."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField
from pawabase_core.records import ulid_pk


class RequestLog(Model):
    """One persisted data-plane request and its structured trace notes."""

    id = ulid_pk()
    request_id = fields.CharField(max_length=64, db_index=True)
    service = fields.CharField(max_length=32, db_index=True)
    env = fields.CharField(max_length=63, db_index=True)
    method = fields.CharField(max_length=16)
    path = fields.TextField()
    route = fields.CharField(max_length=512, null=True)
    status = fields.IntField(db_index=True)
    duration_ms = fields.FloatField(default=0)
    started_at = fields.CharField(max_length=40, db_index=True)
    role = fields.CharField(max_length=64, null=True)
    user = fields.CharField(max_length=255, null=True)
    ip = fields.CharField(max_length=64, null=True)
    user_agent = fields.TextField(null=True)
    error = fields.TextField(null=True)
    notes = AnyJSONField(default=dict)

    class Meta:
        table = "pb_request_logs"
        ordering = ["-id"]


class FlowRun(Model):
    """One execution of a flow, with its step trace."""

    id = fields.CharField(max_length=32, primary_key=True)
    env = fields.CharField(max_length=63)
    flow = fields.CharField(max_length=128, db_index=True)
    trigger = fields.CharField(max_length=32)
    status = fields.CharField(max_length=16)
    input = AnyJSONField(null=True)
    output = AnyJSONField(null=True)
    error = fields.TextField(null=True)
    trace = AnyJSONField(default=list)
    logs = AnyJSONField(default=list)
    duration_ms = fields.FloatField(default=0)
    request_id = fields.CharField(max_length=64, null=True)
    job_id = fields.CharField(max_length=64, null=True)

    class Meta:
        table = "pb_flow_runs"
        ordering = ["-created_at"]


class EventLog(Model):
    """A published event and what consumed it."""

    id = ulid_pk()
    event_id = fields.CharField(max_length=64, unique=True, db_index=True)
    env = fields.CharField(max_length=63)
    name = fields.CharField(max_length=128, db_index=True)
    source = fields.CharField(max_length=32)
    actor = fields.CharField(max_length=255, null=True)
    payload = AnyJSONField(null=True)
    request_id = fields.CharField(max_length=64, null=True)
    occurred_at = fields.CharField(max_length=40)
    consumers = AnyJSONField(default=list)

    class Meta:
        table = "pb_event_log"
        ordering = ["-id"]


class WebhookDelivery(Model):
    """One attempt series to deliver an event to an outbound webhook."""

    id = ulid_pk()
    endpoint = fields.ForeignKeyField(
        "models.WebhookEndpoint", related_name="deliveries", on_delete=fields.CASCADE
    )
    event_id = fields.CharField(max_length=64, db_index=True)
    event = fields.CharField(max_length=128)
    payload = AnyJSONField(null=True)
    status = fields.CharField(max_length=16, default="pending")
    attempts = fields.IntField(default=0)
    response_status = fields.IntField(null=True)
    response_body = fields.TextField(null=True)
    error = fields.TextField(null=True)
    duration_ms = fields.FloatField(null=True)
    delivered_at = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_webhook_deliveries"
        ordering = ["-id"]


class JobRun(Model):
    """A queued job's lifecycle, recorded by job middleware for Studio.

    Sillo's queue holds the job itself; this row is its history.
    """

    id = fields.CharField(max_length=64, primary_key=True)
    env = fields.CharField(max_length=63)
    queue = fields.CharField(max_length=128, db_index=True)
    job = fields.CharField(max_length=128)
    target = fields.CharField(max_length=255, default="")
    source = fields.CharField(max_length=32, default="api")
    request_id = fields.CharField(max_length=64, null=True, db_index=True)
    status = fields.CharField(max_length=16, default="queued", db_index=True)
    attempts = fields.IntField(default=0)
    max_attempts = fields.IntField(default=1)
    payload = AnyJSONField(null=True)
    result = AnyJSONField(null=True)
    error = fields.TextField(null=True)
    available_at = fields.DatetimeField(null=True)
    started_at = fields.DatetimeField(null=True)
    finished_at = fields.DatetimeField(null=True)
    duration_ms = fields.FloatField(null=True)
    worker = fields.CharField(max_length=128, null=True)

    class Meta:
        table = "pb_job_runs"
        ordering = ["-created_at"]


class FailedJobRecord(Model):
    """Sillo's failed-job store, persisted (see ``app.jobs.failed``)."""

    id = ulid_pk()
    job_id = fields.CharField(max_length=64, db_index=True)
    queue = fields.CharField(max_length=128, db_index=True)
    job_class = fields.CharField(max_length=255)
    payload = fields.TextField()
    exception = fields.TextField()
    failed_at = fields.FloatField()

    class Meta:
        table = "pb_failed_jobs"
        ordering = ["-id"]


class MetricCounter(Model):
    """A per-minute counter written by ``metric.increment`` and platform code."""

    id = ulid_pk()
    env = fields.CharField(max_length=63)
    name = fields.CharField(max_length=128, db_index=True)
    tags = fields.CharField(max_length=512, default="")
    window = fields.DatetimeField(index=True)
    value = fields.FloatField(default=0)
    count = fields.IntField(default=0)

    class Meta:
        table = "pb_metrics"
        unique_together = (("env", "name", "tags", "window"),)
        ordering = ["-window"]


class MailLog(Model):
    """A message sent (or suppressed) through an environment's mail settings."""

    id = ulid_pk()
    env = fields.CharField(max_length=63)
    to = AnyJSONField(default=list)
    subject = fields.CharField(max_length=255, default="")
    template = fields.CharField(max_length=128, null=True)
    status = fields.CharField(max_length=16)
    message_id = fields.CharField(max_length=255, null=True)
    error = fields.TextField(null=True)
    source = fields.CharField(max_length=32, default="api")

    class Meta:
        table = "pb_mail_log"
        ordering = ["-id"]


class WorkerHeartbeat(Model):
    """A queue worker or scheduler process, as last seen."""

    id = ulid_pk()
    name = fields.CharField(max_length=255, unique=True)
    kind = fields.CharField(max_length=16, default="worker")
    queues = AnyJSONField(default=list)
    status = fields.CharField(max_length=16, default="running")
    concurrency = fields.IntField(default=1)
    processed = fields.IntField(default=0)
    started_at = fields.DatetimeField(null=True)
    last_seen = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_workers"
        ordering = ["-last_seen"]
