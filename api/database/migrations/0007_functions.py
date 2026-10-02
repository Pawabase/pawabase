"""Function artifacts and invocation history."""

import functools
from json import dumps, loads

from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise import fields, migrations
from tortoise.fields.base import OnDelete
from tortoise.migrations import operations as ops

from database.fields import AnyJSONField


def json_field(default):
    return AnyJSONField(default=default, encoder=functools.partial(dumps, separators=(",", ":")), decoder=loads)


def base_fields(pk):
    return [("created_at", CreatedAtField()), ("updated_at", UpdatedAtField()), ("deleted_at", SoftDeleteField(null=True)), ("id", pk)]


def env_field():
    return fields.ForeignKeyField("models.Environment", source_field="environment_id", db_constraint=True, to_field="id", related_name="function_deployments", on_delete=OnDelete.CASCADE)


class Migration(migrations.Migration):
    dependencies = [("models", "0006_branch_drafts")]

    operations = [
        ops.CreateModel(
            name="FunctionDeployment",
            fields=base_fields(fields.CharField(max_length=32, primary_key=True, unique=True, db_index=True)) + [
                ("checksum", fields.CharField(max_length=64, db_index=True)),
                ("status", fields.CharField(max_length=16, default="building")),
                ("runtime", fields.CharField(max_length=64, default="python3.11")),
                ("entrypoint", fields.CharField(max_length=255, default="functions")),
                ("manifest", json_field(dict)), ("limits", json_field(dict)),
                ("error", fields.TextField(default="")), ("activated_at", fields.DatetimeField(null=True)),
                ("removed_at", fields.DatetimeField(null=True)), ("created_by", fields.CharField(max_length=255, null=True)),
                ("environment", env_field()),
            ], options={"table": "pb_function_deployments", "app": "models", "ordering": ["-created_at"], "pk_attr": "id"}, bases=["Model"],
        ),
        ops.CreateModel(
            name="FunctionRun",
            fields=base_fields(fields.CharField(max_length=32, primary_key=True, unique=True, db_index=True)) + [
                ("project", fields.CharField(max_length=63, db_index=True)),
                ("env", fields.CharField(max_length=63, db_index=True)), ("function", fields.CharField(max_length=128, db_index=True)),
                ("deployment_id", fields.CharField(max_length=32, null=True)), ("trigger", fields.CharField(max_length=32)),
                ("status", fields.CharField(max_length=16, default="running")), ("input", json_field(None)),
                ("output", json_field(None)), ("error", fields.TextField(null=True)), ("logs", json_field(list)),
                ("duration_ms", fields.FloatField(null=True)), ("request_id", fields.CharField(max_length=128, null=True)),
                ("job_id", fields.CharField(max_length=128, null=True)),
            ], options={"table": "pb_function_runs", "app": "models", "ordering": ["-created_at"], "pk_attr": "id"}, bases=["Model"],
        ),
    ]
