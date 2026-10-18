from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from json import dumps, loads
from pawabase_server.core.ids import new_ulid
from pawabase_server.db.fields import AnyJSONField
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0030_realtime_persistence')]

    initial = False

    operations = [
        ops.CreateModel(
            name='Dashboard',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('environment', fields.ForeignKeyField('models.Environment', source_field='environment_id', db_constraint=True, to_field='id', related_name='dashboards', on_delete=OnDelete.CASCADE)),
                ('name', fields.CharField(max_length=64)),
                ('title', fields.CharField(default='', max_length=160)),
                ('description', fields.TextField(default='', unique=False)),
                ('definition', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('enabled', fields.BooleanField(default=True)),
                ('public', fields.BooleanField(default=False)),
                ('publishable_key_ciphertext', fields.TextField(null=True, unique=False)),
            ],
            options={'table': 'pb_dashboards', 'app': 'models', 'unique_together': (('environment', 'name'),), 'pk_attr': 'id', 'table_description': 'A page (or several) of widgets, published as its own web page.'},
            bases=['Model'],
        ),
    ]
