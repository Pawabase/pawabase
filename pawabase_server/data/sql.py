"""SQL for Resource tables across SQLite, PostgreSQL and MySQL.

Queries are built with PyPika (the query builder Tortoise, and so Sillo Record,
already uses) in the dialect of the environment's connection, and always
parameterised. Nothing here builds SQL by string formatting from user input:
identifiers are validated against the Resource's declared fields before they
reach PyPika, which quotes them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

TIMESTAMP_FIELDS = ("created_at", "updated_at")
JSON_TYPES = {"json", "array", "object", "ref"}


class SqlError(ValueError):
    """A request the data layer refuses: unknown column, bad operator."""


def check_identifier(name: str) -> str:
    if not isinstance(name, str) or not IDENTIFIER.match(name):
        raise SqlError(f"invalid identifier {name!r}")
    return name


def dialect_of(client: Any) -> str:
    """``sqlite``, ``postgres`` or ``mysql`` for a Tortoise client."""
    module = type(client).__module__
    if "sqlite" in module:
        return "sqlite"
    if "asyncpg" in module or "psycopg" in module or "postgres" in module:
        return "postgres"
    if "mysql" in module:
        return "mysql"
    return "sqlite"


# ── DDL ──────────────────────────────────────────────────────────────────────

_TYPES: dict[str, dict[str, str]] = {
    "sqlite": {
        "string": "TEXT",
        "text": "TEXT",
        "email": "TEXT",
        "url": "TEXT",
        "integer": "INTEGER",
        "number": "REAL",
        "boolean": "INTEGER",
        "datetime": "TEXT",
        "date": "TEXT",
        "uuid": "TEXT",
        "ulid": "TEXT",
        "json": "TEXT",
    },
    "postgres": {
        "string": "VARCHAR({n})",
        "text": "TEXT",
        "email": "VARCHAR(320)",
        "url": "TEXT",
        "integer": "BIGINT",
        "number": "DOUBLE PRECISION",
        "boolean": "BOOLEAN",
        "datetime": "TIMESTAMPTZ",
        "date": "DATE",
        "uuid": "UUID",
        "ulid": "CHAR(26)",
        "json": "JSONB",
    },
    "mysql": {
        "string": "VARCHAR({n})",
        "text": "TEXT",
        "email": "VARCHAR(320)",
        "url": "TEXT",
        "integer": "BIGINT",
        "number": "DOUBLE",
        "boolean": "BOOLEAN",
        "datetime": "DATETIME(6)",
        "date": "DATE",
        "uuid": "CHAR(36)",
        "ulid": "CHAR(26)",
        "json": "JSON",
    },
}


def column_type(dialect: str, spec: Mapping[str, Any]) -> str:
    kind = spec.get("type", "string")
    if kind in JSON_TYPES:
        kind = "json"
    template = _TYPES[dialect][kind]
    return template.format(n=int(spec.get("max_length") or 255))


def quote(dialect: str, name: str) -> str:
    check_identifier(name)
    return f"`{name}`" if dialect == "mysql" else f'"{name}"'


def primary_key_column(dialect: str, name: str, id_type: str) -> str:
    column = quote(dialect, name)
    if id_type == "uuid":
        kind = {"sqlite": "TEXT", "postgres": "UUID", "mysql": "CHAR(36)"}[dialect]
        return f"{column} {kind} PRIMARY KEY"
    if id_type == "ulid":
        kind = {"sqlite": "TEXT", "postgres": "CHAR(26)", "mysql": "CHAR(26)"}[dialect]
        return f"{column} {kind} PRIMARY KEY"
    return {
        "sqlite": f"{column} INTEGER PRIMARY KEY AUTOINCREMENT",
        "postgres": f"{column} BIGSERIAL PRIMARY KEY",
        "mysql": f"{column} BIGINT AUTO_INCREMENT PRIMARY KEY",
    }[dialect]


def column_definition(dialect: str, spec: Mapping[str, Any]) -> str:
    parts = [quote(dialect, spec["name"]), column_type(dialect, spec)]
    if spec.get("unique"):
        parts.append("UNIQUE")
    return " ".join(parts)


def create_table_sql(
    dialect: str,
    table: str,
    primary_key: str,
    id_type: str,
    fields: list[Mapping[str, Any]],
    timestamps: bool,
) -> str:
    columns = [primary_key_column(dialect, primary_key, id_type)]
    for spec in fields:
        if spec["name"] in (primary_key, *(TIMESTAMP_FIELDS if timestamps else ())):
            continue
        columns.append(column_definition(dialect, spec))
    if timestamps:
        kind = column_type(dialect, {"type": "datetime"})
        columns += [f"{quote(dialect, name)} {kind}" for name in TIMESTAMP_FIELDS]
    return f"CREATE TABLE IF NOT EXISTS {quote(dialect, table)} ({', '.join(columns)})"


def add_column_sql(dialect: str, table: str, spec: Mapping[str, Any]) -> str:
    return f"ALTER TABLE {quote(dialect, table)} ADD COLUMN {column_definition(dialect, {**spec, 'unique': False})}"


def index_name(table: str, column: str) -> str:
    """The name the index on one column gets (and is looked for under)."""
    return check_identifier(f"ix_{table}_{column}"[:60])


def create_index_sql(dialect: str, table: str, column: str, unique: bool = False) -> str:
    name = index_name(table, column)
    kind = "UNIQUE INDEX" if unique else "INDEX"
    exists = "" if dialect == "mysql" else "IF NOT EXISTS "
    return f"CREATE {kind} {exists}{quote(dialect, name)} ON {quote(dialect, table)} ({quote(dialect, column)})"


def drop_index_sql(dialect: str, table: str, name: str) -> str:
    if dialect == "mysql":
        return f"DROP INDEX {quote(dialect, name)} ON {quote(dialect, table)}"
    return f"DROP INDEX IF EXISTS {quote(dialect, name)}"


def rename_column_sql(dialect: str, table: str, old: str, new: str) -> str:
    return f"ALTER TABLE {quote(dialect, table)} RENAME COLUMN {quote(dialect, old)} TO {quote(dialect, new)}"


def rename_table_sql(dialect: str, old: str, new: str) -> str:
    return f"ALTER TABLE {quote(dialect, old)} RENAME TO {quote(dialect, new)}"


def drop_column_sql(dialect: str, table: str, column: str) -> str:
    return f"ALTER TABLE {quote(dialect, table)} DROP COLUMN {quote(dialect, column)}"


def drop_table_sql(dialect: str, table: str) -> str:
    return f"DROP TABLE IF EXISTS {quote(dialect, table)}"


#: What a column or table is renamed to when a migration retires it: kept, out of the way, until it is purged.
PARKED = re.compile(r"^_pbm_([0-9a-z]{8})(x?)_(.+)$")


def is_parked(name: str) -> bool:
    return name.startswith("_pbm_")


_CAST_MYSQL = {
    "integer": "SIGNED",
    "boolean": "SIGNED",
    "number": "DOUBLE",
    "datetime": "DATETIME(6)",
    "date": "DATE",
    "json": "JSON",
}


def cast_type(dialect: str, spec: Mapping[str, Any]) -> str:
    """The type to ``CAST`` to when copying values into a retyped column."""
    if dialect != "mysql":
        return column_type(dialect, spec)
    kind = "json" if spec.get("type") in JSON_TYPES else spec.get("type", "string")
    return _CAST_MYSQL.get(kind, "CHAR")


# ── value codecs ─────────────────────────────────────────────────────────────


def encode_value(dialect: str, spec: Mapping[str, Any] | None, value: Any) -> Any:
    """A Python value as the driver expects it for this column."""
    if value is None or spec is None:
        return value
    kind = spec.get("type", "string")
    if kind in JSON_TYPES:
        return json.dumps(_unwrap_json_text(value), default=str)
    if kind == "boolean":
        if isinstance(value, str):
            normalized = value.lower()
            if normalized not in ("true", "false", "1", "0"):
                raise ValueError("expected true, false, 1 or 0")
            value = normalized in ("true", "1")
        elif not isinstance(value, (bool, int)) or value not in (0, 1):
            raise ValueError("expected a boolean")
        return int(bool(value)) if dialect == "sqlite" else bool(value)
    if kind == "datetime":
        moment = (
            value
            if isinstance(value, dt.datetime)
            else dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.UTC)
        if dialect == "sqlite":
            return moment.isoformat()
        if dialect == "mysql":
            return moment.astimezone(dt.UTC).replace(tzinfo=None)
        return moment
    if kind == "date":
        day = value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value))
        return day.isoformat() if dialect == "sqlite" else day
    if kind == "uuid":
        return str(value) if dialect != "postgres" else uuid.UUID(str(value))
    if kind == "ulid":
        return str(value).upper()
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    return value


def _unwrap_json_text(value: Any) -> Any:
    """A JSON document that arrived as text, as the document.

    Forms and hand-written clients often send an object or array as its JSON text. Stored as given, that text would be
    encoded a second time and read back as a string, and an edit-save cycle would wrap it again. A string that holds an
    object or array is always such a slip; any other string (a name, a number in quotes) is a value and is kept.
    """
    for _ in range(4):
        if not isinstance(value, str) or not value.lstrip().startswith(("{", "[", '"')):
            break
        try:
            parsed = json.loads(value)
        except ValueError:
            break
        if not isinstance(parsed, (dict, list, str)):
            break
        value = parsed
    return value


def decode_value(spec: Mapping[str, Any] | None, value: Any) -> Any:
    """A driver value as JSON-friendly Python."""
    if value is None:
        return None
    kind = (spec or {}).get("type")
    if kind in JSON_TYPES and isinstance(value, (str, bytes)):
        try:
            # Rows written before JSON text was unwrapped on the way in come back wrapped; read them as documents.
            return _unwrap_json_text(json.loads(value))
        except ValueError:
            return value
    if kind == "boolean":
        return bool(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "is_finite"):  # Decimal
        return float(value)
    return value


def now_value(dialect: str) -> Any:
    return encode_value(dialect, {"type": "datetime"}, dt.datetime.now(dt.UTC))
