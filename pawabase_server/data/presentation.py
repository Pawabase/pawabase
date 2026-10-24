"""Public resource serialization, separate from trusted storage and policy input."""

from __future__ import annotations

from typing import Any

from pawabase_server.core.transformers import apply_transformer


def _value(value: Any, spec: dict, schemas: dict) -> Any:
    kind = spec.get("type")
    if kind == "array" and isinstance(value, list):
        return [_value(item, spec.get("items") or {}, schemas) for item in value]
    if kind in ("object", "ref") and isinstance(value, dict):
        fields = spec.get("fields", []) if kind == "object" else schemas.get(spec.get("schema"), [])
        return _fields(value, {field["name"]: field for field in fields}, schemas)
    return value


def _fields(record: dict, fields: dict, schemas: dict) -> dict:
    return {
        name: _value(value, fields[name], schemas)
        for name, value in record.items()
        if name in fields and not fields[name].get("write_only")
    }


def _plan(spec: Any, schemas: dict) -> tuple[frozenset, frozenset] | None:
    """The columns a record may show and those that need their nested fields cleaned, worked out once per resource."""
    plan = getattr(spec, "_public_plan", False)
    if plan is False:
        allowed = frozenset(
            name for name, field in spec.field_map.items() if not field.get("write_only")
        )
        nested = frozenset(
            name
            for name in allowed
            if spec.field_map[name].get("type") in ("array", "object", "ref")
        )
        plan = spec._public_plan = (allowed, nested)
    return plan


def public_record(state: Any, resource: Any, data: Any) -> Any:
    """Drop undeclared and write-only fields, including nested fields and relations.

    Never mutate a row used for policy evaluation, caching, or internal work.
    """
    if isinstance(data, list):
        return [public_record(state, resource, row) for row in data]
    if not isinstance(data, dict):
        return data
    spec = state.specs[resource.name]
    plan = _plan(spec, state.schemas)
    if plan is None:
        result = _fields(data, spec.field_map, state.schemas)
    else:
        allowed, nested = plan
        if nested:
            result = {
                name: _value(value, spec.field_map[name], state.schemas)
                if name in nested
                else value
                for name, value in data.items()
                if name in allowed
            }
        else:
            result = {name: value for name, value in data.items() if name in allowed}
    for relation in spec.relations:
        name = relation["name"]
        target = state.resources.get(relation["resource"])
        if name in data and target is not None:
            # Expanded rows have already been authorized and presented. Keep
            # transformed shapes, which need not resemble the original schema.
            result[name] = data[name]
    return result


async def present_record(state: Any, resource: Any, data: Any, context: dict) -> Any:
    data = public_record(state, resource, data)
    if resource.transformer:
        return await apply_transformer(
            data, resource.transformer, context=context, registry=state.transformers
        )
    return data
