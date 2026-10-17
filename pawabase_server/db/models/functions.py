"""Immutable source artifacts and execution history for custom Functions."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from pawabase_server.db.fields import AnyJSONField


class FunctionDeployment(Model):
    """One uploaded, content-addressed Functions artifact for an environment.

    The artifact itself is kept in the configured code volume; this record is
    the durable control-plane history and deliberately never stores source in
    the platform database.
    """

    id = fields.CharField(max_length=32, primary_key=True)
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="function_deployments", on_delete=fields.CASCADE
    )
    branch = fields.CharField(max_length=63, default="main", db_index=True)
    checksum = fields.CharField(max_length=64, db_index=True)
    status = fields.CharField(max_length=16, default="building")
    runtime = fields.CharField(max_length=64, default="python3.11")
    entrypoint = fields.CharField(max_length=255, default="functions")
    manifest = AnyJSONField(default=dict)
    limits = AnyJSONField(default=dict)
    error = fields.TextField(default="")
    activated_at = fields.DatetimeField(null=True)
    removed_at = fields.DatetimeField(null=True)
    created_by = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "pb_function_deployments"
        ordering = ["-created_at"]


class FunctionRun(Model):
    """An invocation record, including safe structured logs and failures."""

    id = fields.CharField(max_length=32, primary_key=True)
    env = fields.CharField(max_length=63, db_index=True)
    branch = fields.CharField(max_length=63, default="main")
    function = fields.CharField(max_length=128, db_index=True)
    deployment_id = fields.CharField(max_length=32, null=True)
    trigger = fields.CharField(max_length=32)
    status = fields.CharField(max_length=16, default="running")
    input = AnyJSONField(null=True)
    output = AnyJSONField(null=True)
    error = fields.TextField(null=True)
    logs = AnyJSONField(default=list)
    duration_ms = fields.FloatField(null=True)
    request_id = fields.CharField(max_length=128, null=True)
    job_id = fields.CharField(max_length=128, null=True)
    #: ``call`` (an fn() function) or ``http`` (an HTTP function); ``None`` on runs recorded before HTTP functions existed.
    kind = fields.CharField(max_length=8, null=True)
    method = fields.CharField(max_length=10, null=True)
    path = fields.CharField(max_length=512, null=True)
    status_code = fields.IntField(null=True)
    cold_start = fields.BooleanField(null=True)
    worker = fields.CharField(max_length=32, null=True)
    request_bytes = fields.BigIntField(null=True)
    response_bytes = fields.BigIntField(null=True)
    background_ms = fields.FloatField(null=True)
    fetches = fields.IntField(null=True)

    class Meta:
        table = "pb_function_runs"
        ordering = ["-created_at"]
