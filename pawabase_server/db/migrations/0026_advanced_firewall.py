import functools
from json import dumps, loads

from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from pawabase_server.core.ids import new_ulid
from pawabase_server.db.fields import AnyJSONField
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField

JSON = dict(encoder=functools.partial(dumps, separators=(",", ":")), decoder=loads)


def ulid():
    return fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)


def stamps():
    return [("created_at", CreatedAtField()), ("updated_at", UpdatedAtField()), ("deleted_at", SoftDeleteField(null=True))]


class Migration(migrations.Migration):
    dependencies = [("models", "0025_scheduler_lease")]

    operations = [
        ops.AlterField(model_name="FirewallRule", name="action", field=fields.CharField(max_length=16, default="block")),
        # Added nullable, filled, then made required: existing rules have no value yet.
        ops.AddField(model_name="FirewallRule", name="conditions", field=AnyJSONField(null=True, **JSON)),
        ops.RunSQL("UPDATE pb_firewall_rules SET conditions = '[]' WHERE conditions IS NULL"),
        ops.AlterField(model_name="FirewallRule", name="conditions", field=AnyJSONField(default=list, **JSON)),
        ops.AddField(model_name="FirewallRule", name="logic", field=fields.CharField(max_length=4, null=True)),
        ops.RunSQL("UPDATE pb_firewall_rules SET logic = 'all' WHERE logic IS NULL"),
        ops.AlterField(model_name="FirewallRule", name="logic", field=fields.CharField(max_length=4, default="all")),
        ops.AddField(model_name="FirewallRule", name="mode", field=fields.CharField(max_length=10, null=True)),
        ops.RunSQL("UPDATE pb_firewall_rules SET mode = 'enforce' WHERE mode IS NULL"),
        ops.AlterField(model_name="FirewallRule", name="mode", field=fields.CharField(max_length=10, default="enforce")),
        ops.AddField(model_name="FirewallRule", name="rate_limit", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(model_name="FirewallRule", name="expires_at", field=fields.DatetimeField(null=True)),
        ops.AddField(model_name="FirewallRule", name="hits", field=fields.BigIntField(null=True)),
        ops.RunSQL("UPDATE pb_firewall_rules SET hits = 0 WHERE hits IS NULL"),
        ops.AlterField(model_name="FirewallRule", name="hits", field=fields.BigIntField(default=0)),
        ops.AddField(model_name="FirewallRule", name="last_hit_at", field=fields.DatetimeField(null=True)),
        ops.CreateModel(
            name="FirewallConfig",
            fields=[
                *stamps(),
                ("env", fields.CharField(primary_key=True, max_length=63)),
                ("managed", AnyJSONField(default=dict, **JSON)),
                ("auto_ban", AnyJSONField(default=dict, **JSON)),
            ],
            options={"table": "pb_firewall_config", "app": "models", "pk_attr": "env", "table_description": "An environment's managed rule sets and automatic bans."},
            bases=["Model"],
        ),
        ops.CreateModel(
            name="FirewallBan",
            fields=[
                *stamps(),
                ("id", ulid()),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("ip", fields.CharField(max_length=64)),
                ("source", fields.CharField(default="manual", max_length=16)),
                ("reason", fields.CharField(default="", max_length=300)),
                ("expires_at", fields.DatetimeField(null=True)),
                ("hits", fields.IntField(default=0)),
            ],
            options={"table": "pb_firewall_bans", "app": "models", "pk_attr": "id", "table_description": "An address refused until it expires."},
            bases=["Model"],
        ),
        ops.CreateModel(
            name="FirewallEvent",
            fields=[
                *stamps(),
                ("id", ulid()),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("ip", fields.CharField(max_length=64)),
                ("method", fields.CharField(max_length=10)),
                ("path", fields.CharField(max_length=1024)),
                ("country", fields.CharField(default="", max_length=8)),
                ("user_agent", fields.CharField(default="", max_length=300)),
                ("source", fields.CharField(max_length=16)),
                ("action", fields.CharField(max_length=16)),
                ("rule_id", fields.CharField(max_length=26, null=True)),
                ("rule_name", fields.CharField(default="", max_length=120)),
                ("simulated", fields.BooleanField(default=False)),
            ],
            options={"table": "pb_firewall_events", "app": "models", "pk_attr": "id", "table_description": "One request the firewall acted on or recorded."},
            bases=["Model"],
        ),
    ]
