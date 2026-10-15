from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0019_project_limits")]

    operations = [
        ops.AddField(
            model_name="Bucket",
            name="signed_downloads",
            field=fields.BooleanField(null=True),
        ),
        ops.RunSQL("UPDATE pb_buckets SET signed_downloads = FALSE WHERE signed_downloads IS NULL"),
        ops.AlterField(
            model_name="Bucket",
            name="signed_downloads",
            field=fields.BooleanField(default=False),
        ),
    ]
