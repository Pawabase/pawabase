"""SQL sessions for functions: the developer's database, with transactions.

Functions are trusted server-side code, and some of what a commerce backend must do
cannot be said as "create this record": decrement stock only if enough is left,
add a ledger entry and update an order in one commit, sum a month of payments.
:class:`DbSession` is the capability for that. It runs SQL with ``?`` placeholders on
the environment's database, encodes and decodes values by the resources' field
definitions (JSON, booleans, datetimes), and can be opened as a transaction:

    async with ctx.runtime.transaction() as db:
        moved = await db.execute(
            "UPDATE variants SET reserved = reserved + ? WHERE id = ? AND stock - reserved >= ?",
            [2, 7, 2],
        )
        if not moved:
            raise OutOfStock()
        await db.insert("order_items", {"order_id": 1, "variant_id": 7, "quantity": 2})

The statements are the caller's responsibility: pass values as parameters, never
interpolate them.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pawabase_core.ids import new_ulid

from .source import timed
from .sql import JSON_TYPES, TIMESTAMP_FIELDS, check_identifier, decode_value, encode_value, now_value, quote


class DbSession:
    """Run SQL on an environment's database, outside or inside a transaction."""

    def __init__(self, source: Any, specs: Mapping[str, Any], client: Any | None = None) -> None:
        self.source = source
        self.dialect: str = source.dialect
        self._client = client
        self._by_table = {spec.table: spec for spec in specs.values()}
        # Column name -> field spec, when every table that has the column agrees on its type.
        seen: dict[str, dict[str, Any] | None] = {}
        for spec in specs.values():
            for name, field in spec.field_map.items():
                kind = field.get("type", "string")
                kind = "json" if kind in JSON_TYPES else kind
                known = seen.get(name, ...)
                if known is ...:
                    seen[name] = {"type": kind}
                elif known is not None and known["type"] != kind:
                    seen[name] = None
        self._columns = {name: field for name, field in seen.items() if field is not None}

    # ── plumbing ─────────────────────────────────────────────────────────

    @property
    def client(self) -> Any:
        return self._client or self.source.client

    def sql(self, statement: str) -> str:
        """``?`` placeholders for the dialect (``$1`` on PostgreSQL, ``%s`` on MySQL)."""
        if self.dialect == "sqlite":
            return statement
        counter = iter(range(1, 10_000))
        marker = (lambda _m: f"${next(counter)}") if self.dialect == "postgres" else (lambda _m: "%s")
        return re.sub(r"\?", marker, statement)

    def _param(self, value: Any) -> Any:
        if isinstance(value, bool):
            return int(value) if self.dialect == "sqlite" else value
        if isinstance(value, (dict, list)):
            return json.dumps(value, default=str)
        if isinstance(value, dt.datetime):
            return encode_value(self.dialect, {"type": "datetime"}, value)
        if isinstance(value, dt.date):
            return encode_value(self.dialect, {"type": "date"}, value)
        return value

    def _row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, value in row.items():
            out[name] = decode_value(self._columns.get(name), value)
        return out

    # ── reading ──────────────────────────────────────────────────────────

    async def fetch(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Rows as dicts. JSON, boolean and datetime columns are decoded."""
        rows = await timed(self.client, "execute_query_dict", self.sql(statement), [self._param(p) for p in params])
        return [self._row(dict(row)) for row in rows]

    async def one(self, statement: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = await self.fetch(statement, params)
        return rows[0] if rows else None

    async def scalar(self, statement: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = await self.one(statement, params)
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    # ── writing ──────────────────────────────────────────────────────────

    async def execute(self, statement: str, params: Sequence[Any] = ()) -> int:
        """Run a write. Returns the number of rows it changed."""
        count, _ = await timed(self.client, "execute_query", self.sql(statement), [self._param(p) for p in params])
        return int(count or 0)

    def _encode(self, table: str, data: Mapping[str, Any]) -> dict[str, Any]:
        spec = self._by_table.get(table)
        out: dict[str, Any] = {}
        for name, value in data.items():
            check_identifier(name)
            field = (spec.field_map.get(name) if spec else None) or self._columns.get(name)
            out[name] = encode_value(self.dialect, field, value) if field else self._param(value)
        return out

    async def insert(self, table: str, data: Mapping[str, Any]) -> dict[str, Any]:
        """Insert a row and return it, with its key. Timestamps are filled in."""
        check_identifier(table)
        spec = self._by_table.get(table)
        values = dict(data)
        if spec is not None:
            for field in spec.fields:  # declared defaults apply however the row arrives, as in ResourceStore.create
                if "default" in field and field["name"] not in values:
                    values[field["name"]] = field["default"]
        if spec is not None and spec.primary_key not in values and spec.id_type in ("ulid", "uuid"):
            values[spec.primary_key] = new_ulid() if spec.id_type == "ulid" else str(uuid.uuid4())
        if spec is not None and spec.timestamps:
            stamp = dt.datetime.now(dt.UTC)
            values.setdefault("created_at", stamp)
            values.setdefault("updated_at", stamp)
        encoded = self._encode(table, values)
        names = list(encoded)
        columns = ", ".join(quote(self.dialect, name) for name in names)
        marks = ", ".join("?" for _ in names)
        statement = f"INSERT INTO {quote(self.dialect, table)} ({columns}) VALUES ({marks})"
        key = spec.primary_key if spec is not None else "id"
        if self.dialect == "postgres":
            rows = await timed(
                self.client,
                "execute_query_dict",
                self.sql(statement + f" RETURNING {quote(self.dialect, key)}"),
                list(encoded.values()),
            )
            new_id = rows[0][key]
        else:
            new_id = await timed(self.client, "execute_insert", self.sql(statement), list(encoded.values()))
            if key in values:
                new_id = values[key]
        row = await self.one(f"SELECT * FROM {quote(self.dialect, table)} WHERE {quote(self.dialect, key)} = ?", [new_id])
        return row or {key: new_id, **dict(data)}

    async def update(self, table: str, record_id: Any, data: Mapping[str, Any]) -> int:
        """Set columns on one row by its key. ``updated_at`` is touched."""
        check_identifier(table)
        spec = self._by_table.get(table)
        values = dict(data)
        if spec is not None and spec.timestamps:
            values["updated_at"] = dt.datetime.now(dt.UTC)
        key = spec.primary_key if spec is not None else "id"
        encoded = self._encode(table, values)
        sets = ", ".join(f"{quote(self.dialect, name)} = ?" for name in encoded)
        return await self.execute(
            f"UPDATE {quote(self.dialect, table)} SET {sets} WHERE {quote(self.dialect, key)} = ?",
            [*encoded.values(), record_id],
        )

    async def delete(self, table: str, record_id: Any) -> int:
        check_identifier(table)
        spec = self._by_table.get(table)
        key = spec.primary_key if spec is not None else "id"
        return await self.execute(f"DELETE FROM {quote(self.dialect, table)} WHERE {quote(self.dialect, key)} = ?", [record_id])

    @staticmethod
    def now() -> dt.datetime:
        return dt.datetime.now(dt.UTC)


class Transaction:
    """``async with runtime.transaction() as db``: commit on success, roll back on error."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._context: Any = None

    async def __aenter__(self) -> DbSession:
        source = await self._state.source()
        self._context = source.transaction()
        client = await self._context.__aenter__()
        return DbSession(source, self._state.specs, client)

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._context.__aexit__(*exc)


__all__ = ["DbSession", "Transaction", "TIMESTAMP_FIELDS", "now_value"]
