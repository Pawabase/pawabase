from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from pawabase_server.core.ids import new_ulid
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField


class Migration(migrations.Migration):
    dependencies = [("models", "0023_durable_events")]

    operations = [
        ops.CreateModel(
            name="UsageCounter",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26
                    ),
                ),
                ("project_id", fields.CharField(max_length=64)),
                ("metric", fields.CharField(max_length=48)),
                ("period", fields.CharField(default="", max_length=7)),
                ("value", fields.BigIntField(default=0)),
            ],
            options={
                "table": "pb_usage_counters",
                "app": "models",
                "pk_attr": "id",
                "unique_together": (("project_id", "metric", "period"),),
                "table_description": "A project's metered usage.",
            },
            bases=["Model"],
        ),
    ]
