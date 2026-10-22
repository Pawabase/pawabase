import functools
from json import dumps, loads

from sillo.record.fields import CreatedAtField, SoftDeleteField, UpdatedAtField
from tortoise import fields, migrations
from tortoise.migrations import operations as ops

from pawabase_server.core.ids import new_ulid
from pawabase_server.db.fields import AnyJSONField

JSON = dict(encoder=functools.partial(dumps, separators=(",", ":")), decoder=loads)


def ulid():
    return fields.CharField(
        primary_key=True, default=new_ulid, unique=True, db_index=True, max_length=26
    )


def stamps():
    return [
        ("created_at", CreatedAtField()),
        ("updated_at", UpdatedAtField()),
        ("deleted_at", SoftDeleteField(null=True)),
    ]


class Migration(migrations.Migration):
    """Mail in Studio: drafts, designs and versions for templates; stored content, scheduling and retries for sent mail; a shared brand kit."""

    dependencies = [("models", "0031_dashboards")]

    operations = [
        # Templates. Existing templates were written as HTML and are live, so they start out published.
        ops.AddField(
            model_name="MailTemplate",
            name="preheader",
            field=fields.CharField(max_length=255, default="", null=True),
        ),
        ops.RunSQL("UPDATE pb_mail_templates SET preheader = '' WHERE preheader IS NULL"),
        ops.AlterField(
            model_name="MailTemplate",
            name="preheader",
            field=fields.CharField(max_length=255, default=""),
        ),
        ops.AddField(
            model_name="MailTemplate", name="design", field=AnyJSONField(null=True, **JSON)
        ),
        ops.AddField(
            model_name="MailTemplate", name="draft", field=AnyJSONField(null=True, **JSON)
        ),
        ops.AddField(
            model_name="MailTemplate",
            name="status",
            field=fields.CharField(max_length=12, default="published", null=True),
        ),
        ops.RunSQL("UPDATE pb_mail_templates SET status = 'published' WHERE status IS NULL"),
        ops.AlterField(
            model_name="MailTemplate",
            name="status",
            field=fields.CharField(max_length=12, default="published"),
        ),
        ops.AddField(
            model_name="MailTemplate",
            name="category",
            field=fields.CharField(max_length=32, default="", null=True),
        ),
        ops.RunSQL("UPDATE pb_mail_templates SET category = '' WHERE category IS NULL"),
        ops.AlterField(
            model_name="MailTemplate",
            name="category",
            field=fields.CharField(max_length=32, default=""),
        ),
        ops.AddField(model_name="MailTemplate", name="tags", field=AnyJSONField(null=True, **JSON)),
        ops.RunSQL("UPDATE pb_mail_templates SET tags = '[]' WHERE tags IS NULL"),
        ops.AlterField(
            model_name="MailTemplate", name="tags", field=AnyJSONField(default=list, **JSON)
        ),
        ops.AddField(
            model_name="MailTemplate",
            name="folder",
            field=fields.CharField(max_length=64, default="", null=True),
        ),
        ops.RunSQL("UPDATE pb_mail_templates SET folder = '' WHERE folder IS NULL"),
        ops.AlterField(
            model_name="MailTemplate",
            name="folder",
            field=fields.CharField(max_length=64, default=""),
        ),
        ops.AddField(
            model_name="MailTemplate",
            name="favorite",
            field=fields.BooleanField(default=False, null=True),
        ),
        ops.RunSQL("UPDATE pb_mail_templates SET favorite = false WHERE favorite IS NULL"),
        ops.AlterField(
            model_name="MailTemplate", name="favorite", field=fields.BooleanField(default=False)
        ),
        ops.AddField(
            model_name="MailTemplate", name="variables", field=AnyJSONField(null=True, **JSON)
        ),
        ops.RunSQL("UPDATE pb_mail_templates SET variables = '[]' WHERE variables IS NULL"),
        ops.AlterField(
            model_name="MailTemplate", name="variables", field=AnyJSONField(default=list, **JSON)
        ),
        ops.AddField(
            model_name="MailTemplate", name="published_at", field=fields.DatetimeField(null=True)
        ),
        # Sent mail.
        ops.AddField(
            model_name="MailLog",
            name="from_email",
            field=fields.CharField(max_length=255, null=True),
        ),
        ops.AddField(
            model_name="MailLog", name="reply_to", field=fields.CharField(max_length=255, null=True)
        ),
        ops.AddField(model_name="MailLog", name="cc", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(model_name="MailLog", name="bcc", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(model_name="MailLog", name="html", field=fields.TextField(null=True)),
        ops.AddField(model_name="MailLog", name="text", field=fields.TextField(null=True)),
        ops.AddField(model_name="MailLog", name="data", field=AnyJSONField(null=True, **JSON)),
        ops.AddField(
            model_name="MailLog", name="attachments", field=AnyJSONField(null=True, **JSON)
        ),
        ops.AddField(
            model_name="MailLog", name="provider_response", field=AnyJSONField(null=True, **JSON)
        ),
        ops.AddField(
            model_name="MailLog", name="attempts", field=fields.IntField(default=1, null=True)
        ),
        ops.RunSQL("UPDATE pb_mail_log SET attempts = 1 WHERE attempts IS NULL"),
        ops.AlterField(model_name="MailLog", name="attempts", field=fields.IntField(default=1)),
        ops.AddField(
            model_name="MailLog", name="retry_of", field=fields.CharField(max_length=26, null=True)
        ),
        ops.AddField(
            model_name="MailLog", name="scheduled_at", field=fields.DatetimeField(null=True)
        ),
        ops.AddField(model_name="MailLog", name="sent_at", field=fields.DatetimeField(null=True)),
        ops.CreateModel(
            name="MailTemplateVersion",
            fields=[
                *stamps(),
                ("id", ulid()),
                ("env", fields.CharField(max_length=63)),
                ("template", fields.CharField(max_length=128)),
                ("number", fields.IntField()),
                ("subject", fields.CharField(max_length=255, default="")),
                ("preheader", fields.CharField(max_length=255, default="")),
                ("html", fields.TextField(default="", unique=False)),
                ("text", fields.TextField(default="", unique=False)),
                ("design", AnyJSONField(null=True, **JSON)),
                ("note", fields.CharField(max_length=255, default="")),
            ],
            options={
                "table": "pb_mail_template_versions",
                "app": "models",
                "unique_together": (("env", "template", "number"),),
                "pk_attr": "id",
                "table_description": "One published version of a mail template.",
            },
            bases=["Model"],
        ),
        ops.CreateModel(
            name="MailKit",
            fields=[
                *stamps(),
                ("id", ulid()),
                ("env", fields.CharField(max_length=63, unique=True)),
                ("brand", AnyJSONField(default=dict, **JSON)),
                ("palettes", AnyJSONField(default=list, **JSON)),
                ("blocks", AnyJSONField(default=list, **JSON)),
                ("folders", AnyJSONField(default=list, **JSON)),
            ],
            options={
                "table": "pb_mail_kits",
                "app": "models",
                "pk_attr": "id",
                "table_description": "An environment's shared email styling.",
            },
            bases=["Model"],
        ),
    ]
