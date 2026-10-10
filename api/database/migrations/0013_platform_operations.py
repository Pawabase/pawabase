from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from database.fields import AnyJSONField
from json import dumps, loads
from pawabase_core.ids import new_ulid
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise.fields.base import OnDelete
from tortoise import fields


class Migration(migrations.Migration):
    dependencies = [("models", "0012_secret_rotation")]

    initial = False

    operations = [
        ops.CreateModel(
            name="AlarmRule",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("name", fields.CharField(max_length=120)),
                ("description", fields.TextField(default="", unique=False)),
                ("metric", fields.CharField(max_length=64)),
                (
                    "comparison",
                    fields.CharField(
                        default="gt",
                        description="``gt``, ``gte``, ``lt`` or ``lte``.",
                        max_length=4,
                    ),
                ),
                ("threshold", fields.FloatField(default=0)),
                ("window_minutes", fields.IntField(default=5)),
                (
                    "breaches_needed",
                    fields.IntField(
                        default=1,
                        description="Checks in a row that must breach before the alarm fires, so a blip does not page anyone.",
                    ),
                ),
                (
                    "recipients",
                    AnyJSONField(
                        default=list,
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                ("enabled", fields.BooleanField(default=True)),
                (
                    "state",
                    fields.CharField(
                        default="unknown",
                        description="``ok``, ``alarm`` or ``unknown`` (never checked, or no data).",
                        max_length=16,
                    ),
                ),
                (
                    "state_since",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
                ("last_value", fields.FloatField(null=True)),
                (
                    "last_checked_at",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
                ("breach_count", fields.IntField(default=0)),
                (
                    "last_notified_at",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
                (
                    "renotify_minutes",
                    fields.IntField(
                        default=0,
                        description="While in alarm, tell people again after this many minutes. 0 tells them once.",
                    ),
                ),
                (
                    "muted_until",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
            ],
            options={
                "table": "pb_alarm_rules",
                "app": "models",
                "unique_together": (("env", "name"),),
                "pk_attr": "id",
                "table_description": "A watch on one metric of an environment: when it crosses a line for long enough, people are told.",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="AlarmEvent",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                (
                    "rule",
                    fields.ForeignKeyField(
                        "models.AlarmRule",
                        source_field="rule_id",
                        db_constraint=True,
                        to_field="id",
                        related_name="events",
                        on_delete=OnDelete.CASCADE,
                    ),
                ),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("from_state", fields.CharField(max_length=16)),
                ("to_state", fields.CharField(max_length=16)),
                ("value", fields.FloatField(null=True)),
                ("message", fields.TextField(default="", unique=False)),
                ("notified", fields.BooleanField(default=False)),
                ("error", fields.TextField(null=True, unique=False)),
            ],
            options={
                "table": "pb_alarm_events",
                "app": "models",
                "pk_attr": "id",
                "table_description": "One change of an alarm's state, and whether anyone was told.",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="BackupSchedule",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(unique=True, max_length=63)),
                ("enabled", fields.BooleanField(default=False)),
                (
                    "frequency",
                    fields.CharField(
                        default="daily",
                        description="``hourly``, ``daily`` or ``weekly``.",
                        max_length=8,
                    ),
                ),
                ("hour", fields.IntField(default=3)),
                ("weekday", fields.IntField(default=0)),
                ("keep_last", fields.IntField(default=7)),
                ("keep_days", fields.IntField(default=30)),
                (
                    "include",
                    AnyJSONField(
                        default=list,
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                (
                    "last_run_at",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
                ("last_status", fields.CharField(default="", max_length=16)),
            ],
            options={"table": "pb_backup_schedules", "app": "models", "pk_attr": "id"},
            bases=["Model"],
        ),
        ops.CreateModel(
            name="BackupSnapshot",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("name", fields.CharField(max_length=160)),
                ("note", fields.TextField(default="", unique=False)),
                (
                    "trigger",
                    fields.CharField(
                        default="manual",
                        description="``manual``, ``scheduled`` or ``before-restore``.",
                        max_length=16,
                    ),
                ),
                (
                    "include",
                    AnyJSONField(
                        default=list,
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                ("size_bytes", fields.IntField(default=0)),
                ("checksum", fields.CharField(default="", max_length=64)),
                ("storage_key", fields.CharField(default="", max_length=255)),
                ("status", fields.CharField(default="complete", max_length=16)),
                ("error", fields.TextField(null=True, unique=False)),
                (
                    "counts",
                    AnyJSONField(
                        default=dict,
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                (
                    "pinned",
                    fields.BooleanField(
                        default=False, description="A kept snapshot is never removed by retention."
                    ),
                ),
            ],
            options={
                "table": "pb_backup_snapshots",
                "app": "models",
                "pk_attr": "id",
                "table_description": "A saved copy of an environment, kept in the platform's object storage.",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="Domain",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("hostname", fields.CharField(unique=True, max_length=253)),
                ("token", fields.CharField(max_length=64)),
                (
                    "status",
                    fields.CharField(
                        default="pending",
                        description="``pending``, ``verified`` or ``failed``.",
                        max_length=12,
                    ),
                ),
                (
                    "verified_at",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
                (
                    "last_checked_at",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
                ("last_error", fields.TextField(null=True, unique=False)),
            ],
            options={
                "table": "pb_domains",
                "app": "models",
                "pk_attr": "id",
                "table_description": "A hostname an environment answers on, once the owner has shown they control it.",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="FirewallRule",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("name", fields.CharField(max_length=120)),
                ("position", fields.IntField(default=0)),
                (
                    "action",
                    fields.CharField(
                        default="block", description="``allow`` or ``block``.", max_length=8
                    ),
                ),
                ("enabled", fields.BooleanField(default=True)),
                (
                    "match",
                    AnyJSONField(
                        default=dict,
                        description="Any of ``ips`` (addresses or CIDRs), ``paths`` (globs), ``methods``, ``user_agents`` (substrings).",
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                ("note", fields.TextField(default="", unique=False)),
            ],
            options={
                "table": "pb_firewall_rules",
                "app": "models",
                "pk_attr": "id",
                "table_description": "An ordered rule the gateway checks before a request reaches anything: the first that matches decides.",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="StatusIncident",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("title", fields.CharField(max_length=200)),
                (
                    "status",
                    fields.CharField(
                        default="investigating",
                        description="``investigating``, ``identified``, ``monitoring`` or ``resolved``.",
                        max_length=16,
                    ),
                ),
                (
                    "impact",
                    fields.CharField(
                        default="minor",
                        description="``none``, ``minor``, ``major`` or ``critical``.",
                        max_length=16,
                    ),
                ),
                (
                    "components",
                    AnyJSONField(
                        default=list,
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                (
                    "updates",
                    AnyJSONField(
                        default=list,
                        description='``[{"at", "status", "message"}]``, oldest first.',
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                (
                    "resolved_at",
                    fields.DatetimeField(null=True, auto_now=False, auto_now_add=False),
                ),
            ],
            options={"table": "pb_status_incidents", "app": "models", "pk_attr": "id"},
            bases=["Model"],
        ),
        ops.CreateModel(
            name="StatusPage",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(unique=True, max_length=63)),
                ("enabled", fields.BooleanField(default=False)),
                ("title", fields.CharField(default="Status", max_length=120)),
                ("description", fields.TextField(default="", unique=False)),
                (
                    "components",
                    AnyJSONField(
                        default=list,
                        description='``[{"name", "source", "description"}]``. A source is ``gateway``, ``api``, ``auth``, ``realtime``,',
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
                ("contact_url", fields.TextField(default="", unique=False)),
            ],
            options={
                "table": "pb_status_pages",
                "app": "models",
                "pk_attr": "id",
                "table_description": "What an environment shows the public at /status.",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="StatusSample",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(max_length=63)),
                ("component", fields.CharField(max_length=64)),
                ("at", fields.DatetimeField(db_index=True, auto_now=False, auto_now_add=False)),
                ("ok", fields.BooleanField(default=True)),
            ],
            options={
                "table": "pb_status_samples",
                "app": "models",
                "pk_attr": "id",
                "table_description": "One look at one component, for the uptime history.",
            },
            bases=["Model"],
        ),
        ops.AlterField(
            model_name="Secret",
            name="previous_ciphertext",
            field=fields.TextField(
                null=True,
                description="The value this one replaced, kept so a bad rotation can be undone.",
                unique=False,
            ),
        ),
        ops.AlterField(
            model_name="Secret",
            name="rotate_every_days",
            field=fields.IntField(
                null=True,
                description="A reminder, not a job: a secret older than this is shown as due for rotation.",
            ),
        ),
    ]
