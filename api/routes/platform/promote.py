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
from pawabase_core.crypto import SecretBox
from pawabase_core.records import upsert

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
    source: Environment,
    target: Environment,
    *,
    include: list[str] | None = None,
    box: SecretBox | None = None,
) -> dict[str, int]:
    """Upsert *source*'s definitions into *target*. Returns counts per kind.

    A webhook or inbound hook carries its signing secret as ciphertext. With a *box* it is
    re-sealed for *target*, because a value sealed for one environment does not open in another;
    without one it is copied as it is, which is only right for values sealed before environments
    had keys of their own.
    """
    counts: dict[str, int] = {}
    for kind, (model, natural) in KINDS.items():
        if include is not None and kind not in include:
            continue
        count = 0
        for item in await model.filter(environment_id=source.id):
            values = {
                name: getattr(item, name)
                for name in model._meta.fields_map
                # Real columns only: a webhook's `deliveries` is the other side of a relation
                # and cannot be set when a row is created.
                if name in model._meta.db_fields and name not in SKIP and name != "environment"
            }
            if box is not None and values.get("secret_ciphertext"):
                try:
                    values["secret_ciphertext"] = box.reseal(
                        values["secret_ciphertext"], source=source.name, target=target.name
                    )
                except Exception:  # unreadable under this master key: carry it over unchanged
                    pass
            lookup = {name: values.pop(name) for name in natural}
            await upsert(model, values, environment_id=target.id, **lookup)
            count += 1
        counts[kind] = count
    return counts
