from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    """Bandwidth: every recorded request now carries the bytes it received and sent."""

    dependencies = [("models", "0035_drop_functions")]

    operations = [
        ops.AddField(model_name="RequestLog", name="bytes_in", field=fields.BigIntField(null=True)),
        ops.AddField(model_name="RequestLog", name="bytes_out", field=fields.BigIntField(null=True)),
    ]
