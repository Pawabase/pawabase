"""Gateway replicas: a row per running ``pawabase-server serve`` process, so Studio and a load balancer's health check can see the
fleet instead of one fixed address. See ``pawabase_server/db/models/gateway.py``."""

from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField


class Migration(migrations.Migration):
    dependencies = [("models", "0028_function_limits")]

    operations = [
        ops.CreateModel(
            name="GatewayInstance",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                ("id", fields.CharField(primary_key=True, max_length=128)),
                ("plane_id", fields.CharField(default="", db_index=True, max_length=64)),
                ("host", fields.CharField(max_length=255)),
                ("internal_url", fields.CharField(max_length=255)),
                ("version", fields.CharField(default="", max_length=64)),
                ("draining", fields.BooleanField(default=False)),
                ("drain_requested_at", fields.DatetimeField(null=True)),
                ("heartbeat_at", fields.DatetimeField(auto_now=True)),
            ],
            options={
                "table": "pb_gateway_instances",
                "app": "models",
                "pk_attr": "id",
                "table_description": "One running gateway (Edge + Platform) process of this installation.",
            },
            bases=["Model"],
        ),
    ]
