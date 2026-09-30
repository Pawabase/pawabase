"""Copying definitions between environments (development → production)."""

from __future__ import annotations

from typing import Any

from database.models import (
    Bucket,
    Environment,
    EventSubscription,
    Flow,
    InboundHook,
    MailTemplate,
    PolicyDef,
    Resource,
    RouteDef,
    Schedule,
    SchemaDef,
    TransformerDef,
    WebhookEndpoint,
)
from pawabase_kit.records import upsert

#: Kind name → (model, natural key fields). Secrets and keys are never copied:
#: they belong to the environment they were created in.
KINDS: dict[str, tuple[Any, tuple[str, ...]]] = {
    "schemas": (SchemaDef, ("name",)),
    "transformers": (TransformerDef, ("name",)),
    "policies": (PolicyDef, ("name",)),
    "resources": (Resource, ("name",)),
    "routes": (RouteDef, ("method", "path")),
    "flows": (Flow, ("name",)),
    "buckets": (Bucket, ("name",)),
    "mail_templates": (MailTemplate, ("name",)),
    "subscriptions": (EventSubscription, ("name",)),
    "webhooks": (WebhookEndpoint, ("name",)),
    "inbound_hooks": (InboundHook, ("slug",)),
    "schedules": (Schedule, ("name",)),
}

SKIP = {
    "id",
    "environment_id",
    "created_at",
    "updated_at",
    "deleted_at",
    "received",
    "last_received_at",
    "last_run_at",
    "last_status",
    "run_count",
}


async def copy_definitions(
    source: Environment, target: Environment, *, include: list[str] | None = None
) -> dict[str, int]:
    """Upsert *source*'s definitions into *target*. Returns counts per kind."""
    counts: dict[str, int] = {}
    for kind, (model, natural) in KINDS.items():
        if include is not None and kind not in include:
            continue
        count = 0
        for item in await model.filter(environment_id=source.id):
            values = {
                name: getattr(item, name)
                for name in model._meta.fields_map
                if name not in SKIP and name != "environment"
            }
            lookup = {name: values.pop(name) for name in natural}
            await upsert(model, values, environment_id=target.id, **lookup)
            count += 1
        counts[kind] = count
    return counts
