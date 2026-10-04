from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from database.fields import AnyJSONField
from json import dumps, loads
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise.fields.base import OnDelete
from uuid import uuid4
from tortoise import fields
from tortoise.migrations.constraints import UniqueConstraint

async def require_empty_runtime(apps, editor):
    """One installation is now one project: it does not carry the old projects and organizations over."""
    quote = (lambda n: f"`{n}`") if editor.client.capabilities.dialect == "mysql" else (lambda n: f'"{n}"')
    rows = await editor.client.execute_query_dict(f"SELECT COUNT(*) AS n FROM {quote('pb_projects')}")
    if rows and rows[0]["n"]:
        raise RuntimeError(
            "This database holds projects from a version of Pawabase that managed many. "
            "A Pawabase installation is now one project, with no organizations, and the old projects are not "
            "converted. Back up what you need (Studio's backups and blueprints, on the old version), then start "
            "this version from an empty database: docker compose down -v, then docker compose up -d."
        )


class Migration(migrations.Migration):
    dependencies = [('models', '0010_user_ids_are_ulids')]

    initial = False

    operations = [
        ops.RunPython(require_empty_runtime),
        ops.DeleteModel(name='ProjectKey'),  # before ApiKey exists: both claim Environment.keys
        ops.CreateModel(
            name='ApiKey',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.UUIDField(primary_key=True, default=uuid4, unique=True, db_index=True)),
                ('environment', fields.ForeignKeyField('models.Environment', source_field='environment_id', db_constraint=True, to_field='id', related_name='keys', on_delete=OnDelete.CASCADE)),
                ('name', fields.CharField(max_length=200)),
                ('role', fields.CharField(default='publishable', max_length=20)),
                ('prefix', fields.CharField(max_length=24)),
                ('key_hash', fields.CharField(unique=True, db_index=True, max_length=128)),
                ('scopes', AnyJSONField(default=list, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('allowed_ips', AnyJSONField(default=list, description='CIDRs and route rules evaluated by the public gateway before proxying.', encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('allowed_routes', AnyJSONField(default=list, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('expires_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('last_used_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('use_count', fields.IntField(default=0)),
                ('created_by', fields.CharField(null=True, max_length=255)),
            ],
            options={'table': 'pb_api_keys', 'app': 'models', 'pk_attr': 'id', 'table_description': 'An API key for one environment.'},
            bases=['Model'],
        ),
        ops.AlterModelOptions(
            name='AuditEntry',
            options={'table': 'pb_audit', 'app': 'models', 'pk_attr': 'id', 'table_description': 'What changed on the management plane.'},
        ),
        ops.RemoveField(model_name='AuditEntry', name='org'),
        ops.RemoveField(model_name='AuditEntry', name='project'),
        ops.RemoveConstraint(
            model_name='Environment',
            name=None,
            fields=['project', 'name'],
        ),
        ops.AlterModelOptions(
            name='Environment',
            options={'table': 'pb_environments', 'app': 'models', 'pk_attr': 'id', 'table_description': 'An isolated stage of this runtime (``development``, ``production``…).'},
        ),
        ops.AlterField(
            model_name='Environment',
            name='name',
            field=fields.CharField(unique=True, max_length=63),
        ),
        ops.RemoveField(model_name='Environment', name='project'),
        ops.RemoveField(model_name='EventLog', name='project'),
        ops.RemoveField(model_name='FlowRun', name='project'),
        ops.RemoveField(model_name='FunctionRun', name='project'),
        ops.RemoveField(model_name='JobRun', name='project'),
        ops.RemoveField(model_name='MailLog', name='project'),
        ops.RemoveConstraint(
            model_name='MetricCounter',
            name=None,
            fields=['project', 'env', 'name', 'tags', 'window'],
        ),
        ops.RemoveField(model_name='MetricCounter', name='project'),
        ops.AddConstraint(
            model_name='MetricCounter',
            constraint=UniqueConstraint(fields=('env', 'name', 'tags', 'window'), name=None),
        ),
        ops.RemoveField(model_name='RequestLog', name='project'),
        ops.DeleteModel(name='OrgInvitation'),
        ops.DeleteModel(name='OrgMember'),
        ops.DeleteModel(name='Project'),
        ops.DeleteModel(name='Organization'),
    ]
