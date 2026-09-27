"""Everything a developer defines for an environment.

Definitions belong to one environment, so development can change without
touching production. ``POST …/promote`` copies them from one environment to
another.
"""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField


class _Definition(Model):
    id = fields.IntField(primary_key=True)
    name = fields.CharField(max_length=128)
    description = fields.TextField(default="")

    class Meta:
        abstract = True


class SchemaDef(_Definition):
    """A reusable schema: field definitions (see ``pawabase_kit.schemas``)."""

    environment = fields.ForeignKeyField("models.Environment", related_name="schemas", on_delete=fields.CASCADE)
    fields_ = AnyJSONField(default=list, source_field="fields")

    class Meta:
        table = "pb_schemas"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class TransformerDef(_Definition):
    """A reusable declarative transformer."""

    environment = fields.ForeignKeyField("models.Environment", related_name="transformers", on_delete=fields.CASCADE)
    definition = AnyJSONField(default=dict)

    class Meta:
        table = "pb_transformers"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class PolicyDef(_Definition):
    """A reusable policy condition."""

    environment = fields.ForeignKeyField("models.Environment", related_name="policies", on_delete=fields.CASCADE)
    condition = AnyJSONField(default=dict)

    class Meta:
        table = "pb_policies"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class Resource(_Definition):
    """An application entity and its backend behaviour.

    Attributes:
        table: The table in the environment's database.
        primary_key, id_type: How records are identified (``integer`` or ``uuid``).
        fields_: Field definitions.
        operations: Per-operation settings: ``{"list": {"enabled": true,
            "policy": "public"}, "create": {...}}``. Operations are off until enabled.
        relations: ``[{"name": "author", "resource": "users", "field": "author_id",
            "type": "belongs_to"}]``, used by ``?expand=``.
        transformer: A transformer name or inline definition for responses.
        cache_ttl: Seconds to cache reads (0 is off). Writes invalidate.
        rate_limit: ``{"limit": 100, "window": 60}`` per client, or empty.
        events: Publish ``<resource>.created|updated|deleted`` events.
        realtime: Publish changes to the ``resource:<name>`` channel.
        timestamps: Maintain ``created_at``/``updated_at`` columns.
        owner_field: Set this column to the caller's user id on create.
    """

    environment = fields.ForeignKeyField("models.Environment", related_name="resources", on_delete=fields.CASCADE)
    table = fields.CharField(max_length=128)
    primary_key = fields.CharField(max_length=64, default="id")
    id_type = fields.CharField(max_length=16, default="integer")
    fields_ = AnyJSONField(default=list, source_field="fields")
    operations = AnyJSONField(default=dict)
    relations = AnyJSONField(default=list)
    transformer = AnyJSONField(null=True)
    cache_ttl = fields.IntField(default=0)
    rate_limit = AnyJSONField(default=dict)
    events = fields.BooleanField(default=True)
    realtime = fields.BooleanField(default=False)
    timestamps = fields.BooleanField(default=True)
    owner_field = fields.CharField(max_length=64, null=True)
    tags = AnyJSONField(default=list)

    class Meta:
        table = "pb_resources"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class RouteDef(_Definition):
    """A custom API route served by a flow or a Python function."""

    environment = fields.ForeignKeyField("models.Environment", related_name="routes", on_delete=fields.CASCADE)
    method = fields.CharField(max_length=10, default="POST")
    path = fields.CharField(max_length=255)
    policy = AnyJSONField(null=True)
    input_fields = AnyJSONField(null=True)
    input_schema = fields.CharField(max_length=128, null=True)
    response_schema = fields.CharField(max_length=128, null=True)
    transformer = AnyJSONField(null=True)
    handler_type = fields.CharField(max_length=16, default="flow")
    handler = fields.CharField(max_length=128)
    rate_limit = AnyJSONField(default=dict)
    cache_ttl = fields.IntField(default=0)
    tags = AnyJSONField(default=list)
    enabled = fields.BooleanField(default=True)

    class Meta:
        table = "pb_routes"
        unique_together = (("environment", "method", "path"),)
        ordering = ["path"]


class Flow(_Definition):
    """A visual backend flow (``@xyflow/react`` nodes and edges)."""

    environment = fields.ForeignKeyField("models.Environment", related_name="flows", on_delete=fields.CASCADE)
    definition = AnyJSONField(default=dict)
    enabled = fields.BooleanField(default=True)
    timeout = fields.FloatField(default=60.0)
    record_runs = fields.BooleanField(default=True)

    class Meta:
        table = "pb_flows"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class Bucket(_Definition):
    """A storage bucket in the environment's storage.

    Attributes:
        public: Anyone may read (writes still follow ``write_policy``).
        read_policy, write_policy: Policy references; conditions see
            ``object.key`` and ``object.segments``.
        accepts: Content types accepted after sniffing (empty is anything).
        max_bytes: Largest object (0 is the environment's default).
    """

    environment = fields.ForeignKeyField("models.Environment", related_name="buckets", on_delete=fields.CASCADE)
    public = fields.BooleanField(default=False)
    read_policy = AnyJSONField(null=True)
    write_policy = AnyJSONField(null=True)
    accepts = AnyJSONField(default=list)
    max_bytes = fields.BigIntField(default=0)
    signed_uploads = fields.BooleanField(default=True)

    class Meta:
        table = "pb_buckets"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class MailTemplate(_Definition):
    """A stored email template, rendered in Jinja2's sandbox."""

    environment = fields.ForeignKeyField("models.Environment", related_name="mail_templates", on_delete=fields.CASCADE)
    subject = fields.CharField(max_length=255, default="")
    html = fields.TextField(default="")
    text = fields.TextField(default="")

    class Meta:
        table = "pb_mail_templates"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class EventSubscription(_Definition):
    """Connects events to consumers.

    Attributes:
        event: Name or pattern (``order.*``).
        target_type: ``flow``, ``function``, ``realtime`` (publish to a channel)
            or ``webhook`` (deliver to subscribed endpoints).
        target: The flow or function name, or the channel template.
        condition: Optional condition on ``event`` (``{"eq": ["$event.payload.status", "paid"]}``).
    """

    environment = fields.ForeignKeyField("models.Environment", related_name="subscriptions", on_delete=fields.CASCADE)
    event = fields.CharField(max_length=128)
    target_type = fields.CharField(max_length=16)
    target = fields.CharField(max_length=255, default="")
    condition = AnyJSONField(null=True)
    enabled = fields.BooleanField(default=True)

    class Meta:
        table = "pb_event_subscriptions"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class WebhookEndpoint(_Definition):
    """An outbound webhook: events POSTed, signed, to a URL."""

    environment = fields.ForeignKeyField("models.Environment", related_name="webhooks", on_delete=fields.CASCADE)
    url = fields.CharField(max_length=2048)
    events = AnyJSONField(default=list)
    secret_ciphertext = fields.TextField()
    headers = AnyJSONField(default=dict)
    enabled = fields.BooleanField(default=True)
    max_attempts = fields.IntField(default=5)

    class Meta:
        table = "pb_webhooks"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class InboundHook(_Definition):
    """An inbound webhook: a URL third parties call.

    Attributes:
        slug: The URL segment (``/hooks/v1/<slug>``).
        verification: ``none``, ``hmac-sha256`` or ``token``.
        signature_header: Where the signature (or token) arrives.
        target_type: ``event`` (publish ``target``) or ``flow``.
    """

    environment = fields.ForeignKeyField("models.Environment", related_name="inbound_hooks", on_delete=fields.CASCADE)
    slug = fields.CharField(max_length=128)
    verification = fields.CharField(max_length=20, default="hmac-sha256")
    signature_header = fields.CharField(max_length=128, default="x-signature")
    secret_ciphertext = fields.TextField(null=True)
    target_type = fields.CharField(max_length=16, default="event")
    target = fields.CharField(max_length=255)
    enabled = fields.BooleanField(default=True)
    received = fields.IntField(default=0)
    last_received_at = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_inbound_hooks"
        unique_together = (("environment", "slug"),)
        ordering = ["slug"]


class Schedule(_Definition):
    """Scheduled work, run by Sillo's scheduler in the scheduler process.

    Attributes:
        cron: A cron expression, or
        interval_seconds: an interval.
        target_type: ``flow``, ``function`` or ``event``.
        payload: Input for the flow or function, or the event payload.
    """

    environment = fields.ForeignKeyField("models.Environment", related_name="schedules", on_delete=fields.CASCADE)
    cron = fields.CharField(max_length=64, null=True)
    interval_seconds = fields.IntField(null=True)
    target_type = fields.CharField(max_length=16)
    target = fields.CharField(max_length=255)
    payload = AnyJSONField(null=True)
    enabled = fields.BooleanField(default=True)
    last_run_at = fields.DatetimeField(null=True)
    last_status = fields.CharField(max_length=20, null=True)
    run_count = fields.IntField(default=0)

    class Meta:
        table = "pb_schedules"
        unique_together = (("environment", "name"),)
        ordering = ["name"]
