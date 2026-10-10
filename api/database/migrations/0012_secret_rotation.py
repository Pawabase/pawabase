"""Secret versions: the previous value, when it was rotated, and how often it should be."""

from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0011_single_runtime")]

    operations = [
        ops.AddField(
            model_name="Secret", name="previous_ciphertext", field=fields.TextField(null=True)
        ),
        # Added empty, filled, then made required: existing rows are version 1.
        ops.AddField(model_name="Secret", name="version", field=fields.IntField(null=True)),
        ops.RunSQL("UPDATE pb_secrets SET version = 1 WHERE version IS NULL"),
        ops.AlterField(model_name="Secret", name="version", field=fields.IntField(default=1)),
        ops.AddField(model_name="Secret", name="rotated_at", field=fields.DatetimeField(null=True)),
        ops.AddField(
            model_name="Secret", name="rotate_every_days", field=fields.IntField(null=True)
        ),
    ]
