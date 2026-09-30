from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0001_initial')]

    initial = False

    operations = [
        ops.AddField(
            model_name='SessionInfo',
            name='org',
            field=fields.CharField(null=True, max_length=63),
        ),
    ]
