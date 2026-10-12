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
    dependencies = [('models', '0015_schema_history')]

    initial = False

    operations = [
        ops.CreateModel(
            name='Device',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='devices', on_delete=OnDelete.CASCADE)),
                ('env', fields.CharField(db_index=True, max_length=63)),
                ('key', fields.CharField(db_index=True, max_length=64)),
                ('label', fields.CharField(default='', max_length=120)),
                ('user_agent', fields.CharField(null=True, max_length=512)),
                ('first_ip', fields.CharField(null=True, max_length=64)),
                ('last_ip', fields.CharField(null=True, max_length=64)),
                ('last_seen_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('sign_ins', fields.IntField(default=1)),
                ('trusted_until', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ],
            options={'table': 'akz_devices', 'app': 'models', 'unique_together': (('user', 'key'),), 'pk_attr': 'id', 'table_description': 'A browser or app a person has signed in from.'},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='PasswordHistory',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='password_history', on_delete=OnDelete.CASCADE)),
                ('env', fields.CharField(max_length=63)),
                ('password_hash', fields.CharField(max_length=256)),
            ],
            options={'table': 'akz_password_history', 'app': 'models', 'pk_attr': 'id', 'table_description': "The hashes of a user's previous passwords, so the directory can refuse reusing the last few."},
            bases=['Model'],
        ),
        ops.CreateModel(
            name='UserApiKey',
            fields=[
                ('created_at', CreatedAtField()),
                ('updated_at', UpdatedAtField()),
                ('deleted_at', SoftDeleteField(null=True)),
                ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
                ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='api_keys', on_delete=OnDelete.CASCADE)),
                ('env', fields.CharField(db_index=True, max_length=63)),
                ('name', fields.CharField(max_length=120)),
                ('prefix', fields.CharField(max_length=24)),
                ('key_hash', fields.CharField(unique=True, db_index=True, max_length=128)),
                ('scopes', AnyJSONField(default=list, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
                ('org', fields.CharField(null=True, max_length=63)),
                ('expires_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('last_used_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
                ('last_ip', fields.CharField(null=True, max_length=64)),
                ('use_count', fields.IntField(default=0)),
                ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ],
            options={'table': 'akz_user_api_keys', 'app': 'models', 'pk_attr': 'id', 'table_description': 'A key a person creates to call the API as themselves, without signing in.'},
            bases=['Model'],
        ),
        ops.AddField(
            model_name='AuthUser',
            name='password_changed_at',
            field=fields.DatetimeField(null=True, description="When the password was last set, for the directory's password-age rule. Null: never recorded (treated as not expired).", auto_now=False, auto_now_add=False),
        ),
    ]
