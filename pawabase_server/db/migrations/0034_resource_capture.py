from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    """A resource can capture changes made outside the API (database triggers and an outbox)."""

    dependencies = [("models", "0033_saved_queries")]

    operations = [
        # Existing resources keep today's behaviour: nothing is captured.
        ops.AddField(
            model_name="Resource",
            name="capture",
            field=fields.BooleanField(default=False, null=True),
        ),
        ops.RunSQL("UPDATE pb_resources SET capture = false WHERE capture IS NULL"),
        ops.AlterField(
            model_name="Resource", name="capture", field=fields.BooleanField(default=False)
        ),
    ]
