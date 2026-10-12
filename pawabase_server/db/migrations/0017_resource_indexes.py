from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from json import dumps, loads
from pawabase_server.db.fields import AnyJSONField

class Migration(migrations.Migration):
    dependencies = [('models', '0016_directory')]

    initial = False

    operations = [
        ops.AddField(
            model_name='Resource',
            name='indexes',
            field=AnyJSONField(null=True, default=list, description='Indexes beyond the single-column ones fields ask for: ``[{"name", "columns", "unique", "where", "method"}]`` (see ``data/indexes.py``).', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads),
        ),
    ]
