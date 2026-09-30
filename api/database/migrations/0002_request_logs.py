import functools
from json import dumps, loads

from database.fields import AnyJSONField
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0001_initial")]

    operations = [
        ops.CreateModel(
            name="RequestLog",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.IntField(
                        generated=True, primary_key=True, unique=True, db_index=True
                    ),
                ),
                ("request_id", fields.CharField(max_length=64, db_index=True)),
                ("service", fields.CharField(max_length=32, db_index=True)),
                ("project", fields.CharField(max_length=63, db_index=True)),
                ("env", fields.CharField(max_length=63, db_index=True)),
                ("method", fields.CharField(max_length=16)),
                ("path", fields.TextField()),
                ("route", fields.CharField(max_length=512, null=True)),
                ("status", fields.IntField(db_index=True)),
                ("duration_ms", fields.FloatField(default=0)),
                ("started_at", fields.CharField(max_length=40, db_index=True)),
                ("role", fields.CharField(max_length=64, null=True)),
                ("user", fields.CharField(max_length=255, null=True)),
                ("ip", fields.CharField(max_length=64, null=True)),
                ("user_agent", fields.TextField(null=True)),
                ("error", fields.TextField(null=True)),
                (
                    "notes",
                    AnyJSONField(
                        default=dict,
                        encoder=functools.partial(dumps, separators=(",", ":")),
                        decoder=loads,
                    ),
                ),
            ],
            options={
                "table": "pb_request_logs",
                "app": "models",
                "pk_attr": "id",
                "table_description": "One persisted data-plane request and its structured trace notes.",
            },
            bases=["Model"],
        ),
        ops.AddField(
            model_name="JobRun",
            name="request_id",
            field=fields.CharField(max_length=64, null=True, db_index=True),
        ),
    ]
