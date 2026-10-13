import functools
from json import dumps, loads

from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from pawabase_server.db.fields import AnyJSONField


class Migration(migrations.Migration):
    dependencies = [("models", "0018_projects")]

    operations = [
        ops.AddField(
            model_name="Project",
            name="limits",
            field=AnyJSONField(null=True, encoder=functools.partial(dumps, separators=(",", ":")), decoder=loads),
        ),
        # Existing installations already have projects. PostgreSQL cannot add their
        # required JSON policy column until every legacy row has a value.
        ops.RunSQL("UPDATE pb_projects SET limits = '{}' WHERE limits IS NULL"),
        ops.AlterField(
            model_name="Project",
            name="limits",
            field=AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(",", ":")), decoder=loads),
        ),
        ops.AddField(model_name="Project", name="limits_version", field=fields.IntField(null=True)),
        ops.RunSQL("UPDATE pb_projects SET limits_version = 1 WHERE limits_version IS NULL"),
        ops.AlterField(
            model_name="Project", name="limits_version", field=fields.IntField(default=1)
        ),
    ]
