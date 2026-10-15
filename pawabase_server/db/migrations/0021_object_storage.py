import functools
from json import dumps, loads

from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from pawabase_server.core.ids import new_ulid
from pawabase_server.db.fields import AnyJSONField
from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField

JSON = dict(encoder=functools.partial(dumps, separators=(",", ":")), decoder=loads)


class Migration(migrations.Migration):
    dependencies = [("models", "0020_bucket_signed_downloads")]

    operations = [
        # Per-action policies: null keeps the behaviour a bucket had before they existed.
        ops.AddField(model_name="Bucket", name="list_policy", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(model_name="Bucket", name="update_policy", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(model_name="Bucket", name="delete_policy", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(model_name="Bucket", name="transforms", field=AnyJSONField(null=True, **JSON)),
        ops.RunSQL("UPDATE pb_buckets SET transforms = '{}' WHERE transforms IS NULL"),
        ops.AlterField(
            model_name="Bucket", name="transforms", field=AnyJSONField(default=dict, **JSON)
        ),
        ops.AddField(model_name="Bucket", name="cache_max_age", field=fields.IntField(null=True)),
        ops.RunSQL("UPDATE pb_buckets SET cache_max_age = 0 WHERE cache_max_age IS NULL"),
        ops.AlterField(
            model_name="Bucket", name="cache_max_age", field=fields.IntField(default=0)
        ),
        ops.CreateModel(
            name="ObjectShare",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                (
                    "id",
                    fields.CharField(
                        primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26
                    ),
                ),
                ("env", fields.CharField(db_index=True, max_length=63)),
                ("bucket", fields.CharField(max_length=63)),
                ("key", fields.CharField(max_length=1024)),
                ("token_hash", fields.CharField(unique=True, max_length=64)),
                ("permissions", fields.CharField(default="both", max_length=12)),
                ("label", fields.CharField(default="", max_length=120)),
                ("expires_at", fields.DatetimeField()),
                ("revoked_at", fields.DatetimeField(null=True)),
                ("created_by", fields.CharField(default="", max_length=120)),
                ("hits", fields.IntField(default=0)),
                ("last_used_at", fields.DatetimeField(null=True)),
            ],
            options={
                "table": "pb_object_shares",
                "app": "models",
                "pk_attr": "id",
                "table_description": "A temporary link to one object.",
            },
            bases=["Model"],
        ),
    ]
