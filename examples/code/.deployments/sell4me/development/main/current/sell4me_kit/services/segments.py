"""Customer segments: a saved query, resolved when it is read.

``rules`` is a list of ``{field, operator, value}`` compiled into SQL here, so a customer who
crossed a threshold an hour ago is in the segment now. The compilable fields are the ones the
``customers`` table maintains, which keeps every segment answerable with one indexed query.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .. import q

FIELDS: dict[str, tuple[str, str]] = {
    "total_spent_minor": ("Total spent", "money"),
    "orders_count": ("Number of orders", "number"),
    "last_order_at": ("Last order", "date"),
    "first_order_at": ("First order", "date"),
    "created_at": ("Signed up", "date"),
    "accepts_marketing": ("Accepts marketing", "boolean"),
    "tags": ("Tag", "tag"),
    "risk_score": ("Risk score", "number"),
}

#: operator -> (label, SQL operator)
OPERATORS: dict[str, tuple[str, str]] = {
    "eq": ("is", "="),
    "ne": ("is not", "<>"),
    "gt": ("is more than", ">"),
    "gte": ("is at least", ">="),
    "lt": ("is less than", "<"),
    "lte": ("is at most", "<="),
    "contains": ("contains", "LIKE"),
    "is_null": ("is empty", "IS NULL"),
    "days_ago_gt": ("is more than N days ago", "<"),
    "days_ago_lt": ("is within the last N days", ">="),
}


def compile_rules(rules: list[dict[str, Any]] | None) -> tuple[str, list[Any]]:
    """Rules as a SQL ``WHERE`` fragment (always ``AND``-joined). Unknown fields and operators are skipped."""
    clauses: list[str] = []
    params: list[Any] = []
    for rule in rules or []:
        field = rule.get("field")
        operator = rule.get("operator", "eq")
        value = rule.get("value")
        if field not in FIELDS or operator not in OPERATORS:
            continue
        if operator in ("days_ago_gt", "days_ago_lt"):
            try:
                days = int(value)
            except (TypeError, ValueError):
                continue
            clauses.append(f"{field} {OPERATORS[operator][1]} ?")
            params.append(datetime.now(UTC) - timedelta(days=days))
        elif operator == "is_null":
            clauses.append(f"{field} IS NULL" if value else f"{field} IS NOT NULL")
        elif field == "tags" and operator == "contains":
            clauses.append("CAST(tags AS TEXT) LIKE ?")
            params.append(f'%"{value}"%')
        else:
            clauses.append(f"{field} {OPERATORS[operator][1]} ?")
            params.append(value)
    return (" AND ".join(clauses) or "1 = 1"), params


def _where(store_id: int, segment: q.Row, extra: str = "") -> tuple[str, list[Any]]:
    clause, params = compile_rules(segment.rules)
    return f"store_id = ? AND deleted_at IS NULL AND {clause}{extra}", [store_id, *params]


async def members(db: Any, store_id: int, segment: q.Row, *, limit: int = 50, offset: int = 0) -> list[q.Row]:
    where, params = _where(store_id, segment)
    return [q.Row(r) for r in await db.fetch(f"SELECT * FROM customers WHERE {where} ORDER BY id DESC LIMIT {int(limit)} OFFSET {int(offset)}", params)]


async def count(db: Any, store_id: int, segment: q.Row) -> int:
    where, params = _where(store_id, segment)
    return int(await db.scalar(f"SELECT COUNT(*) FROM customers WHERE {where}", params, default=0))


async def contains(db: Any, segment_id: int, customer: q.Row) -> bool:
    """Whether one customer is in one segment (one indexed query, not a fetch of the segment)."""
    segment = await q.get(db, "segments", segment_id)
    if segment is None:
        return False
    where, params = _where(customer.store_id, segment, " AND id = ?")
    return int(await db.scalar(f"SELECT COUNT(*) FROM customers WHERE {where}", [*params, customer.pk], default=0)) > 0


SYSTEM_SEGMENTS: tuple[dict[str, Any], ...] = (
    {"name": "High-value customers", "description": "Spent more than 500 in your store currency.",
     "rules": [{"field": "total_spent_minor", "operator": "gt", "value": 50000}]},
    {"name": "New customers", "description": "Signed up in the last 30 days.",
     "rules": [{"field": "created_at", "operator": "days_ago_lt", "value": 30}]},
    {"name": "Frequent buyers", "description": "Three or more orders.",
     "rules": [{"field": "orders_count", "operator": "gte", "value": 3}]},
    {"name": "Inactive customers", "description": "Has ordered before, but not in the last 90 days.",
     "rules": [{"field": "orders_count", "operator": "gte", "value": 1},
               {"field": "last_order_at", "operator": "days_ago_gt", "value": 90}]},
    {"name": "Never purchased", "description": "Signed up but has not ordered.",
     "rules": [{"field": "orders_count", "operator": "eq", "value": 0}]},
    {"name": "Marketing subscribers", "description": "Opted in to marketing email.",
     "rules": [{"field": "accepts_marketing", "operator": "eq", "value": True}]},
)


async def seed_system_segments(db: Any, store: q.Row) -> int:
    created = 0
    for spec in SYSTEM_SEGMENTS:
        _, was_created = await q.get_or_create(
            db, "segments", {"store_id": store.pk, "name": spec["name"]},
            {"description": spec["description"], "rules": spec["rules"], "is_system": True},
        )
        created += int(was_created)
    return created


async def refresh_counts(db: Any, store: q.Row) -> None:
    """Recompute every segment's cached size (a cache, shown with its timestamp)."""
    now = datetime.now(UTC)
    for segment in await q.find(db, "segments", {"store_id": store.pk}):
        await q.update(db, "segments", segment.pk, {"cached_count": await count(db, store.pk, segment), "counted_at": now})
