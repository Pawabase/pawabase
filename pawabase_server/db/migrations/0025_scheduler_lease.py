from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField


class Migration(migrations.Migration):
    dependencies = [("models", "0024_usage_counters")]

    operations = [
        ops.AddField(model_name="Schedule", name="timezone", field=fields.CharField(max_length=64, null=True)),
        # Added nullable, filled, then made required: existing schedules have no value yet.
        ops.AddField(model_name="Schedule", name="overlap", field=fields.CharField(max_length=8, null=True)),
        ops.RunSQL("UPDATE pb_schedules SET overlap = 'allow' WHERE overlap IS NULL"),
        ops.AlterField(model_name="Schedule", name="overlap", field=fields.CharField(max_length=8, default="allow")),
        ops.AddField(model_name="Schedule", name="catch_up", field=fields.BooleanField(null=True)),
        ops.RunSQL("UPDATE pb_schedules SET catch_up = FALSE WHERE catch_up IS NULL"),
        ops.AlterField(model_name="Schedule", name="catch_up", field=fields.BooleanField(default=False)),
        ops.CreateModel(
            name="Lease",
            fields=[
                ("created_at", CreatedAtField()),
                ("updated_at", UpdatedAtField()),
                ("deleted_at", SoftDeleteField(null=True)),
                ("name", fields.CharField(primary_key=True, max_length=64)),
                ("holder", fields.CharField(max_length=200)),
                ("expires_at", fields.DatetimeField()),
            ],
            options={"table": "pb_leases", "app": "models", "pk_attr": "name", "table_description": "A named lease one process holds at a time."},
            bases=["Model"],
        ),
    ]
