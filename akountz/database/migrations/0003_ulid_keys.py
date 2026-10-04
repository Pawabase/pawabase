"""Every table keys on a ULID instead of an auto-increment integer.

Rebuilds the schema from the current models and carries every row across with
a new id, foreign keys rewritten (see ``pawabase_core.ulid_migration``).
Atomic: if anything fails the old tables are left as they were.

Users are re-keyed with ``legacy_ulid``, a fixed function of the old integer, not
a fresh random id: the platform API stores user ids as plain strings (members of
an organization, who created what), and its own migration maps the same
integers to the same ULIDs without having to read this database.
"""

from tortoise import migrations
from pawabase_core.ids import legacy_ulid
from pawabase_core.ulid_migration import UlidRebuild
from tortoise.migrations import operations as ops
import functools
from database.fields import AnyJSONField
from json import dumps, loads
from pawabase_core.ids import new_ulid
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise.fields.base import OnDelete
from tortoise import fields

# The schema after this migration: the models as they are now.
CREATES = [
    ops.CreateModel(
        name='AuthUser',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('email', fields.CharField(db_index=True, max_length=255)),
            ('username', fields.CharField(db_index=True, max_length=150)),
            ('password', fields.CharField(max_length=128)),
            ('is_active', fields.BooleanField(default=True)),
            ('is_staff', fields.BooleanField(default=False)),
            ('is_superuser', fields.BooleanField(default=False)),
            ('last_login', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('email_verified_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('project', fields.CharField(db_index=True, max_length=63)),
            ('env', fields.CharField(max_length=63)),
            ('name', fields.CharField(default='', max_length=200)),
            ('avatar_url', fields.CharField(null=True, max_length=2048)),
            ('phone', fields.CharField(null=True, max_length=32)),
            ('user_metadata', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ('app_metadata', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ('disabled_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('failed_logins', fields.IntField(default=0)),
            ('locked_until', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('mfa_enabled', fields.BooleanField(default=False)),
            ('last_sign_in_ip', fields.CharField(null=True, max_length=64)),
        ],
        options={'table': 'akz_users', 'app': 'models', 'unique_together': (('project', 'env', 'email'), ('project', 'env', 'username')), 'pk_attr': 'id', 'table_description': "A project environment's user."},
        bases=['PermissionMixin', 'UlidJWTUserMixin', 'UlidUserMixin', 'UserBaseModel'],
    ),
    ops.CreateModel(
        name='Group',
        fields=[
            ('created_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('name', fields.CharField(unique=True, db_index=True, max_length=150)),
            ('description', fields.TextField(null=True, unique=False)),
            ('modified_at', fields.DatetimeField(auto_now=True, auto_now_add=False)),
        ],
        options={'table': 'perm_groups', 'app': 'models', 'pk_attr': 'id'},
        bases=['Group'],
    ),
    ops.CreateModel(
        name='Identity',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='identities', on_delete=OnDelete.CASCADE)),
            ('project', fields.CharField(max_length=63)),
            ('env', fields.CharField(max_length=63)),
            ('provider', fields.CharField(max_length=64)),
            ('subject', fields.CharField(max_length=255)),
            ('email', fields.CharField(null=True, max_length=255)),
            ('email_verified', fields.BooleanField(default=False)),
            ('data', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ('last_sign_in_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
        ],
        options={'table': 'akz_identities', 'app': 'models', 'unique_together': (('project', 'env', 'provider', 'subject'),), 'pk_attr': 'id', 'table_description': 'An external identity (OAuth/OIDC) linked to a user.'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='JWTToken',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('user_id', fields.CharField(db_index=True, max_length=26)),
            ('token_jti', fields.CharField(unique=True, db_index=True, max_length=255)),
            ('token_family', fields.CharField(db_index=True, max_length=64)),
            ('token_type', fields.CharField(default='access', max_length=16)),
            ('expires_at', fields.DatetimeField(auto_now=False, auto_now_add=False)),
            ('consumed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('revoked', fields.BooleanField(default=False)),
        ],
        options={'table': 'jwt_tokens', 'app': 'models', 'pk_attr': 'id'},
        bases=['JWTToken'],
    ),
    ops.CreateModel(
        name='LoginEvent',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('project', fields.CharField(db_index=True, max_length=63)),
            ('env', fields.CharField(max_length=63)),
            ('user_id', fields.CharField(null=True, db_index=True, max_length=26)),
            ('email', fields.CharField(null=True, max_length=255)),
            ('kind', fields.CharField(max_length=32)),
            ('method', fields.CharField(default='password', max_length=32)),
            ('success', fields.BooleanField(default=True)),
            ('reason', fields.CharField(null=True, max_length=255)),
            ('ip', fields.CharField(null=True, max_length=64)),
            ('user_agent', fields.CharField(null=True, max_length=512)),
        ],
        options={'table': 'akz_login_events', 'app': 'models', 'pk_attr': 'id', 'table_description': 'Authentication history: sign-ins, failures, refreshes, sign-outs, resets.'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='MfaFactor',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='factors', on_delete=OnDelete.CASCADE)),
            ('kind', fields.CharField(default='totp', max_length=16)),
            ('secret_ciphertext', fields.TextField(unique=False)),
            ('confirmed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('last_used_step', fields.BigIntField(default=0)),
            ('friendly_name', fields.CharField(default='Authenticator app', max_length=100)),
        ],
        options={'table': 'akz_mfa_factors', 'app': 'models', 'pk_attr': 'id', 'table_description': 'A second factor. TOTP secrets are encrypted at rest.'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='OneTimeToken',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('project', fields.CharField(db_index=True, max_length=63)),
            ('env', fields.CharField(max_length=63)),
            ('purpose', fields.CharField(max_length=32)),
            ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', null=True, db_constraint=True, to_field='id', related_name='tokens', on_delete=OnDelete.CASCADE)),
            ('email', fields.CharField(null=True, max_length=255)),
            ('data', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ('expires_at', fields.DatetimeField(auto_now=False, auto_now_add=False)),
            ('used_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
        ],
        options={'table': 'akz_one_time_tokens', 'app': 'models', 'pk_attr': 'id', 'table_description': 'A single-use token for verification, recovery, magic links, invitations and MFA challenges.'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='Organization',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('project', fields.CharField(db_index=True, max_length=63)),
            ('env', fields.CharField(max_length=63)),
            ('slug', fields.CharField(max_length=63)),
            ('name', fields.CharField(max_length=200)),
            ('metadata', AnyJSONField(default=dict, encoder=functools.partial(dumps, separators=(',', ':')), decoder=loads)),
            ('created_by', fields.CharField(null=True, max_length=26)),
        ],
        options={'table': 'akz_organizations', 'app': 'models', 'unique_together': (('project', 'env', 'slug'),), 'pk_attr': 'id'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='Invitation',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='invitations', on_delete=OnDelete.CASCADE)),
            ('email', fields.CharField(max_length=255)),
            ('role', fields.CharField(default='member', max_length=32)),
            ('token_id', fields.CharField(max_length=32)),
            ('invited_by', fields.CharField(null=True, max_length=26)),
            ('accepted_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('expires_at', fields.DatetimeField(auto_now=False, auto_now_add=False)),
        ],
        options={'table': 'akz_invitations', 'app': 'models', 'pk_attr': 'id'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='Membership',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='memberships', on_delete=OnDelete.CASCADE)),
            ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='memberships', on_delete=OnDelete.CASCADE)),
            ('role', fields.CharField(default='member', max_length=32)),
        ],
        options={'table': 'akz_memberships', 'app': 'models', 'unique_together': (('organization', 'user'),), 'pk_attr': 'id'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='Permission',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('name', fields.CharField(unique=True, max_length=255)),
            ('description', fields.TextField(null=True, unique=False)),
        ],
        options={'table': 'permissions', 'app': 'models', 'pk_attr': 'id'},
        bases=['Permission'],
    ),
    ops.CreateModel(
        name='GroupPermission',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('group', fields.ForeignKeyField('models.Group', source_field='group_id', db_constraint=True, to_field='id', related_name='group_permissions', on_delete=OnDelete.CASCADE)),
            ('permission', fields.ForeignKeyField('models.Permission', source_field='permission_id', db_constraint=True, to_field='id', related_name='group_permissions', on_delete=OnDelete.CASCADE)),
        ],
        options={'table': 'perm_group_permissions', 'app': 'models', 'unique_together': (('group', 'permission'),), 'pk_attr': 'id'},
        bases=['GroupPermission'],
    ),
    ops.CreateModel(
        name='RecoveryCode',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='recovery_codes', on_delete=OnDelete.CASCADE)),
            ('code_hash', fields.CharField(db_index=True, max_length=128)),
            ('used_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
        ],
        options={'table': 'akz_recovery_codes', 'app': 'models', 'pk_attr': 'id', 'table_description': 'A single-use MFA recovery code, stored as a SHA-256 hash.'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='SessionInfo',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('family', fields.CharField(primary_key=True, unique=True, db_index=True, max_length=64)),
            ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='sessions', on_delete=OnDelete.CASCADE)),
            ('project', fields.CharField(db_index=True, max_length=63)),
            ('env', fields.CharField(max_length=63)),
            ('method', fields.CharField(max_length=32)),
            ('aal', fields.CharField(default='aal1', max_length=8)),
            ('org', fields.CharField(null=True, max_length=63)),
            ('ip', fields.CharField(null=True, max_length=64)),
            ('user_agent', fields.CharField(null=True, max_length=512)),
            ('last_refreshed_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
            ('revoked_at', fields.DatetimeField(null=True, auto_now=False, auto_now_add=False)),
        ],
        options={'table': 'akz_sessions', 'app': 'models', 'pk_attr': 'family', 'table_description': 'What Pawabase knows about one token family (a session).'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='Team',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('organization', fields.ForeignKeyField('models.Organization', source_field='organization_id', db_constraint=True, to_field='id', related_name='teams', on_delete=OnDelete.CASCADE)),
            ('slug', fields.CharField(max_length=63)),
            ('name', fields.CharField(max_length=200)),
        ],
        options={'table': 'akz_teams', 'app': 'models', 'unique_together': (('organization', 'slug'),), 'pk_attr': 'id'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='TeamMember',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('team', fields.ForeignKeyField('models.Team', source_field='team_id', db_constraint=True, to_field='id', related_name='members', on_delete=OnDelete.CASCADE)),
            ('user', fields.ForeignKeyField('models.AuthUser', source_field='user_id', db_constraint=True, to_field='id', related_name='team_memberships', on_delete=OnDelete.CASCADE)),
        ],
        options={'table': 'akz_team_members', 'app': 'models', 'unique_together': (('team', 'user'),), 'pk_attr': 'id'},
        bases=['Model'],
    ),
    ops.CreateModel(
        name='TokenBlacklist',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('token_jti', fields.CharField(unique=True, db_index=True, max_length=512)),
            ('blacklisted_at', fields.DatetimeField(auto_now=False, auto_now_add=True)),
            ('expires_at', fields.DatetimeField(auto_now=False, auto_now_add=False)),
        ],
        options={'table': 'token_blacklist', 'app': 'models', 'pk_attr': 'id'},
        bases=['TokenBlacklist'],
    ),
    ops.CreateModel(
        name='UserGroup',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('user_id', fields.CharField(db_index=True, max_length=255)),
            ('group', fields.ForeignKeyField('models.Group', source_field='group_id', db_constraint=True, to_field='id', related_name='memberships', on_delete=OnDelete.CASCADE)),
        ],
        options={'table': 'perm_user_groups', 'app': 'models', 'unique_together': (('user_id', 'group'),), 'pk_attr': 'id'},
        bases=['UserGroup'],
    ),
    ops.CreateModel(
        name='UserPermission',
        fields=[
            ('created_at', CreatedAtField()),
            ('updated_at', UpdatedAtField()),
            ('deleted_at', SoftDeleteField(null=True)),
            ('id', fields.CharField(primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26)),
            ('user_id', fields.CharField(db_index=True, max_length=255)),
            ('permission', fields.ForeignKeyField('models.Permission', source_field='permission_id', db_constraint=True, to_field='id', on_delete=OnDelete.CASCADE)),
        ],
        options={'table': 'user_permissions', 'app': 'models', 'pk_attr': 'id'},
        bases=['UserPermission'],
    ),
]

# Columns that hold a user's integer id without being a foreign key.
USER_REFERENCES = {
    'jwt_tokens': {'user_id': 'akz_users'},
    'akz_login_events': {'user_id': 'akz_users'},
    'akz_organizations': {'created_by': 'akz_users'},
    'akz_invitations': {'invited_by': 'akz_users'},
    'user_permissions': {'user_id': 'akz_users'},
    'perm_user_groups': {'user_id': 'akz_users'},
}


class Migration(migrations.Migration):
    dependencies = [('models', '0002_session_org')]

    initial = False

    operations = UlidRebuild(
        CREATES, fixed={'akz_users': legacy_ulid}, references=USER_REFERENCES
    ).operations()
