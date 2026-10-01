"""Add isolated working snapshots to definition branches."""

import functools
from json import dumps, loads

from tortoise import migrations
from tortoise.migrations import operations as ops

from database.fields import AnyJSONField


def json_field(default):
    return AnyJSONField(
        default=default,
        encoder=functools.partial(dumps, separators=(",", ":")),
        decoder=loads,
    )


class Migration(migrations.Migration):
    dependencies = [("models", "0005_key_restrictions_and_previews")]

    operations = [
        ops.AddField(
            model_name="Branch",
            name="draft",
            field=json_field(dict),
        ),
        ops.AddField(
            model_name="Branch",
            name="base_snapshot",
            field=json_field(dict),
        ),
        ops.AddField(
            model_name="Branch",
            name="changes",
            field=json_field(list),
        ),
    ]
