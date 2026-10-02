"""The numbers, and the honest way to get them.

Two sources, chosen by age: **today** is computed live from ``orders`` and ``ledger_entries`` (a merchant watching sales
come in needs the current hour to be current); **anything before today** is read from ``daily_stats``, which the nightly job
writes. ``aggregate_day`` recomputes a whole day and *writes* totals rather than incrementing, so running it twice (or again
after a late webhook) converges. Where a number cannot be known it is absent, not invented: conversion needs sessions, and a
store with none gets ``null`` ("not enough data"), which is true, instead of ``0%``, which is a claim.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .. import q
from ..money import Money

RANGES: dict[str, tuple[str, int]] = {
    "today": ("Today", 1), "7d": ("Last 7 days", 7), "30d": ("Last 30 days", 30), "90d": ("Last 90 days", 90),
    "ytd": ("Year to date", 0), "12m": ("Last 12 months", 365),
}
PAID = "('paid', 'partially_refunded', 'refunded')"


def range_bounds(key: str, *, start: str | None = None, end: str | None = None) -> tuple[datetime, datetime, str]:
    """A range key as ``(since, until, label)``. ``until`` is exclusive and always the start of tomorrow."""
    today = datetime.now(UTC).date()
    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    if key == "custom" and start:
        since = datetime.combine(date.fromisoformat(start), datetime.min.time(), tzinfo=UTC)
        until = datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), tzinfo=UTC) if end else tomorrow
        return since, until, f"{start} – {end or 'today'}"
    if key == "ytd":
        return datetime(today.year, 1, 1, tzinfo=UTC), tomorrow, "Year to date"
    label, days = RANGES.get(key, RANGES["30d"])
    return datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), tzinfo=UTC), tomorrow, label


async def _fees(db: Any, store_id: int, since: datetime, until: datetime) -> dict[str, int]:
    rows = await db.fetch("SELECT kind, COALESCE(SUM(amount_minor), 0) AS total FROM ledger_entries WHERE store_id = ? AND kind IN ('provider_fee', 'platform_fee') "
                          "AND occurred_at >= ? AND occurred_at < ? AND deleted_at IS NULL GROUP BY kind", [store_id, since, until])
    return {r["kind"]: -int(r["total"] or 0) for r in rows}


async def _session_count(db: Any, store_id: int, since: datetime, until: datetime) -> int:
    """Distinct storefront sessions in the window. A session is a cart token, which every visitor has: no third-party analytics needed."""
    return int(await db.scalar("SELECT COUNT(DISTINCT session_token) FROM storefront_events WHERE store_id = ? AND created_at >= ? AND created_at < ? "
                               "AND session_token IS NOT NULL AND deleted_at IS NULL", [store_id, since, until], default=0))


async def overview(db: Any, *, store: q.Row, since: datetime, until: datetime) -> dict[str, Any]:
    """The headline figures for a window; every money value is a ``Money`` prop, so no screen formats currency itself."""
    row = await db.one(f"SELECT COALESCE(SUM(total_minor), 0) AS gross, COALESCE(SUM(refunded_minor), 0) AS refunded, COUNT(*) AS orders FROM orders "
                       f"WHERE store_id = ? AND payment_status IN {PAID} AND paid_at >= ? AND paid_at < ? AND deleted_at IS NULL", [store.pk, since, until]) or {}
    gross, refunded, order_count = int(row.get("gross") or 0), int(row.get("refunded") or 0), int(row.get("orders") or 0)
    fees = await _fees(db, store.pk, since, until)
    new_customers = int(await db.scalar(
        "SELECT COUNT(*) FROM customers WHERE store_id = ? AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", [store.pk, since, until], default=0))
    ok = int(await db.scalar("SELECT COUNT(*) FROM payments WHERE store_id = ? AND status = 'succeeded' AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", [store.pk, since, until], default=0))
    failed = int(await db.scalar("SELECT COUNT(*) FROM payments WHERE store_id = ? AND status = 'failed' AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", [store.pk, since, until], default=0))
    sessions = await _session_count(db, store.pk, since, until)
    cur = store.currency
    net = gross - refunded - fees.get("provider_fee", 0) - fees.get("platform_fee", 0)
    return {
        "gross": Money(gross, cur).as_prop(), "net": Money(net, cur).as_prop(), "refunded": Money(refunded, cur).as_prop(),
        "provider_fees": Money(fees.get("provider_fee", 0), cur).as_prop(), "platform_fees": Money(fees.get("platform_fee", 0), cur).as_prop(),
        "orders": order_count, "new_customers": new_customers, "average_order": Money(gross // order_count if order_count else 0, cur).as_prop(),
        "payments_succeeded": ok, "payments_failed": failed,
        "payment_failure_rate": round(failed / (ok + failed), 4) if (ok + failed) else None,
        "conversion_rate": round(order_count / sessions, 4) if sessions else None,
        "refund_rate": round(refunded / gross, 4) if gross else None, "sessions": sessions,
    }


async def series(db: Any, *, store: q.Row, since: datetime, until: datetime, metric: str = "revenue") -> list[dict[str, Any]]:
    """A day-by-day series for the charts: closed days from ``daily_stats``, today live, and a zero row for any missing day (a gap would imply missing data)."""
    start, end, today = since.date(), (until - timedelta(seconds=1)).date(), datetime.now(UTC).date()
    stored = {str(r["date"])[:10]: q.Row(r) for r in await db.fetch("SELECT * FROM daily_stats WHERE store_id = ? AND date >= ? AND date <= ? AND deleted_at IS NULL",
                                                                    [store.pk, start.isoformat(), end.isoformat()])}
    points: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        if cursor == today:
            points.append(await _live_day(db, store, cursor))
        elif key in stored:
            row = stored[key]
            points.append({"date": key, "revenue_minor": row.gross_minor - row.refund_minor, "orders": row.paid_orders_count,
                           "customers": row.new_customers, "sessions": row.sessions})
        else:
            points.append({"date": key, "revenue_minor": 0, "orders": 0, "customers": 0, "sessions": 0})
        cursor += timedelta(days=1)
    for point in points:
        point["revenue"] = Money(point["revenue_minor"], store.currency).as_prop()
    return points


async def _live_day(db: Any, store: q.Row, day: date) -> dict[str, Any]:
    since = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    until = since + timedelta(days=1)
    row = await db.one(f"SELECT COALESCE(SUM(total_minor), 0) AS gross, COALESCE(SUM(refunded_minor), 0) AS refunded, COUNT(*) AS orders FROM orders "
                       f"WHERE store_id = ? AND payment_status IN {PAID} AND paid_at >= ? AND paid_at < ? AND deleted_at IS NULL", [store.pk, since, until]) or {}
    customers = int(await db.scalar("SELECT COUNT(*) FROM customers WHERE store_id = ? AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", [store.pk, since, until], default=0))
    return {"date": day.isoformat(), "revenue_minor": int(row.get("gross") or 0) - int(row.get("refunded") or 0), "orders": int(row.get("orders") or 0),
            "customers": customers, "sessions": await _session_count(db, store.pk, since, until)}


async def aggregate_day(db: Any, store: q.Row, day: date) -> q.Row:
    """Recompute one day's row from scratch. Idempotent by construction: it computes totals and *writes* them."""
    since = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    until = since + timedelta(days=1)
    o = await db.one("SELECT COALESCE(SUM(total_minor), 0) AS gross, COALESCE(SUM(discount_minor), 0) AS discount, COALESCE(SUM(shipping_minor), 0) AS shipping, "
                     "COALESCE(SUM(tax_minor), 0) AS tax, COALESCE(SUM(refunded_minor), 0) AS refunded, COUNT(*) AS n FROM orders "
                     "WHERE store_id = ? AND paid_at >= ? AND paid_at < ? AND deleted_at IS NULL", [store.pk, since, until]) or {}
    fees = await _fees(db, store.pk, since, until)
    events = {r["kind"]: int(r["n"]) for r in await db.fetch("SELECT kind, COUNT(*) AS n FROM storefront_events WHERE store_id = ? AND created_at >= ? AND created_at < ? "
                                                             "AND deleted_at IS NULL GROUP BY kind", [store.pk, since, until])}
    gross, refunds = int(o.get("gross") or 0), int(o.get("refunded") or 0)

    async def count(sql: str, *params: Any) -> int:
        return int(await db.scalar(sql, [store.pk, *params], default=0))

    values = {
        "orders_count": await count("SELECT COUNT(*) FROM orders WHERE store_id = ? AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", since, until),
        "paid_orders_count": int(o.get("n") or 0),
        "cancelled_orders_count": await count("SELECT COUNT(*) FROM orders WHERE store_id = ? AND status = 'cancelled' AND cancelled_at >= ? AND cancelled_at < ? AND deleted_at IS NULL", since, until),
        "gross_minor": gross, "discount_minor": int(o.get("discount") or 0), "shipping_minor": int(o.get("shipping") or 0), "tax_minor": int(o.get("tax") or 0),
        "refund_minor": refunds, "provider_fee_minor": fees.get("provider_fee", 0), "platform_fee_minor": fees.get("platform_fee", 0),
        "net_minor": gross - refunds - fees.get("provider_fee", 0) - fees.get("platform_fee", 0),
        "new_customers": await count("SELECT COUNT(*) FROM customers WHERE store_id = ? AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", since, until),
        "returning_customers": await count("SELECT COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.store_id = ? AND o.paid_at >= ? AND o.paid_at < ? "
                                           "AND c.orders_count > 1 AND o.deleted_at IS NULL", since, until),
        "sessions": await _session_count(db, store.pk, since, until),
        "product_views": events.get("product_view", 0), "add_to_carts": events.get("add_to_cart", 0), "checkouts_started": events.get("begin_checkout", 0),
        "payments_succeeded": await count("SELECT COUNT(*) FROM payments WHERE store_id = ? AND status = 'succeeded' AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", since, until),
        "payments_failed": await count("SELECT COUNT(*) FROM payments WHERE store_id = ? AND status = 'failed' AND created_at >= ? AND created_at < ? AND deleted_at IS NULL", since, until),
    }
    stat = await q.first(db, "daily_stats", {"store_id": store.pk, "date": day.isoformat()})
    if stat is None:
        return await q.insert(db, "daily_stats", {"store_id": store.pk, "date": day.isoformat(), **values})
    await q.update(db, "daily_stats", stat.pk, values)
    return await q.get(db, "daily_stats", stat.pk)


async def product_performance(db: Any, *, store: q.Row, since: datetime, until: datetime, limit: int = 20) -> list[dict[str, Any]]:
    """Top products by revenue with the funnel that produced it. Conversion is ``purchases / views`` and ``null`` when never viewed (not 0%)."""
    sold = await db.fetch(
        "SELECT oi.product_id, COALESCE(SUM(oi.total_minor), 0) AS revenue, COALESCE(SUM(oi.quantity), 0) AS units, COALESCE(SUM(oi.quantity_refunded), 0) AS refunds "
        "FROM order_items oi JOIN orders o ON o.id = oi.order_id WHERE o.store_id = ? AND o.payment_status IN ('paid', 'partially_refunded') "
        "AND o.paid_at >= ? AND o.paid_at < ? AND oi.deleted_at IS NULL GROUP BY oi.product_id", [store.pk, since, until])
    by_product = {r["product_id"]: r for r in sold if r["product_id"]}
    funnel: dict[int, dict[str, int]] = {}
    for r in await db.fetch("SELECT product_id, kind, COUNT(*) AS n FROM storefront_events WHERE store_id = ? AND kind IN ('product_view', 'add_to_cart') "
                            "AND created_at >= ? AND created_at < ? AND product_id IS NOT NULL AND deleted_at IS NULL GROUP BY product_id, kind", [store.pk, since, until]):
        funnel.setdefault(r["product_id"], {})[r["kind"]] = int(r["n"])
    ids = set(by_product) | set(funnel)
    if not ids:
        return []
    products = {p.pk: p for p in await q.find(db, "products", {"id": q.in_(list(ids))})}
    rows: list[dict[str, Any]] = []
    for pid in ids:
        product = products.get(pid)
        if product is None:
            continue
        sales, steps = by_product.get(pid, {}), funnel.get(pid, {})
        views, units = steps.get("product_view", 0), int(sales.get("units") or 0)
        rows.append({"id": product.pk, "title": product.title, "slug": product.slug, "status": product.status,
                     "revenue": Money(int(sales.get("revenue") or 0), store.currency).as_prop(), "revenue_minor": int(sales.get("revenue") or 0),
                     "units": units, "refunded_units": int(sales.get("refunds") or 0), "views": views, "add_to_carts": steps.get("add_to_cart", 0),
                     "conversion_rate": round(units / views, 4) if views else None})
    rows.sort(key=lambda row: -row["revenue_minor"])
    return rows[:limit]


async def track(db: Any, *, store: q.Row, kind: str, session_token: str | None = None, product_id: int | None = None, variant_id: int | None = None,
                collection_id: int | None = None, order_id: int | None = None, value_minor: int = 0, quantity: int = 0, referrer: str | None = None) -> None:
    """Record a storefront event. Never raises: analytics is the least important thing on a page, and a failed view record must not fail the view."""
    try:
        await q.insert(db, "storefront_events", {"store_id": store.pk, "kind": kind, "session_token": session_token, "product_id": product_id, "variant_id": variant_id,
                                                  "collection_id": collection_id, "order_id": order_id, "value_minor": value_minor, "quantity": quantity,
                                                  "referrer": (referrer or "")[:500] or None})
    except Exception:  # noqa: BLE001
        return
