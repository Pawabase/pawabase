from tortoise import migrations
from tortoise.migrations import operations as ops


class Migration(migrations.Migration):
    """Custom Functions are gone: their deployment records and run history go with them."""

    dependencies = [("models", "0034_resource_capture")]

    operations = [
        ops.DeleteModel(name="FunctionRun"),
        ops.DeleteModel(name="FunctionDeployment"),
    ]
