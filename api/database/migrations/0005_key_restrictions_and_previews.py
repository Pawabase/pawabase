"""Gateway key restrictions and expiring deploy-preview environments."""

import functools
from json import dumps, loads

from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from database.fields import AnyJSONField


class Migration(migrations.Migration):
    dependencies = [("models", "0004_organizations")]

    operations = [
        ops.AddField(
            model_name="ProjectKey",
            name="allowed_ips",
            field=AnyJSONField(
                default=list,
                encoder=functools.partial(dumps, separators=(",", ":")),
                decoder=loads,
            ),
        ),
        ops.AddField(
            model_name="ProjectKey",
            name="allowed_routes",
            field=AnyJSONField(
                default=list,
                encoder=functools.partial(dumps, separators=(",", ":")),
                decoder=loads,
            ),
        ),
        ops.AddField(
            model_name="Environment",
            name="preview_source",
            field=fields.CharField(max_length=63, null=True),
        ),
        ops.AddField(
            model_name="Environment",
            name="preview_expires_at",
            field=fields.DatetimeField(null=True),
        ),
    ]
