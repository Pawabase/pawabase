"""Reading and writing one Resource's records.

A :class:`ResourceSpec` is the Resource definition in the shape the data layer
needs. A :class:`ResourceStore` pairs it with the environment's
:class:`~app.data.source.DataSource` and does the SQL: PyPika queries in the
connection's dialect, parameterised, with values encoded per column type.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pypika_tortoise import Order, Table
from pypika_tortoise.functions import Count, Star

from . import inspect as db_inspect
from .source import DataSource
from .sql import (
    TIMESTAMP_FIELDS,
    SqlError,
    add_column_sql,
    check_identifier,
    create_index_sql,
    create_table_sql,
    decode_value,
    encode_value,
    now_value,
)

MAX_PAGE_SIZE = 200
FILTER_OPERATORS = (
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "like",
    "ilike",
    "in",
    "nin",
    "is",
    "isnot",
)


@dataclass(frozen=True, slots=True)
class Filter:
    column: str
    op: str
    value: Any


@dataclass
class ResourceSpec:
    """What the data layer needs to know about a Resource."""

    name: str
    table: str
    primary_key: str = "id"
    id_type: str = "integer"
    fields: list[dict[str, Any]] = field(default_factory=list)
    timestamps: bool = True
    owner_field: str | None = None
    relations: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        check_identifier(self.table)
        check_identifier(self.primary_key)
        self.field_map = {spec["name"]: spec for spec in self.fields}
        if self.primary_key not in self.field_map:
            self.field_map[self.primary_key] = {
                "name": self.primary_key,
                "type": "uuid" if self.id_type == "uuid" else "integer",
            }
        if self.timestamps:
            for name in TIMESTAMP_FIELDS:
                self.field_map.setdefault(name, {"name": name, "type": "datetime"})
        if self.owner_field and self.owner_field not in self.field_map:
            self.field_map[self.owner_field] = {"name": self.owner_field, "type": "string"}

    @classmethod
    def from_model(cls, resource: Any) -> ResourceSpec:
        return cls(
            name=resource.name,
            table=resource.table,
            primary_key=resource.primary_key,
            id_type=resource.id_type,
            fields=list(resource.fields_ or []),
            timestamps=resource.timestamps,
            owner_field=resource.owner_field,
            relations=list(resource.relations or []),
        )

    @property
    def columns(self) -> list[str]:
        return list(self.field_map)

    def writable(self) -> set[str]:
        """Columns a client may write (not the key, timestamps or read-only fields)."""
        blocked = {self.primary_key, *TIMESTAMP_FIELDS}
        return {
            name
            for name, spec in self.field_map.items()
            if name not in blocked and not spec.get("read_only")
        }


class ResourceStore:
    """CRUD for one Resource in one environment's database."""

    def __init__(self, source: DataSource, spec: ResourceSpec) -> None:
        self.source = source
        self.spec = spec
        self.table = Table(spec.table)

    # ── schema ───────────────────────────────────────────────────────────

    async def migrate(self) -> list[str]:
        """Create the table, or add columns the definition gained.

        Columns are never dropped or retyped automatically: that loses data,
        and it is a decision for a person looking at a migration, not for a
        button.

        Returns:
            The statements that were run.
        """
        dialect = self.source.dialect
        spec = self.spec
        statements: list[str] = []
        tables = await db_inspect.list_tables(self.source)
        if spec.table not in tables:
            statements.append(
                create_table_sql(
                    dialect,
                    spec.table,
                    spec.primary_key,
                    spec.id_type,
                    spec.fields,
                    spec.timestamps,
                )
            )
            if spec.owner_field and spec.owner_field not in {f["name"] for f in spec.fields}:
                statements.append(
                    add_column_sql(dialect, spec.table, spec.field_map[spec.owner_field])
                )
        else:
            existing = {
                column["name"] for column in await db_inspect.list_columns(self.source, spec.table)
            }
            for name, column in spec.field_map.items():
                if name not in existing:
                    statements.append(add_column_sql(dialect, spec.table, column))
        for column in spec.fields:
            if column.get("indexed") or column.get("unique"):
                statements.append(
                    create_index_sql(
                        dialect, spec.table, column["name"], unique=bool(column.get("unique"))
                    )
                )
        if spec.owner_field:
            statements.append(create_index_sql(dialect, spec.table, spec.owner_field))
        for statement in statements:
            await self.source.execute(statement)
        return statements

    # ── reads ────────────────────────────────────────────────────────────

    def _column(self, name: str):
        if name not in self.spec.field_map:
            raise SqlError(f"{self.spec.name} has no field {name!r}")
        return self.table.field(name)

    def _encode(self, column: str, value: Any) -> Any:
        return encode_value(self.source.dialect, self.spec.field_map.get(column), value)

    def _criterion(self, item: Filter):
        column = self._column(item.column)
        op = item.op
        if op in ("in", "nin"):
            values = (
                item.value if isinstance(item.value, (list, tuple)) else str(item.value).split(",")
            )
            encoded = [self._encode(item.column, v) for v in values]
            return column.isin(encoded) if op == "in" else column.notin(encoded)
        if op in ("is", "isnot"):
            value = str(item.value).lower()
            if value == "null":
                return column.isnull() if op == "is" else column.notnull()
            if value in ("true", "false"):
                encoded = self._encode(item.column, value == "true")
                return column == encoded if op == "is" else column != encoded
            raise SqlError("is/isnot take null, true or false")
        value = (
            self._encode(item.column, item.value)
            if op not in ("like", "ilike")
            else str(item.value).replace("*", "%")
        )
        if op == "eq":
            return column == value
        if op == "neq":
            return column != value
        if op == "gt":
            return column > value
        if op == "gte":
            return column >= value
        if op == "lt":
            return column < value
        if op == "lte":
            return column <= value
        if op == "like":
            return column.like(value)
        if op == "ilike":
            return column.ilike(value) if self.source.dialect == "postgres" else column.like(value)
        raise SqlError(f"unknown filter operator {op!r}")

    def _where(self, query: Any, filters: Sequence[Filter]) -> Any:
        for item in filters:
            query = query.where(self._criterion(item))
        return query

    def _decode(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            name: decode_value(self.spec.field_map.get(name), value) for name, value in row.items()
        }

    async def list(
        self,
        *,
        filters: Sequence[Filter] = (),
        sort: Sequence[tuple[str, bool]] = (),
        limit: int = 50,
        offset: int = 0,
        select: Sequence[str] | None = None,
        count: bool = True,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Records matching *filters*, and the total when *count*.

        Args:
            sort: ``(column, descending)`` pairs.
            select: Columns to return (all when omitted).
        """
        limit = max(1, min(int(limit), MAX_PAGE_SIZE))
        offset = max(0, int(offset))
        qc = self.source.query_class
        columns = [self._column(name) for name in select] if select else [Star()]
        query = self._where(qc.from_(self.table).select(*columns), filters)
        for column, descending in sort or [(self.spec.primary_key, False)]:
            query = query.orderby(
                self._column(column), order=Order.desc if descending else Order.asc
            )
        query = query.limit(limit).offset(offset)
        sql, params = query.get_parameterized_sql()
        rows = [self._decode(row) for row in await self.source.fetch(sql, params)]
        total = None
        if count:
            count_query = self._where(
                qc.from_(self.table).select(Count(Star()).as_("total")), filters
            )
            sql, params = count_query.get_parameterized_sql()
            result = await self.source.fetch(sql, params)
            total = int(result[0]["total"]) if result else 0
        return rows, total

    async def get(self, record_id: Any) -> dict[str, Any] | None:
        rows, _ = await self.list(
            filters=[Filter(self.spec.primary_key, "eq", record_id)], limit=1, count=False
        )
        return rows[0] if rows else None

    async def get_many(self, column: str, values: Sequence[Any]) -> list[dict[str, Any]]:
        if not values:
            return []
        rows, _ = await self.list(
            filters=[Filter(column, "in", list(values))], limit=MAX_PAGE_SIZE, count=False
        )
        return rows

    # ── writes ───────────────────────────────────────────────────────────

    def _clean(self, data: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(data) - set(self.spec.field_map)
        if unknown:
            raise SqlError(f"unknown fields: {', '.join(sorted(unknown))}")
        return {key: self._encode(key, value) for key, value in data.items()}

    async def create(self, data: Mapping[str, Any], *, client: Any = None) -> dict[str, Any]:
        values = self._clean(data)
        dialect = self.source.dialect
        if self.spec.id_type == "uuid" and self.spec.primary_key not in values:
            values[self.spec.primary_key] = self._encode(self.spec.primary_key, str(uuid.uuid4()))
        if self.spec.timestamps:
            stamp = now_value(dialect)
            values.setdefault("created_at", stamp)
            values.setdefault("updated_at", stamp)
        qc = self.source.query_class
        query = qc.into(self.table).columns(*values.keys()).insert(*values.values())
        runner = client or self.source.client
        if dialect == "postgres":
            query = query.returning(self.table.field(self.spec.primary_key))
            sql, params = query.get_parameterized_sql()
            rows = await runner.execute_query_dict(sql, params)
            new_id = rows[0][self.spec.primary_key]
        else:
            sql, params = query.get_parameterized_sql()
            new_id = await runner.execute_insert(sql, params)
            if self.spec.primary_key in values:
                new_id = values[self.spec.primary_key]
        record = await self._get_with(runner, new_id)
        return record or {self.spec.primary_key: decode_value(None, new_id), **dict(data)}

    async def _get_with(self, runner: Any, record_id: Any) -> dict[str, Any] | None:
        qc = self.source.query_class
        query = (
            qc.from_(self.table)
            .select(Star())
            .where(
                self._column(self.spec.primary_key)
                == self._encode(self.spec.primary_key, record_id)
            )
            .limit(1)
        )
        sql, params = query.get_parameterized_sql()
        rows = await runner.execute_query_dict(sql, params)
        return self._decode(rows[0]) if rows else None

    async def update(
        self, record_id: Any, data: Mapping[str, Any], *, client: Any = None
    ) -> dict[str, Any] | None:
        values = self._clean(data)
        values.pop(self.spec.primary_key, None)
        if self.spec.timestamps:
            values["updated_at"] = now_value(self.source.dialect)
        runner = client or self.source.client
        if values:
            qc = self.source.query_class
            query = qc.update(self.table)
            for key, value in values.items():
                query = query.set(self.table.field(key), value)
            query = query.where(
                self._column(self.spec.primary_key)
                == self._encode(self.spec.primary_key, record_id)
            )
            sql, params = query.get_parameterized_sql()
            await runner.execute_query(sql, params)
        return await self._get_with(runner, record_id)

    async def delete(self, record_id: Any, *, client: Any = None) -> bool:
        qc = self.source.query_class
        query = (
            qc.from_(self.table)
            .delete()
            .where(
                self._column(self.spec.primary_key)
                == self._encode(self.spec.primary_key, record_id)
            )
        )
        sql, params = query.get_parameterized_sql()
        runner = client or self.source.client
        count, _ = await runner.execute_query(sql, params)
        return bool(count)


def parse_filters(query: Mapping[str, str], spec: ResourceSpec) -> list[Filter]:
    """``?filter[status]=eq.live&filter[views]=gte.10`` into filters.

    A bare ``?filter[status]=live`` means ``eq``.
    """
    filters = []
    for key, raw in query.items():
        if not (key.startswith("filter[") and key.endswith("]")):
            continue
        column = key[7:-1]
        if column not in spec.field_map:
            raise SqlError(f"cannot filter on unknown field {column!r}")
        op, sep, value = raw.partition(".")
        if not sep or op not in FILTER_OPERATORS:
            op, value = "eq", raw
        filters.append(Filter(column, op, value))
    return filters


def parse_sort(value: str | None, spec: ResourceSpec) -> list[tuple[str, bool]]:
    """``-created_at,title`` into ``[("created_at", True), ("title", False)]``."""
    if not value:
        return []
    result = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        descending = part.startswith("-")
        column = part.lstrip("-+")
        if column not in spec.field_map:
            raise SqlError(f"cannot sort on unknown field {column!r}")
        result.append((column, descending))
    return result
