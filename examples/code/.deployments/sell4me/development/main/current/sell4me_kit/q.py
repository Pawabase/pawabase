"""Querying the store's tables without an ORM.

Rows are :class:`Row`: dicts that also answer attribute access, so code reads like it
did on the ORM (``variant.price_minor``) and still serialises as JSON.

Every table in this application soft-deletes. ``find``, ``first``, ``count`` and
``exists`` hide rows whose ``deleted_at`` is set unless asked not to; ``soft_delete``
sets it. Conditions are a dict of ``column -> value`` (equality; ``None`` means
``IS NULL``) or ``column -> Op``, built with :func:`gt`, :func:`gte`, :func:`lt`,
:func:`lte`, :func:`ne`, :func:`in_`, :func:`like` and :func:`not_null`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any



def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _joined(row: "Row") -> str:
    joined = " ".join(part for part in (row.get("first_name"), row.get("last_name")) if part)
    return joined or (row.get("email") or "")


#: What the ORM models computed from their columns. A column of the same name always wins.
COMPUTED: dict[str, Any] = {
    "available": lambda r: (r.get("stock") or 0) - (r.get("reserved") or 0) if "stock" in r else None,
    "stock_state": lambda r: (
        "untracked"
        if not r.get("track_inventory")
        else "out_of_stock"
        if (r.get("stock") or 0) - (r.get("reserved") or 0) <= 0
        else "low_stock"
        if (r.get("stock") or 0) - (r.get("reserved") or 0) <= (r.get("low_stock_threshold") or 0)
        else "in_stock"
    ),
    "is_paid": lambda r: r.get("payment_status") in ("paid", "partially_refunded"),
    "net_minor": lambda r: (r.get("total_minor") or 0) - (r.get("refunded_minor") or 0),
    "name": _joined,
    "is_exhausted": lambda r: r.get("usage_limit") is not None and (r.get("usage_count") or 0) >= r["usage_limit"],
    "is_expired": lambda r: bool(parse_dt(r.get("ends_at")) and parse_dt(r.get("ends_at")) < _now()),
    "line_total_minor": lambda r: (r.get("unit_price_minor") or 0) * (r.get("quantity") or 0),
    "refundable_quantity": lambda r: max((r.get("quantity") or 0) - (r.get("quantity_refunded") or 0), 0),
    "is_usable": lambda r: bool(r.get("is_platform")) or r.get("status") == "verified",
    "has_unpublished_changes": lambda r: r.get("draft") != r.get("published") if "draft" in r else False,
    "path": lambda r: "/" if r.get("kind") in ("home", "header") else f"/pages/{r.get('slug')}",
    "variance_minor": lambda r: None
    if r.get("closing_count_minor") is None or r.get("expected_cash_minor") is None
    else r["closing_count_minor"] - r["expected_cash_minor"],
    "roi_minor": lambda r: (r.get("revenue_minor") or 0) - (r.get("spend_minor") or 0),
    "is_connected": lambda r: r.get("status") == "connected",
    "is_processed": lambda r: bool(r.get("variants")),
    "is_open": lambda r: not r.get("accepted_at")
    and not r.get("revoked_at")
    and bool(parse_dt(r.get("expires_at")) and parse_dt(r.get("expires_at")) > _now()),
    "state": lambda r: (
        None
        if "is_automatic" not in r
        else "disabled"
        if not r.get("is_active")
        else "expired"
        if COMPUTED["is_expired"](r)
        else "exhausted"
        if COMPUTED["is_exhausted"](r)
        else "scheduled"
        if parse_dt(r.get("starts_at")) and parse_dt(r.get("starts_at")) > _now()
        else "active"
    ),
    "average_order_minor": lambda r: 0 if not r.get("orders_count") else (r.get("total_spent_minor") or 0) // r["orders_count"],
}


class Row(dict):
    """A table row. ``row.name`` is ``row["name"]``; a missing column is ``None``.

    Attributes the ORM models computed (``available``, ``is_paid``, ``net_minor``…) are
    computed here on demand, so ported code reads the same.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        if name in self:
            return self[name]
        compute = COMPUTED.get(name)
        return compute(self) if compute else None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    @property
    def pk(self) -> Any:
        return self.get("id")


def row(data: Mapping[str, Any] | None) -> Row | None:
    return Row(data) if data is not None else None


class Op:
    __slots__ = ("op", "value")

    def __init__(self, op: str, value: Any = None) -> None:
        self.op = op
        self.value = value


def gt(value: Any) -> Op:
    return Op(">", value)


def gte(value: Any) -> Op:
    return Op(">=", value)


def lt(value: Any) -> Op:
    return Op("<", value)


def lte(value: Any) -> Op:
    return Op("<=", value)


def ne(value: Any) -> Op:
    return Op("<>", value)


def in_(values: Sequence[Any]) -> Op:
    return Op("IN", list(values))


def not_in(values: Sequence[Any]) -> Op:
    return Op("NOT IN", list(values))


def like(pattern: str) -> Op:
    return Op("LIKE", pattern)


def not_null() -> Op:
    return Op("IS NOT NULL")


def conditions(where: Mapping[str, Any] | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, value in (where or {}).items():
        if isinstance(value, Op):
            if value.op in ("IS NOT NULL",):
                clauses.append(f"{column} IS NOT NULL")
            elif value.op in ("IN", "NOT IN"):
                if not value.value:
                    clauses.append("1 = 0" if value.op == "IN" else "1 = 1")
                else:
                    clauses.append(f"{column} {value.op} ({', '.join('?' for _ in value.value)})")
                    params.extend(value.value)
            else:
                clauses.append(f"{column} {value.op} ?")
                params.append(value.value)
        elif value is None:
            clauses.append(f"{column} IS NULL")
        else:
            clauses.append(f"{column} = ?")
            params.append(value)
    return (" AND ".join(clauses) or "1 = 1"), params


async def find(
    db: Any,
    table: str,
    where: Mapping[str, Any] | None = None,
    *,
    order: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    columns: str = "*",
    deleted: bool = False,
) -> list[Row]:
    clause, params = conditions(where)
    if not deleted and not (where and "deleted_at" in where):
        clause += " AND deleted_at IS NULL"
    sql = f"SELECT {columns} FROM {table} WHERE {clause}"
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    if offset:
        sql += f" OFFSET {int(offset)}"
    return [Row(r) for r in await db.fetch(sql, params)]


async def first(db: Any, table: str, where: Mapping[str, Any] | None = None, **kwargs: Any) -> Row | None:
    rows = await find(db, table, where, limit=1, **kwargs)
    return rows[0] if rows else None


async def get(db: Any, table: str, record_id: Any, *, deleted: bool = False) -> Row | None:
    if record_id is None:
        return None
    return await first(db, table, {"id": record_id}, deleted=deleted)


async def count(db: Any, table: str, where: Mapping[str, Any] | None = None, *, deleted: bool = False) -> int:
    clause, params = conditions(where)
    if not deleted and not (where and "deleted_at" in where):
        clause += " AND deleted_at IS NULL"
    return int(await db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {clause}", params, default=0))


async def exists(db: Any, table: str, where: Mapping[str, Any] | None = None) -> bool:
    return await count(db, table, where) > 0


async def total(db: Any, table: str, column: str, where: Mapping[str, Any] | None = None) -> int:
    clause, params = conditions(where)
    return int(await db.scalar(f"SELECT COALESCE(SUM({column}), 0) FROM {table} WHERE {clause} AND deleted_at IS NULL", params, default=0))


async def insert(db: Any, table: str, data: Mapping[str, Any]) -> Row:
    return Row(await db.insert(table, data))


async def update(db: Any, table: str, record_id: Any, data: Mapping[str, Any]) -> None:
    await db.update(table, record_id, data)


async def update_where(db: Any, table: str, where: Mapping[str, Any], data: Mapping[str, Any]) -> int:
    """Set columns on every row matching *where*."""
    clause, params = conditions(where)
    sets = ", ".join(f"{column} = ?" for column in data)
    values = list(data.values())
    if "updated_at" not in data:
        sets += ", updated_at = ?"
        values.append(dt.datetime.now(dt.UTC))
    return await db.execute(f"UPDATE {table} SET {sets} WHERE {clause}", [*values, *params])


async def save(db: Any, table: str, record: "Row", *fields: str) -> "Row":
    """Persist a row mutated in memory: the named fields, or every column but the key and timestamps."""
    names = fields or tuple(k for k in record if k not in ("id", "created_at", "updated_at"))
    await db.update(table, record["id"], {name: record.get(name) for name in names})
    return record


async def soft_delete(db: Any, table: str, record_id: Any) -> None:
    await db.update(table, record_id, {"deleted_at": dt.datetime.now(dt.UTC)})


async def increment(db: Any, table: str, record_id: Any, **deltas: int) -> int:
    """Atomic ``column = column + n`` on one row."""
    sets = ", ".join(f"{column} = {column} + ?" for column in deltas)
    return await db.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*deltas.values(), record_id])


async def get_or_create(db: Any, table: str, where: Mapping[str, Any], defaults: Mapping[str, Any] | None = None) -> tuple[Row, bool]:
    found = await first(db, table, where)
    if found is not None:
        return found, False
    return await insert(db, table, {**where, **(defaults or {})}), True


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def parse_dt(value: Any) -> dt.datetime | None:
    """A datetime from what a column holds: a datetime, or an ISO string."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=dt.UTC)
