from tortoise import migrations
from tortoise.migrations import operations as ops
import functools
from database.fields import AnyJSONField
from json import dumps, loads
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise.fields.base import OnDelete
from tortoise import fields

class Migration(migrations.Migration):
    dependencies = [('models', '0003_releases')]

    initial = False

    operations = [
        ops.CreateModel(
            name='Organization',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('slug', fields.CharField(unique=True, db_index=True, max_length=63)),
                ('name', fields.CharField(max_length=200)),
                ('created_by', fields.CharField(null=True, max_length=255)),
            ],
            options={'table': 'pb_organizations', 'app': 'models', 'pk_attr': 'id', 'table_description': 'A company or team. Members are platform operators, and projects belong to it.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='OrgInvitation',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='invitations', on_delete=OnDelete.CASCADE)),
                ('email', fields.CharField(db_index=True, max_length=255)),
                ('role', fields.CharField(default='developer', max_length=16)),
                ('token_hash', fields.CharField(unique=True, db_index=True, max_length=64)),
                ('invited_by', fields.CharField(null=True, max_length=255)),
                ('expires_at', fields.DatetimeField(auto_now=False, auto_now_add=False)),
                ('accepted_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('details', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ],
            options={'table': 'pb_org_invitations', 'app': 'models', 'pk_attr': 'id', 'table_description': "An invitation to join. Only the token's hash is stored."},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='OrgMember',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)),
                ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='members', on_delete=OnDelete.CASCADE)),
                ('user_id', fields.CharField(db_index=True, max_length=64)),
                ('email', fields.CharField(max_length=255)),
                ('name', fields.CharField(default='', max_length=200)),
                ('role', fields.CharField(default='developer', max_length=16)),
            ],
            options={'table': 'pb_org_members', 'app': 'models', 'unique_together': (('organization', 'user_id'),), 'pk_attr': 'id', 'table_description': 'An operator (an Akountz user of ``_platform``) in an organization.'},
            bases=['Model'],
        ),
        ops.AddField(
            model_name='AuditEntry',
            name='org',
            field=fields.CharField(null=True, db_index=True, max_length=63),
        ),
        ops.AddField(
            model_name='Project',
            name='organization',
            field=fields.ForeignKeyField('models.Organization', source_field='organization_id', null=True, db_constraint=True, to_field='id', related_name='projects', on_delete=OnDelete.RESTRICT),
        ),
    ]
