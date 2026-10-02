"""Function deployments and runs know which branch they belong to."""

from tortoise import fields, migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    dependencies = [("models", "0007_functions")]

    operations = [
        ops.AddField(model_name="FunctionDeployment", name="branch", field=fields.CharField(max_length=63, default="main", db_index=True)),
        ops.AddField(model_name="FunctionRun", name="branch", field=fields.CharField(max_length=63, default="main")),
    ]
