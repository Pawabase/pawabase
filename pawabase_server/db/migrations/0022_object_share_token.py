from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0021_object_storage")]

    operations = [
        ops.AddField(
            model_name="ObjectShare", name="token_ciphertext", field=fields.TextField(null=True)
        ),
        ops.RunSQL("UPDATE pb_object_shares SET token_ciphertext = '' WHERE token_ciphertext IS NULL"),
        ops.AlterField(
            model_name="ObjectShare",
            name="token_ciphertext",
            field=fields.TextField(default="", unique=False),
        ),
    ]
