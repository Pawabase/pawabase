"""HTTP functions: what a run of one records beyond an ``fn()`` call (all nullable or defaulted, so existing rows need nothing)."""

from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0026_advanced_firewall")]

    operations = [
        # ``call`` (an fn() function: JSON in, JSON out) or ``http`` (an HTTP function: a request in, a response out).
        ops.AddField(model_name="FunctionRun", name="kind", field=fields.CharField(max_length=8, null=True)),
        ops.AddField(model_name="FunctionRun", name="method", field=fields.CharField(max_length=10, null=True)),
        ops.AddField(model_name="FunctionRun", name="path", field=fields.CharField(max_length=512, null=True)),
        ops.AddField(model_name="FunctionRun", name="status_code", field=fields.IntField(null=True)),
        # Whether the call waited for a worker to start (a cold start), and which worker answered it.
        ops.AddField(model_name="FunctionRun", name="cold_start", field=fields.BooleanField(null=True)),
        ops.AddField(model_name="FunctionRun", name="worker", field=fields.CharField(max_length=32, null=True)),
        # Bytes in and out, and how long work handed to waitUntil ran after the response.
        ops.AddField(model_name="FunctionRun", name="request_bytes", field=fields.BigIntField(null=True)),
        ops.AddField(model_name="FunctionRun", name="response_bytes", field=fields.BigIntField(null=True)),
        ops.AddField(model_name="FunctionRun", name="background_ms", field=fields.FloatField(null=True)),
        ops.AddField(model_name="FunctionRun", name="fetches", field=fields.IntField(null=True)),
    ]
