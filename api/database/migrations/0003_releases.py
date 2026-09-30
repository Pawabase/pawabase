import functools
from json import dumps, loads

from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise import fields, migrations
from tortoise.fields.base import OnDelete
from tortoise.migrations import operations as ops

from database.fields import AnyJSONField


def json_field(default):
    return AnyJSONField(
        default=default,
        encoder=functools.partial(dumps, separators=(",", ":")),
        decoder=loads,
    )


def base_fields(pk):
    return [
        ("created_at", CreatedAtField()),
        ("updated_at", UpdatedAtField()),
        ("deleted_at", SoftDeleteField(null=True)),
        ("id", pk),
    ]


def env_field(related_name):
    return fields.ForeignKeyField(
        "models.Environment",
        source_field="environment_id",
        db_constraint=True,
        to_field="id",
        related_name=related_name,
        on_delete=OnDelete.CASCADE,
    )


class Migration(migrations.Migration):
    dependencies = [("models", "0002_request_logs")]

    operations = [
        ops.CreateModel(
            name="Branch",
            fields=base_fields(
                fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)
            )
            + [
                ("name", fields.CharField(max_length=63)),
                ("head_revision_id", fields.CharField(max_length=32, null=True)),
                ("protected", fields.BooleanField(default=False)),
                ("environment", env_field("branches")),
            ],
            options={
                "table": "pb_branches",
                "app": "models",
                "unique_together": (("environment", "name"),),
                "ordering": ["name"],
                "pk_attr": "id",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="DefinitionRevision",
            fields=base_fields(
                fields.CharField(max_length=32, primary_key=True, unique=True, db_index=True)
            )
            + [
                ("number", fields.IntField()),
                ("branch", fields.CharField(max_length=63, default="main")),
                ("parent_revision_id", fields.CharField(max_length=32, null=True)),
                ("checksum", fields.CharField(max_length=64, db_index=True)),
                ("snapshot", json_field(dict)),
                ("status", fields.CharField(max_length=16, default="valid")),
                ("problems", json_field(list)),
                ("message", fields.TextField(default="")),
                ("created_by", fields.CharField(max_length=255, null=True)),
                ("environment", env_field("revisions")),
            ],
            options={
                "table": "pb_definition_revisions",
                "app": "models",
                "unique_together": (("environment", "number"),),
                "ordering": ["-number"],
                "pk_attr": "id",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="ApiVersion",
            fields=base_fields(
                fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)
            )
            + [
                ("name", fields.CharField(max_length=32)),
                ("status", fields.CharField(max_length=16, default="draft")),
                ("is_default", fields.BooleanField(default=False)),
                ("active_release_id", fields.CharField(max_length=32, null=True)),
                ("previous_release_id", fields.CharField(max_length=32, null=True)),
                ("sunset_at", fields.DatetimeField(null=True)),
                ("environment", env_field("api_versions")),
            ],
            options={
                "table": "pb_api_versions",
                "app": "models",
                "unique_together": (("environment", "name"),),
                "ordering": ["name"],
                "pk_attr": "id",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="Release",
            fields=base_fields(
                fields.CharField(max_length=32, primary_key=True, unique=True, db_index=True)
            )
            + [
                ("revision_id", fields.CharField(max_length=32, db_index=True)),
                ("api_version", fields.CharField(max_length=32, db_index=True)),
                ("name", fields.CharField(max_length=128)),
                ("status", fields.CharField(max_length=16, default="ready")),
                ("notes", fields.TextField(default="")),
                ("compatibility", json_field(dict)),
                ("created_by", fields.CharField(max_length=255, null=True)),
                ("deployed_at", fields.DatetimeField(null=True)),
                ("environment", env_field("releases")),
            ],
            options={
                "table": "pb_releases",
                "app": "models",
                "ordering": ["-created_at"],
                "pk_attr": "id",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="Deployment",
            fields=base_fields(
                fields.IntField(generated=True, primary_key=True, unique=True, db_index=True)
            )
            + [
                ("api_version", fields.CharField(max_length=32, db_index=True)),
                ("release_id", fields.CharField(max_length=32)),
                ("previous_release_id", fields.CharField(max_length=32, null=True)),
                ("action", fields.CharField(max_length=16)),
                ("status", fields.CharField(max_length=16, default="succeeded")),
                ("actor", fields.CharField(max_length=255, null=True)),
                ("details", json_field(dict)),
                ("environment", env_field("deployments")),
            ],
            options={
                "table": "pb_deployments",
                "app": "models",
                "ordering": ["-id"],
                "pk_attr": "id",
            },
            bases=["Model"],
        ),
    ]
