"""Resource limits on every function run: what a run had, what it used, and which limit stopped it (the limits are the project's)."""

import functools
from json import dumps, loads

from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from pawabase_server.db.fields import AnyJSONField

JSON = dict(encoder=functools.partial(dumps, separators=(",", ":")), decoder=loads)


class Migration(migrations.Migration):
    dependencies = [("models", "0027_http_functions")]

    operations = [
        ops.AddField(model_name="FunctionRun", name="limits", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(model_name="FunctionRun", name="cpu_ms", field=fields.FloatField(null=True)),
        ops.AddField(model_name="FunctionRun", name="memory_peak_mb", field=fields.FloatField(null=True)),
        ops.AddField(model_name="FunctionRun", name="limit_hit", field=fields.CharField(max_length=16, null=True)),
    ]
