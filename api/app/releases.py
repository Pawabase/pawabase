"""Snapshot, validate, compare, and materialize immutable releases."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from typing import Any

from app.data.store import ResourceSpec
from app.state import EnvironmentState
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
    Secret,
    TransformerDef,
    WebhookEndpoint,
)
from pawabase_core.policies import Policy, PolicyEngine, python_policies
from pawabase_core.schemas import compile_schemas

DEFINITIONS: dict[str, tuple[type, tuple[str, ...]]] = {
    "resources": (
        Resource,
        (
            "id",
            "name",
            "description",
            "table",
            "primary_key",
            "id_type",
            "fields_",
            "operations",
            "relations",
            "transformer",
            "cache_ttl",
            "rate_limit",
            "events",
            "realtime",
            "timestamps",
            "owner_field",
            "tags",
        ),
    ),
    "routes": (
        RouteDef,
        (
            "id",
            "name",
            "description",
            "method",
            "path",
            "policy",
            "input_fields",
            "input_schema",
            "response_schema",
            "transformer",
            "handler_type",
            "handler",
            "rate_limit",
            "cache_ttl",
            "tags",
            "enabled",
        ),
    ),
    "flows": (
        Flow,
        ("id", "name", "description", "definition", "enabled", "timeout", "record_runs"),
    ),
    "schemas": (SchemaDef, ("id", "name", "description", "fields_")),
    "transformers": (TransformerDef, ("id", "name", "description", "definition")),
    "policies": (PolicyDef, ("id", "name", "description", "condition")),
    "buckets": (
        Bucket,
        (
            "id",
            "name",
            "description",
            "public",
            "read_policy",
            "write_policy",
            "accepts",
            "max_bytes",
            "signed_uploads",
        ),
    ),
    "mail_templates": (MailTemplate, ("id", "name", "description", "subject", "html", "text")),
    "subscriptions": (
        EventSubscription,
        ("id", "name", "description", "event", "target_type", "target", "condition", "enabled"),
    ),
    "webhooks": (
        WebhookEndpoint,
        (
            "id",
            "name",
            "description",
            "url",
            "events",
            "secret_ciphertext",
            "headers",
            "enabled",
            "max_attempts",
        ),
    ),
    "inbound_hooks": (
        InboundHook,
        (
            "id",
            "name",
            "description",
            "slug",
            "verification",
            "signature_header",
            "secret_ciphertext",
            "target_type",
            "target",
            "enabled",
            "received",
            "last_received_at",
        ),
    ),
    "schedules": (
        Schedule,
        (
            "id",
            "name",
            "description",
            "cron",
            "interval_seconds",
            "target_type",
            "target",
            "payload",
            "enabled",
            "last_run_at",
            "last_status",
            "run_count",
        ),
    ),
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


async def snapshot_environment(environment: Environment) -> dict[str, Any]:
    """Capture every executable definition, excluding data and credentials."""
    result: dict[str, Any] = {"format": 1, "definitions": {}}
    rows = await asyncio.gather(
        *(model.filter(environment_id=environment.id).all() for model, _ in DEFINITIONS.values())
    )
    for (kind, (_model, columns)), instances in zip(DEFINITIONS.items(), rows, strict=True):
        result["definitions"][kind] = [
            {column: _json_value(getattr(instance, column)) for column in columns}
            for instance in instances
        ]
    return result


async def apply_snapshot(environment: Environment, snapshot: dict[str, Any]) -> None:
    """Replace the live definition tree with an explicitly merged snapshot.

    This is intentionally only called by an explicit branch merge. It never
    runs while saving a definition in a feature branch.
    """
    definitions = snapshot.get("definitions") or {}
    for kind, (model, columns) in DEFINITIONS.items():
        await model.filter(environment_id=environment.id).delete()
        for source in definitions.get(kind, []):
            values = {
                key: _json_value(source.get(key))
                for key in columns
                if key != "id" and key in source
            }
            await model.create(environment=environment, **values)


def snapshot_checksum(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def state_from_snapshot(
    platform: Any,
    environment: Environment,
    snapshot: dict[str, Any],
    *,
    api_version: str | None = None,
    release_id: str | None = None,
    revision_id: str | None = None,
) -> EnvironmentState:
    """Build an executable state without copying snapshot rows into live tables."""
    state = EnvironmentState(
        platform=platform,
        environment=environment,
        env_name=environment.name,
        version=environment.version,
        api_version=api_version,
        release_id=release_id,
        revision_id=revision_id,
    )
    materialized: dict[str, list[Any]] = {}
    definitions = snapshot.get("definitions") or {}
    for kind, (model, _columns) in DEFINITIONS.items():
        materialized[kind] = [model(**dict(row)) for row in definitions.get(kind, [])]

    resources = materialized["resources"]
    state.resources = {item.name: item for item in resources}
    state.specs = {item.name: ResourceSpec.from_model(item) for item in resources}
    state.routes = [item for item in materialized["routes"] if item.enabled]
    state.flows = {item.name: item for item in materialized["flows"]}
    state.schemas = {item.name: list(item.fields_ or []) for item in materialized["schemas"]}
    state.transformers = {
        item.name: dict(item.definition or {}) for item in materialized["transformers"]
    }
    state.policies = {
        item.name: Policy(item.name, item.condition, item.description)
        for item in materialized["policies"]
    }
    state.buckets = {item.name: item for item in materialized["buckets"]}
    state.mail_templates = {item.name: item for item in materialized["mail_templates"]}
    state.subscriptions = [item for item in materialized["subscriptions"] if item.enabled]
    state.webhooks = [item for item in materialized["webhooks"] if item.enabled]
    state.inbound_hooks = {item.slug: item for item in materialized["inbound_hooks"]}
    state.schedules = list(materialized["schedules"])
    for secret in await Secret.filter(environment_id=environment.id):
        try:
            state.secret_values[secret.name] = platform.box.open(secret.ciphertext)
        except Exception:
            continue
    state.engine = PolicyEngine(state.policies, python=python_policies())
    state.compiled_schemas = compile_schemas(state.schemas, problems=state.schema_problems)
    return state


async def validate_snapshot(
    platform: Any, environment: Environment, snapshot: dict[str, Any]
) -> list[str]:
    platform.ensure_code()
    state = await state_from_snapshot(platform, environment, snapshot)
    app = await state.compiled()
    return list(app.state.get("problems") or [])


def compatibility(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Return conservative API compatibility findings for two snapshots."""
    breaking: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []
    if previous is None:
        return {"compatible": True, "breaking": [], "warnings": [], "changes": ["initial release"]}
    old = previous.get("definitions") or {}
    new = current.get("definitions") or {}

    def named(kind: str, source: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(item.get("name")): item for item in source.get(kind, [])}

    old_resources, new_resources = named("resources", old), named("resources", new)
    for name in sorted(old_resources.keys() - new_resources.keys()):
        breaking.append(f"resource {name!r} was removed")
    for name in sorted(new_resources.keys() - old_resources.keys()):
        changes.append(f"resource {name!r} was added")
    for name in sorted(old_resources.keys() & new_resources.keys()):
        before = {field["name"]: field for field in old_resources[name].get("fields_", [])}
        after = {field["name"]: field for field in new_resources[name].get("fields_", [])}
        for field in sorted(before.keys() - after.keys()):
            breaking.append(f"resource {name!r} field {field!r} was removed")
        for field in sorted(before.keys() & after.keys()):
            if before[field].get("type") != after[field].get("type"):
                breaking.append(f"resource {name!r} field {field!r} changed type")
            if not before[field].get("required") and after[field].get("required"):
                breaking.append(f"resource {name!r} field {field!r} became required")
        for field in sorted(after.keys() - before.keys()):
            target = (
                breaking
                if after[field].get("required") and "default" not in after[field]
                else changes
            )
            target.append(f"resource {name!r} field {field!r} was added")
        if old_resources[name].get("operations") != new_resources[name].get("operations"):
            warnings.append(f"resource {name!r} operation policies changed")

    def route_key(item: dict[str, Any]) -> str:
        return f"{item.get('method', 'GET').upper()} {item.get('path')}"

    old_routes = {route_key(item) for item in old.get("routes", []) if item.get("enabled", True)}
    new_routes = {route_key(item) for item in new.get("routes", []) if item.get("enabled", True)}
    breaking.extend(f"route {route} was removed" for route in sorted(old_routes - new_routes))
    changes.extend(f"route {route} was added" for route in sorted(new_routes - old_routes))
    return {
        "compatible": not breaking,
        "breaking": breaking,
        "warnings": warnings,
        "changes": changes,
    }
