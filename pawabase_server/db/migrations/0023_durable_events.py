from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0022_object_share_token")]

    operations = [
        ops.AddField(model_name="EventLog", name="routed_at", field=fields.DatetimeField(null=True)),
        # Events recorded before this migration were routed in full when they were recorded.
        ops.RunSQL("UPDATE pb_event_log SET routed_at = created_at WHERE routed_at IS NULL"),
        ops.AddField(
            model_name="WebhookDelivery", name="next_attempt_at", field=fields.DatetimeField(null=True)
        ),
    ]
