from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from json import dumps, loads
from pawabase_server.core.ids import new_ulid
from pawabase_server.db.fields import AnyJSONField
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0014_identity')]

    initial = False

    operations = [
        ops.CreateModel(
            name='SchemaMigration',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('env', fields.CharField(db_index=True, max_length=63)),
                ('resource', fields.CharField(db_index=True, max_length=120)),
                ('table_name', fields.CharField(max_length=63)),
                ('number', fields.IntField(description='1, 2, 3 … per resource in an environment: the order the changes were made in.')),
                ('kind', fields.CharField(default='apply', description='``apply`` for a change to the table, ``rollback`` for undoing one.', max_length=16)),
                ('status', fields.CharField(default='applied', description='``applied``, ``failed`` or ``rolled_back`` (an ``apply`` that was undone).', max_length=16)),
                ('source', fields.CharField(default='studio', description='Where it came from: ``studio``, ``api``, ``blueprint``, ``restore`` or ``rollback``.', max_length=24)),
                ('summary', fields.CharField(default='', max_length=300)),
                ('changes', AnyJSONField(default=list, description='What changed: ``[{"id", "op", "column", "detail", "safe"}]``.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('up', AnyJSONField(default=list, description='The statements that ran, in order, and the ones that undo them, in the order to run.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('down', AnyJSONField(default=list, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('needs', AnyJSONField(default=list, description='Columns or tables a rollback depends on: they are kept until the migration is purged.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('columns', AnyJSONField(default=list, description='The columns the table had once this was applied.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('error', fields.TextField(null=True, unique=False)),
                ('actor', fields.CharField(null=True, max_length=120)),
                ('related_id', fields.CharField(null=True, description='The migration this rolled back, or the rollback that undid this one.', max_length=26)),
            ],
            options={'table': 'pb_schema_migrations', 'app': 'models', 'unique_together': (('env', 'resource', 'number'),), 'pk_attr': 'id', 'table_description': "One change made to a resource's table in an environment's database."},
            bases=['Model'],
        ),
        ops.AlterModelOptions(
            name='FailedJobRecord',
            options={'table': 'pb_failed_jobs', 'app': 'models', 'pk_attr': 'id', 'table_description': "Sillo's failed-job store, persisted (see ``pawabase_server.automation.jobs.failed``)."},
        ),
    ]
