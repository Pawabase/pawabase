"""Move a database from integer keys to ULIDs, keeping every row.

A primary key's type cannot be altered in place across SQLite, Postgres and
MySQL, and every foreign key to it has to change with it. So a migration here
does it in four steps inside the migration's own transaction:

1. read every row of every table into memory,
2. give each row with an integer key a ULID (and rewrite the foreign keys that
   point at it),
3. drop the tables and create them again from the new models,
4. write the rows back.

The tables are created by Tortoise's own ``CreateModel`` operations, so the
indexes, uniques and constraints are exactly what the models declare, on every
dialect. The migration is atomic: a failure anywhere leaves the old tables.

New ULIDs are minted from each row's ``created_at``, so sorting by id still
means creation order for existing data. Tables whose key was not an integer
(already a string or a UUID) keep their ids.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Callable, Mapping
from typing import Any

from tortoise.fields.relational import ForeignKeyFieldInstance, OneToOneFieldInstance
from tortoise.migrations import operations as ops

from .ids import ulid_at

CHUNK = 500
_RELATIONS = (ForeignKeyFieldInstance, OneToOneFieldInstance)


class UlidRebuild:
    """The operations of one rebuild, in order, with the rows carried between them.

    Args:
        creates: ``CreateModel`` operations for every table, referenced tables first.
        fixed: Per table, a function that maps an old integer key to its new id,
            instead of minting one (see :func:`pawabase_core.ids.legacy_ulid`).
        references: Per table, columns that hold an old integer key of another
            table without being a foreign key: ``{"login_events": {"user_id": "users"}}``
            (the value names the *table* referred to). The integer may be stored as
            text. A referenced key with no matching row is kept as it is.
    """

    def __init__(
        self,
        creates: list[ops.CreateModel],
        *,
        fixed: Mapping[str, Callable[[int], str]] | None = None,
        references: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.creates = list(creates)
        self.fixed = dict(fixed or {})
        self.references = {t: dict(c) for t, c in (references or {}).items()}
        self._rows: dict[str, list[dict[str, Any]]] = {}
        self._tables = {op.name: op.options["table"] for op in self.creates}

    # ── the operations ───────────────────────────────────────────────────

    def operations(self) -> list[Any]:
        names = [op.name for op in self.creates]
        return [
            ops.RunPython(self.dump),
            *[ops.DeleteModel(name) for name in reversed(names)],
            *self.creates,
            ops.RunPython(self.restore),
        ]

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _dialect(editor: Any) -> str:
        return editor.client.capabilities.dialect

    @staticmethod
    def _quote(dialect: str, name: str) -> str:
        return f"`{name}`" if dialect == "mysql" else f'"{name}"'

    def _foreign_keys(self, op: ops.CreateModel) -> dict[str, str]:
        """Column → referenced table, for the foreign keys this model declares."""
        out = {}
        for name, field in op.fields:
            if isinstance(field, _RELATIONS):
                column = getattr(field, "source_field", None) or f"{name}_id"
                target = field.model_name.split(".", 1)[1]
                out[column] = self._tables[target]
        return out

    @staticmethod
    def _created_ms(row: Mapping[str, Any]) -> int | None:
        stamp = row.get("created_at")
        if isinstance(stamp, str):
            try:
                stamp = dt.datetime.fromisoformat(stamp.replace(" ", "T"))
            except ValueError:
                return None
        if isinstance(stamp, dt.datetime):
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.UTC)
            return int(stamp.timestamp() * 1000)
        return None

    # ── step 1 and 2: read, re-key ───────────────────────────────────────

    async def dump(self, apps: Any, editor: Any) -> None:
        dialect = self._dialect(editor)
        keys: dict[str, str] = {}
        for op in self.creates:
            table, key = op.options["table"], op.options.get("pk_attr", "id")
            keys[table] = key
            self._rows[table] = await editor.client.execute_query_dict(
                f"SELECT * FROM {self._quote(dialect, table)} ORDER BY {self._quote(dialect, key)}"
            )

        remap: dict[str, dict[Any, str]] = {}
        for table, rows in self._rows.items():
            key = keys[table]
            if rows and not all(isinstance(row[key], int) and not isinstance(row[key], bool) for row in rows):
                continue  # string or UUID keys stay as they are
            mapping: dict[Any, str] = {}
            if table in self.fixed:
                make = self.fixed[table]
                mapping = {row[key]: make(row[key]) for row in rows}
            else:
                last_ms, entropy = 0, 0
                for row in rows:
                    ms = max(self._created_ms(row) or last_ms, last_ms)
                    if ms > last_ms or entropy == 0:
                        entropy = int.from_bytes(os.urandom(10), "big") >> 1  # headroom to count up
                        last_ms = ms
                    else:
                        entropy += 1
                    mapping[row[key]] = ulid_at(last_ms, entropy)
            remap[table] = mapping

        for op in self.creates:
            table = op.options["table"]
            foreign = {**self._foreign_keys(op), **self.references.get(table, {})}
            for row in self._rows[table]:
                if table in remap:
                    row[keys[table]] = remap[table][row[keys[table]]]
                for column, target in foreign.items():
                    value = row.get(column)
                    mapping = remap.get(target)
                    if value is None or mapping is None:
                        continue
                    soft = column in self.references.get(table, {})
                    # A soft reference may hold the integer as text ("7"), as Sillo's permission tables do.
                    lookup = int(value) if soft and isinstance(value, str) and value.isdigit() else value
                    if lookup not in mapping:
                        if soft:
                            continue  # a reference to a row that is gone: keep it as it was
                        raise RuntimeError(
                            f"{table}.{column} = {value!r} has no matching row in {target}; "
                            "fix or remove the orphan row, then run the migration again"
                        )
                    row[column] = mapping[lookup]

    # ── step 4: write back ───────────────────────────────────────────────

    async def restore(self, apps: Any, editor: Any) -> None:
        dialect = self._dialect(editor)
        for op in self.creates:
            table = op.options["table"]
            rows = self._rows.get(table) or []
            if not rows:
                continue
            columns = list(rows[0])
            marks = (
                ", ".join(f"${n}" for n in range(1, len(columns) + 1))
                if dialect == "postgres"
                else ", ".join("?" for _ in columns)
            )
            sql = (
                f"INSERT INTO {self._quote(dialect, table)} "
                f"({', '.join(self._quote(dialect, c) for c in columns)}) VALUES ({marks})"
            )
            values = [[_adapt(row[column]) for column in columns] for row in rows]
            for start in range(0, len(values), CHUNK):
                await editor.client.execute_many(sql, values[start : start + CHUNK])
        self._rows.clear()


def _adapt(value: Any) -> Any:
    """A value read from one connection, as the same connection wants it back."""
    return json.dumps(value) if isinstance(value, (dict, list)) else value
