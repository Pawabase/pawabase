"""Alarms, the public status page, backup snapshots and schedules, custom domains and firewall rules."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from pawabase_server.core.records import ulid_pk
from pawabase_server.db.fields import AnyJSONField


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
    """An ordered rule the gateway checks before a request reaches anything (see ``core/firewall.py``)."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    name = fields.CharField(max_length=120)
    position = fields.IntField(default=0)
    #: ``allow``, ``block``, ``log`` or ``rate_limit``.
    action = fields.CharField(max_length=16, default="block")
    enabled = fields.BooleanField(default=True)
    #: The older shape: any of ``ips``, ``paths``, ``methods``, ``user_agents``. Combined with ``conditions``.
    match = AnyJSONField(default=dict)
    #: ``[{field, op, value}]``, joined by ``logic`` (``all`` or ``any``).
    conditions = AnyJSONField(default=list)
    logic = fields.CharField(max_length=4, default="all")
    #: ``enforce``, or ``simulate``: evaluated and recorded, never acted on.
    mode = fields.CharField(max_length=10, default="enforce")
    #: For ``rate_limit``: ``{requests, window_seconds, by, ban_seconds}``.
    rate_limit = AnyJSONField(null=True)
    #: A temporary rule stops applying at this time.
    expires_at = fields.DatetimeField(null=True)
    hits = fields.BigIntField(default=0)
    last_hit_at = fields.DatetimeField(null=True)
    note = fields.TextField(default="")

    class Meta:
        table = "pb_firewall_rules"
        ordering = ["position", "id"]


class FirewallConfig(Model):
    """An environment's managed rule sets and automatic bans."""

    env = fields.CharField(max_length=63, primary_key=True)
    #: ``{set: "off" | "log" | "block"}`` (``core/firewall.py`` ``MANAGED``).
    managed = AnyJSONField(default=dict)
    #: ``{enabled, threshold, window_seconds, ban_seconds, statuses, exempt}``.
    auto_ban = AnyJSONField(default=dict)

    class Meta:
        table = "pb_firewall_config"


class FirewallBan(Model):
    """An address (or range) refused until ``expires_at`` (or until removed, when it has none)."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    ip = fields.CharField(max_length=64)
    #: ``manual``, ``auto`` (too many errors) or ``rate_limit``.
    source = fields.CharField(max_length=16, default="manual")
    reason = fields.CharField(max_length=300, default="")
    expires_at = fields.DatetimeField(null=True)
    hits = fields.IntField(default=0)

    class Meta:
        table = "pb_firewall_bans"
        ordering = ["-created_at"]


class FirewallEvent(Model):
    """One request the firewall acted on or recorded."""

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    ip = fields.CharField(max_length=64)
    method = fields.CharField(max_length=10)
    path = fields.CharField(max_length=1024)
    country = fields.CharField(max_length=8, default="")
    user_agent = fields.CharField(max_length=300, default="")
    #: ``rule``, ``managed``, ``ban`` or ``rate_limit``.
    source = fields.CharField(max_length=16)
    #: ``block``, ``log``, ``rate_limit``, ``ban`` or ``allow``.
    action = fields.CharField(max_length=16)
    rule_id = fields.CharField(max_length=26, null=True)
    rule_name = fields.CharField(max_length=120, default="")
    simulated = fields.BooleanField(default=False)

    class Meta:
        table = "pb_firewall_events"
        ordering = ["-id"]


class ObjectShare(Model):
    """A temporary link to one object.

    Lookup is by the token's SHA-256; the token itself is kept only sealed with the platform key, for Studio's copy button. ``permissions`` is ``view``, ``download`` or ``both``.
    A link grants that one object in that one bucket of that one environment, and nothing else.
    """

    id = ulid_pk()
    env = fields.CharField(max_length=63, db_index=True)
    bucket = fields.CharField(max_length=63)
    key = fields.CharField(max_length=1024)
    token_hash = fields.CharField(max_length=64, unique=True)
    #: The token sealed with the platform key, so Studio can show the link again to the person who manages it. Lookup uses the hash.
    token_ciphertext = fields.TextField(default="")
    permissions = fields.CharField(max_length=12, default="both")
    label = fields.CharField(max_length=120, default="")
    expires_at = fields.DatetimeField()
    revoked_at = fields.DatetimeField(null=True)
    created_by = fields.CharField(max_length=120, default="")
    hits = fields.IntField(default=0)
    last_used_at = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_object_shares"
        ordering = ["-created_at"]


class Lease(Model):
    """A named lease one process holds at a time (the scheduler's leadership), renewed before ``expires_at``."""

    name = fields.CharField(max_length=64, primary_key=True)
    holder = fields.CharField(max_length=200)
    expires_at = fields.DatetimeField()

    class Meta:
        table = "pb_leases"
