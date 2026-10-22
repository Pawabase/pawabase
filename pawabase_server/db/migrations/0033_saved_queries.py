from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from pawabase_server.core.ids import new_ulid


class Migration(migrations.Migration):
    """Saved SQL queries for Studio's SQL editor."""

    dependencies = [("models", "0032_mail_studio")]

    operations = [
        ops.CreateModel(
            name="SavedQuery",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True,
                        default=new_ulid,
                        unique=True,
                        db_index=True,
                        max_length=26,
                    ),
                ),
                ("env", fields.CharField(max_length=63, db_index=True)),
                ("name", fields.CharField(max_length=120)),
                ("description", fields.CharField(max_length=500, default="")),
                ("sql", fields.TextField(unique=False)),
                ("updated_by", fields.CharField(max_length=255, null=True)),
            ],
            options={
                "table": "pb_saved_queries",
                "app": "models",
                "unique_together": (("env", "name"),),
                "pk_attr": "id",
                "table_description": "A SQL query kept in Studio's SQL editor.",
            },
            bases=["Model"],
        ),
    ]
