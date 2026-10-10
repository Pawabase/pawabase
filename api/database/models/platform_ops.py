"""Alarms, the public status page, backup snapshots and schedules, custom domains and firewall rules."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField
from pawabase_core.records import ulid_pk


class AlarmRule(Model):
    """A watch on one metric of an environment: when it crosses a line for long enough, people are told."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    name = fields.CharField(max_length=120)
    description = fields.TextField(default="")
    metric = fields.CharField(max_length=64)
    #: ``gt``, ``gte``, ``lt`` or ``lte``.
    comparison = fields.CharField(max_length=4, default="gt")
    threshold = fields.FloatField(default=0)
    window_minutes = fields.IntField(default=5)
    #: Checks in a row that must breach before the alarm fires, so a blip does not page anyone.
    breaches_needed = fields.IntField(default=1)
    recipients = AnyJSONField(default=list)
    enabled = fields.BooleanField(default=True)
    #: ``ok``, ``alarm`` or ``unknown`` (never checked, or no data).
    state = fields.CharField(max_length=16, default="unknown")
    state_since = fields.DatetimeField(null=True)
    last_value = fields.FloatField(null=True)
    last_checked_at = fields.DatetimeField(null=True)
    breach_count = fields.IntField(default=0)
    last_notified_at = fields.DatetimeField(null=True)
    #: While in alarm, tell people again after this many minutes. 0 tells them once.
    renotify_minutes = fields.IntField(default=0)
    muted_until = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_alarm_rules"
        unique_together = (("env", "name"),)
        ordering = ["name"]


class AlarmEvent(Model):
    """One change of an alarm's state, and whether anyone was told."""

    id = ulid_pk()
    rule = fields.ForeignKeyField(
        "models.AlarmRule", related_name="events", on_delete=fields.CASCADE
    )
    env = fields.CharField(max_length=63, db_index=True)
    from_state = fields.CharField(max_length=16)
    to_state = fields.CharField(max_length=16)
    value = fields.FloatField(null=True)
    message = fields.TextField(default="")
    notified = fields.BooleanField(default=False)
    error = fields.TextField(null=True)

    class Meta:
        table = "pb_alarm_events"
        ordering = ["-id"]


class StatusPage(Model):
    """What an environment shows the public at /status."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, unique=True)
    enabled = fields.BooleanField(default=False)
    title = fields.CharField(max_length=120, default="Status")
    description = fields.TextField(default="")
    #: ``[{"name", "source", "description"}]``. A source is ``gateway``, ``api``, ``auth``, ``realtime``,
    #: ``workers`` or ``manual``.
    components = AnyJSONField(default=list)
    contact_url = fields.TextField(default="")

    class Meta:
        table = "pb_status_pages"


class StatusIncident(Model):
    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    title = fields.CharField(max_length=200)
    #: ``investigating``, ``identified``, ``monitoring`` or ``resolved``.
    status = fields.CharField(max_length=16, default="investigating")
    #: ``none``, ``minor``, ``major`` or ``critical``.
    impact = fields.CharField(max_length=16, default="minor")
    components = AnyJSONField(default=list)
    #: ``[{"at", "status", "message"}]``, oldest first.
    updates = AnyJSONField(default=list)
    resolved_at = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_status_incidents"
        ordering = ["-id"]


class StatusSample(Model):
    """One look at one component, for the uptime history."""

    id = ulid_pk()
    env = fields.CharField(max_length=63)
    component = fields.CharField(max_length=64)
    at = fields.DatetimeField(db_index=True)
    ok = fields.BooleanField(default=True)

    class Meta:
        table = "pb_status_samples"


class BackupSnapshot(Model):
    """A saved copy of an environment, kept in the platform's object storage."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    name = fields.CharField(max_length=160)
    note = fields.TextField(default="")
    #: ``manual``, ``scheduled`` or ``before-restore``.
    trigger = fields.CharField(max_length=16, default="manual")
    include = AnyJSONField(default=list)
    size_bytes = fields.IntField(default=0)
    checksum = fields.CharField(max_length=64, default="")
    storage_key = fields.CharField(max_length=255, default="")
    status = fields.CharField(max_length=16, default="complete")
    error = fields.TextField(null=True)
    counts = AnyJSONField(default=dict)
    #: A kept snapshot is never removed by retention.
    pinned = fields.BooleanField(default=False)

    class Meta:
        table = "pb_backup_snapshots"
        ordering = ["-id"]


class BackupSchedule(Model):
    id = ulid_pk()
    env = fields.CharField(max_length=63, unique=True)
    enabled = fields.BooleanField(default=False)
    #: ``hourly``, ``daily`` or ``weekly``.
    frequency = fields.CharField(max_length=8, default="daily")
    hour = fields.IntField(default=3)
    weekday = fields.IntField(default=0)
    keep_last = fields.IntField(default=7)
    keep_days = fields.IntField(default=30)
    include = AnyJSONField(default=list)
    last_run_at = fields.DatetimeField(null=True)
    last_status = fields.CharField(max_length=16, default="")

    class Meta:
        table = "pb_backup_schedules"


class Domain(Model):
    """A hostname an environment answers on, once the owner has shown they control it."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    hostname = fields.CharField(max_length=253, unique=True)
    token = fields.CharField(max_length=64)
    #: ``pending``, ``verified`` or ``failed``.
    status = fields.CharField(max_length=12, default="pending")
    verified_at = fields.DatetimeField(null=True)
    last_checked_at = fields.DatetimeField(null=True)
    last_error = fields.TextField(null=True)

    class Meta:
        table = "pb_domains"
        ordering = ["hostname"]


class FirewallRule(Model):
    """An ordered rule the gateway checks before a request reaches anything: the first that matches decides."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    name = fields.CharField(max_length=120)
    position = fields.IntField(default=0)
    #: ``allow`` or ``block``.
    action = fields.CharField(max_length=8, default="block")
    enabled = fields.BooleanField(default=True)
    #: Any of ``ips`` (addresses or CIDRs), ``paths`` (globs), ``methods``, ``user_agents`` (substrings).
    #: A rule matches when every key it has matches.
    match = AnyJSONField(default=dict)
    note = fields.TextField(default="")

    class Meta:
        table = "pb_firewall_rules"
        ordering = ["position", "id"]
