from tortoise import migrations
from tortoise.migrations import operations as ops
from tortoise.migrations.constraints import UniqueConstraint

async def require_empty_runtime(apps, editor):
    """Users are now scoped to an environment alone; users of the old per-project layout are not converted.

    The operators Studio used to sign in (the reserved ``_platform`` project) are no longer needed and are removed.
    """
    quote = (lambda n: f"`{n}`") if editor.client.capabilities.dialect == "mysql" else (lambda n: f'"{n}"')
    users, groups, permissions = quote("akz_users"), quote("perm_groups"), quote("permissions")
    await editor.client.execute_query(f"DELETE FROM {users} WHERE {quote('project')} = '_platform'")
    await editor.client.execute_query(f"DELETE FROM {groups} WHERE SUBSTR({quote('name')}, 1, 10) = '_platform/'")
    await editor.client.execute_query(f"DELETE FROM {permissions} WHERE SUBSTR({quote('name')}, 1, 10) = '_platform/'")
    rows = await editor.client.execute_query_dict(f"SELECT COUNT(*) AS n FROM {users}")
    if rows and rows[0]["n"]:
        raise RuntimeError(
            "This database holds users of an installation that managed many projects. A Pawabase installation is now "
            "one project, and the old users are not converted. Back up what you need on the old version, then start "
            "this version from an empty database: docker compose down -v, then docker compose up -d."
        )


class Migration(migrations.Migration):
    dependencies = [('models', '0003_ulid_keys')]

    initial = False

    operations = [
        ops.RunPython(require_empty_runtime),
        ops.RemoveConstraint(
            model_name='AuthUser',
            name=None,
            fields=['project', 'env', 'email'],
        ),
        ops.RemoveConstraint(
            model_name='AuthUser',
            name=None,
            fields=['project', 'env', 'username'],
        ),
        ops.AlterModelOptions(
            name='AuthUser',
            options={'table': 'akz_users', 'app': 'models', 'unique_together': (('env', 'email'), ('env', 'username')), 'pk_attr': 'id', 'table_description': "An environment's user."},
        ),
        ops.RemoveField(model_name='AuthUser', name='project'),
        ops.AddConstraint(
            model_name='AuthUser',
            constraint=UniqueConstraint(fields=('env', 'email'), name=None),
        ),
        ops.AddConstraint(
            model_name='AuthUser',
            constraint=UniqueConstraint(fields=('env', 'username'), name=None),
        ),
        ops.RemoveConstraint(
            model_name='Identity',
            name=None,
            fields=['project', 'env', 'provider', 'subject'],
        ),
        ops.RemoveField(model_name='Identity', name='project'),
        ops.AddConstraint(
            model_name='Identity',
            constraint=UniqueConstraint(fields=('env', 'provider', 'subject'), name=None),
        ),
        ops.RemoveField(model_name='LoginEvent', name='project'),
        ops.RemoveField(model_name='OneTimeToken', name='project'),
        ops.RemoveConstraint(
            model_name='Organization',
            name=None,
            fields=['project', 'env', 'slug'],
        ),
        ops.AlterModelOptions(
            name='Organization',
            options={'table': 'akz_organizations', 'app': 'models', 'unique_together': (('env', 'slug'),), 'pk_attr': 'id', 'table_description': "An organization **your application's users** create and belong to (a company account, a workspace)."},
        ),
        ops.RemoveField(model_name='Organization', name='project'),
        ops.AddConstraint(
            model_name='Organization',
            constraint=UniqueConstraint(fields=('env', 'slug'), name=None),
        ),
        ops.RemoveField(model_name='SessionInfo', name='project'),
    ]
