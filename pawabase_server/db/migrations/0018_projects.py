from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from json import dumps, loads
from pawabase_server.core.ids import new_ulid
from pawabase_server.db.fields import AnyJSONField
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise.fields.base import OnDelete
from tortoise import fields
from tortoise.migrations.constraints import UniqueConstraint

class Migration(migrations.Migration):
    dependencies = [('models', '0017_resource_indexes')]

    initial = False

    operations = [
        ops.CreateModel(
            name='AgentOperation',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=64)),
                ('kind', fields.CharField(max_length=48)),
                ('project_id', fields.CharField(null=True, db_index=True, max_length=64)),
                ('status', fields.CharField(default='running', max_length=16)),
                ('attempts', fields.IntField(default=1)),
                ('nonce', fields.CharField(null=True, max_length=64)),
                ('result', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('error', fields.CharField(null=True, max_length=500)),
            ],
            options={'table': 'pb_agent_operations', 'app': 'models', 'pk_attr': 'id', 'table_description': 'One operation a runtime agent was asked to perform, kept so a repeat of it is answered, not done twice.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='Project',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=64)),
                ('name', fields.CharField(max_length=120)),
                ('slug', fields.CharField(unique=True, max_length=63)),
                ('status', fields.CharField(default='pending', max_length=16)),
                ('origin', fields.CharField(default='local', max_length=16)),
                ('external_ref', fields.CharField(null=True, max_length=64)),
                ('is_default', fields.BooleanField(default=False)),
                ('details', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ],
            options={'table': 'pb_projects', 'app': 'models', 'pk_attr': 'id', 'table_description': 'One application. ``id`` is a ULID, or the identifier a control plane (Pawabase Cloud) supplied, kept as it is.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='ProjectDatabaseBinding',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('project', fields.OneToOneField('models.Project', source_field='project_id', db_constraint=True, to_field='id', related_name='database', on_delete=OnDelete.CASCADE)),
                ('provider', fields.CharField(default='postgres', max_length=16)),
                ('mode', fields.CharField(default='local', max_length=16)),
                ('connection_ref', fields.TextField(unique=False)),
                ('status', fields.CharField(default='pending', max_length=16)),
                ('last_checked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('last_error', fields.CharField(null=True, max_length=500)),
                ('generation', fields.IntField(default=1, description='Counts credential changes, so a connection pool built from the old one is not reused.')),
            ],
            options={'table': 'pb_project_databases', 'app': 'models', 'pk_attr': 'id', 'table_description': 'The one primary application database of a project.'},
            bases=['Model'],
        ),
        ops.AlterField(
            model_name='Environment',
            name='name',
            field=fields.CharField(unique=True, description="The key every table in this runtime files this environment's data under (an ``env`` column, a Redis key, a bucket prefix). It stays", max_length=63),
        ),
        ops.AddField(
            model_name='Environment',
            name='project',
            field=fields.ForeignKeyField('models.Project', source_field='project_id', null=True, db_constraint=True, to_field='id', related_name='environments', on_delete=OnDelete.RESTRICT),
        ),
        ops.AddField(
            model_name='Environment',
            name='schema_name',
            field=fields.CharField(null=True, description="The PostgreSQL schema holding this environment's tables in its project's database (the SQLite file's stem on SQLite).", max_length=63),
        ),
        ops.AddField(
            model_name='Environment',
            name='slug',
            field=fields.CharField(null=True, description="The environment's name inside its project (``production``). Unique with the project; ``name`` equals it for the default project.", max_length=63),
        ),
        ops.AddField(
            model_name='Environment',
            name='status',
            field=fields.CharField(null=True, default='ready', description='``ready``, ``provisioning``, ``failed`` or ``deleting``. Null (rows from before projects) means ready.', max_length=16),
        ),
        ops.AddConstraint(
            model_name='Environment',
            constraint=UniqueConstraint(fields=('project', 'slug'), name=None),
        ),
    ]
