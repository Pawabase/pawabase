"""Stored field definitions, compiled into Pydantic models.

Pawabase stores schemas as data so Studio can edit them. Validation is Sillo's
job, and Sillo validates with Pydantic, so a stored schema is compiled into a
Pydantic model and handed to Sillo as a route's ``request_model`` or
``response_model``. The generated OpenAPI document then describes exactly what
is enforced.

A field definition::

    {"name": "title", "type": "string", "required": true, "max_length": 200}

Types: ``string``, ``text``, ``integer``, ``number``, ``boolean``, ``datetime``,
``date``, ``uuid``, ``email``, ``url``, ``json``, ``array`` (with ``items``),
``object`` (with ``fields``) and ``ref`` (with ``schema``, a reusable schema's name).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

FIELD_TYPES = (
    "string",
    "text",
    "integer",
    "number",
    "boolean",
    "datetime",
    "date",
    "uuid",
    "email",
    "url",
    "json",
    "array",
    "object",
    "ref",
)

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
URL_PATTERN = r"^https?://[^\s]+$"

Mode = Literal["create", "update", "read", "any"]


class SchemaError(ValueError):
    """A field definition that cannot be compiled."""


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _ReadBase(BaseModel):
    model_config = ConfigDict(extra="allow", from_attributes=True)


def validate_fields(fields: Sequence[Mapping[str, Any]], *, path: str = "fields") -> None:
    """Refuse malformed definitions before they are stored.

    Raises:
        SchemaError: Naming the offending field.
    """
    seen: set[str] = set()
    for index, spec in enumerate(fields):
        where = f"{path}[{index}]"
        name = spec.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise SchemaError(
                f"{where}: name must be a letter or underscore followed by letters, digits or underscores"
            )
        if name in seen:
            raise SchemaError(f"{where}: duplicate field {name!r}")
        seen.add(name)
        kind = spec.get("type", "string")
        if kind not in FIELD_TYPES:
            raise SchemaError(f"{where}: unknown type {kind!r}")
        if kind == "array" and "items" in spec:
            validate_fields([{**spec["items"], "name": "item"}], path=f"{where}.items")
        if kind == "object":
            validate_fields(spec.get("fields", []), path=f"{where}.fields")
        if kind == "ref" and not spec.get("schema"):
            raise SchemaError(f"{where}: a ref field names a schema")
        if "pattern" in spec:
            try:
                re.compile(spec["pattern"])
            except re.error as exc:
                raise SchemaError(f"{where}: invalid pattern: {exc}") from exc


def _python_type(
    spec: Mapping[str, Any], model_name: str, registry: Mapping[str, Any], mode: Mode
) -> Any:
    kind = spec.get("type", "string")
    if spec.get("enum"):
        return Literal[tuple(spec["enum"])]  # type: ignore[misc]
    if kind in ("string", "text", "email", "url"):
        return str
    if kind == "integer":
        return int
    if kind == "number":
        return float
    if kind == "boolean":
        return bool
    if kind == "datetime":
        return dt.datetime
    if kind == "date":
        return dt.date
    if kind == "uuid":
        return uuid.UUID
    if kind == "json":
        return Any
    if kind == "array":
        items = spec.get("items") or {"type": "json"}
        return list[_python_type(items, f"{model_name}Item", registry, mode)]  # type: ignore[misc]
    if kind == "object":
        return compile_model(
            f"{model_name}{spec['name'].title()}",
            spec.get("fields", []),
            mode=mode,
            registry=registry,
        )
    if kind == "ref":
        target = registry.get(spec["schema"])
        if target is None:
            raise SchemaError(
                f"field {spec.get('name')!r} refers to unknown schema {spec['schema']!r}"
            )
        if isinstance(target, type) and issubclass(target, BaseModel):
            return target
        return compile_model(spec["schema"], target, mode=mode, registry=registry)
    raise SchemaError(f"unknown type {kind!r}")


def _constraints(spec: Mapping[str, Any]) -> dict[str, Any]:
    kind = spec.get("type", "string")
    out: dict[str, Any] = {}
    for key in ("min_length", "max_length"):
        if key in spec:
            out[key] = spec[key]
    for key, target in (("minimum", "ge"), ("maximum", "le")):
        if key in spec:
            out[target] = spec[key]
    if "pattern" in spec:
        out["pattern"] = spec["pattern"]
    elif kind == "email":
        out["pattern"] = EMAIL_PATTERN
    elif kind == "url":
        out["pattern"] = URL_PATTERN
    if spec.get("description"):
        out["description"] = spec["description"]
    if "example" in spec:
        out["examples"] = [spec["example"]]
    return out


def compile_model(
    name: str,
    fields: Sequence[Mapping[str, Any]],
    *,
    mode: Mode = "create",
    registry: Mapping[str, Any] | None = None,
) -> type[BaseModel]:
    """Compile field definitions into a Pydantic model.

    Args:
        name: The model's name, as it appears in the OpenAPI document.
        fields: Field definitions.
        mode: ``create`` enforces ``required`` and rejects unknown and read-only
            fields; ``update`` makes every field optional (a partial update);
            ``read`` describes responses and drops write-only fields; ``any``
            validates types only.
        registry: Reusable schemas by name, for ``ref`` fields. Values are field
            lists or already-compiled models.
    """
    validate_fields(fields)
    registry = registry or {}
    definitions: dict[str, Any] = {}
    for spec in fields:
        if mode in ("create", "update") and spec.get("read_only"):
            continue
        if mode == "read" and spec.get("write_only"):
            continue
        annotation = _python_type(spec, name, registry, mode)
        constraints = _constraints(spec)
        required = bool(spec.get("required")) and mode == "create" and "default" not in spec
        nullable = spec.get("nullable", not required) or mode in ("update", "read", "any")
        if nullable:
            annotation = annotation | None  # type: ignore[operator]
        if required:
            default: Any = ...
        elif "default" in spec and mode == "create":
            default = spec["default"]
        else:
            default = None
        definitions[spec["name"]] = (Annotated[annotation, Field(**constraints)], default)
    base = _ReadBase if mode == "read" else _Base
    safe_name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return create_model(safe_name, __base__=base, **definitions)


def compile_schemas(
    schemas: Mapping[str, Sequence[Mapping[str, Any]]], *, mode: Mode = "any"
) -> dict[str, type[BaseModel]]:
    """Compile a project's reusable schemas, resolving references between them."""
    compiled: dict[str, Any] = dict(schemas)
    for name, fields in schemas.items():
        compiled[name] = compile_model(name, fields, mode=mode, registry=compiled)
    return compiled


def validate_payload(
    fields: Sequence[Mapping[str, Any]],
    payload: Any,
    *,
    mode: Mode = "create",
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate *payload* against definitions and return the cleaned data.

    Raises:
        pydantic.ValidationError: With locations Sillo already knows how to report.
    """
    model = compile_model("Payload", fields, mode=mode, registry=registry)
    instance = model.model_validate(payload)
    return instance.model_dump(exclude_unset=mode == "update", mode="json")
