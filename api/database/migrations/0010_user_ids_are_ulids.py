"""Rewrite the user ids this database stores as text to the ULIDs Akountz now uses.

Akountz keyed users by integer, and the platform stored those as strings:
who is a member of an organization, who created a project, who an audit entry
or a request is attributed to. Akountz's migration gives user *N* the fixed id
``legacy_ulid(N)``, so this one computes the same ids from the same integers,
without reading Akountz's database. Only values that are digits and nothing
else change; an email or a service name in the same column is left alone.
"""

from pawabase_core.ids import legacy_ulid
from tortoise import migrations
from tortoise.migrations import operations as ops

#: Columns that hold a user's id as text.
USER_COLUMNS = (
    ("pb_org_members", "user_id"),
    ("pb_organizations", "created_by"),
    ("pb_org_invitations", "invited_by"),
    ("pb_projects", "created_by"),
    ("pb_project_keys", "created_by"),
    ("pb_secrets", "updated_by"),
    ("pb_audit", "actor"),
    ("pb_definition_revisions", "created_by"),
    ("pb_releases", "created_by"),
    ("pb_deployments", "actor"),
    ("pb_function_deployments", "created_by"),
    ("pb_event_log", "actor"),
    ("pb_request_logs", "user"),
)


async def rewrite_user_ids(apps, editor):
    client = editor.client
    dialect = client.capabilities.dialect
    quote = (lambda n: f"`{n}`") if dialect == "mysql" else (lambda n: f'"{n}"')
    for table, column in USER_COLUMNS:
        try:
            found = await client.execute_query_dict(
                f"SELECT DISTINCT {quote(column)} AS value FROM {quote(table)} WHERE {quote(column)} IS NOT NULL"
            )
        except Exception:  # a table this deployment never had
            continue
        for row in found:
            value = row["value"]
            if isinstance(value, str) and value.isdigit():
                marks = ("$1", "$2") if dialect == "postgres" else ("?", "?")
                await client.execute_query(
                    f"UPDATE {quote(table)} SET {quote(column)} = {marks[0]} WHERE {quote(column)} = {marks[1]}",
                    [legacy_ulid(int(value)), value],
                )


class Migration(migrations.Migration):
    dependencies = [("models", "0009_ulid_keys")]

    initial = False

    operations = [ops.RunPython(rewrite_user_ids)]
