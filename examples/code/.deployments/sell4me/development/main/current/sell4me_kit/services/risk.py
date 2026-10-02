"""Payment risk signals: a foundation, honestly labelled as one.

Not a fraud engine. A few cheap, explainable signals (card testing, a card tried repeatedly,
an order far outside a store's normal range). It never *blocks*: every signal adds points and a
flag the merchant sees and decides on. ``SIGNALS`` is a list of independent functions returning
``(points, flag, explanation)``; a rules engine or a provider's own score is another entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import q

REVIEW_THRESHOLD = 40
ALERT_THRESHOLD = 70


@dataclass(slots=True)
class Assessment:
    score: int
    flags: list[dict[str, Any]]

    @property
    def level(self) -> str:
        return "high" if self.score >= ALERT_THRESHOLD else "medium" if self.score >= REVIEW_THRESHOLD else "low"


async def _failed_attempts(db: Any, store: q.Row, order: q.Row) -> tuple[int, str, str]:
    """Recent declines from this email or address: card testing looks like many attempts, almost all failing."""
    since = datetime.now(UTC) - timedelta(hours=1)
    if order.client_ip:
        failures = await q.count(db, "payment_attempts", {"store_id": store.pk, "status": "failed", "created_at": q.gte(since), "client_ip": order.client_ip})
    else:
        failures = int(await db.scalar(
            "SELECT COUNT(*) FROM payment_attempts a JOIN orders o ON o.id = a.order_id "
            "WHERE a.store_id = ? AND a.status = 'failed' AND a.created_at >= ? AND o.email = ? AND a.deleted_at IS NULL",
            [store.pk, since, order.email], default=0))
    if failures >= 5:
        return 40, "many_failed_attempts", f"{failures} failed payment attempts in the last hour"
    if failures >= 3:
        return 25, "repeated_failures", f"{failures} failed payment attempts in the last hour"
    return 0, "", ""


async def _unusual_value(db: Any, store: q.Row, order: q.Row) -> tuple[int, str, str]:
    """An order far above this store's own average (needs ten paid orders as a baseline)."""
    row = await db.one("SELECT COUNT(*) AS n, AVG(total_minor) AS average FROM orders WHERE store_id = ? AND payment_status = 'paid' AND deleted_at IS NULL", [store.pk]) or {}
    paid_count, average = int(row.get("n") or 0), int(row.get("average") or 0)
    if paid_count < 10 or average <= 0:
        return 0, "", ""
    if order.total_minor > average * 10:
        return 30, "unusual_value", "Order is more than 10× this store's average"
    if order.total_minor > average * 5:
        return 15, "elevated_value", "Order is more than 5× this store's average"
    return 0, "", ""


async def _velocity(db: Any, store: q.Row, order: q.Row) -> tuple[int, str, str]:
    since = datetime.now(UTC) - timedelta(minutes=15)
    recent = await q.count(db, "orders", {"store_id": store.pk, "email": order.email, "created_at": q.gte(since), "id": q.ne(order.pk)})
    if recent >= 3:
        return 25, "high_velocity", f"{recent + 1} orders from this email in 15 minutes"
    return 0, "", ""


async def _new_customer_large_order(db: Any, store: q.Row, order: q.Row) -> tuple[int, str, str]:
    if order.customer_id is None:
        return 5, "guest_checkout", "Guest checkout"
    customer = await q.get(db, "customers", order.customer_id)
    if customer is None or (customer.orders_count or 0) > 0:
        return 0, "", ""
    if order.total_minor > 30000:
        return 10, "new_customer_large_order", "First order, and a large one"
    return 0, "", ""


async def _mismatched_addresses(db: Any, store: q.Row, order: q.Row) -> tuple[int, str, str]:
    addresses = {a.kind: a for a in await q.find(db, "order_addresses", {"order_id": order.pk})}
    shipping, billing = addresses.get("shipping"), addresses.get("billing")
    if shipping and billing and shipping.country != billing.country:
        return 15, "address_mismatch", f"Ships to {shipping.country}, billed in {billing.country}"
    return 0, "", ""


async def _refund_history(db: Any, store: q.Row, order: q.Row) -> tuple[int, str, str]:
    if order.customer_id is None:
        return 0, "", ""
    refunded = await q.count(db, "orders", {"store_id": store.pk, "customer_id": order.customer_id, "payment_status": q.in_(["refunded", "partially_refunded"])})
    if refunded >= 3:
        return 20, "refund_history", f"{refunded} previous refunds from this customer"
    return 0, "", ""


SIGNALS = (_failed_attempts, _unusual_value, _velocity, _new_customer_large_order, _mismatched_addresses, _refund_history)


async def assess(c: Any, *, store: q.Row, order: q.Row) -> Assessment:
    """Score an order and record the result on it. Additive, never short-circuits; a failing signal is skipped."""
    db = await c.db()
    score, flags = 0, []
    for signal in SIGNALS:
        try:
            points, flag, explanation = await signal(db, store, order)
        except Exception:  # noqa: BLE001
            continue
        if points and flag:
            score += points
            flags.append({"code": flag, "points": points, "reason": explanation})
    score = min(score, 100)
    await q.update(db, "orders", order.pk, {"risk_score": score, "risk_flags": flags})
    order["risk_score"], order["risk_flags"] = score, flags
    if score >= ALERT_THRESHOLD:
        await q.insert(db, "notifications", {"store_id": store.pk, "kind": "order.risk", "title": f"Order #{order.number} looks risky",
                       "body": "; ".join(f["reason"] for f in flags)[:500], "url": f"/orders/{order.pk}", "level": "warning",
                       "required_permission": "orders.read"})
    if order.customer_id:
        await q.update(db, "customers", order.customer_id, {"risk_score": score})
    return Assessment(score=score, flags=flags)


async def record_failure(c: Any, *, store: q.Row, order: q.Row, payment: q.Row) -> None:
    """Note a declined payment against the customer. A single decline is nothing; the value is the count, read on the next order."""
    if order.customer_id is None:
        return
    db = await c.db()
    recent = int(await db.scalar(
        "SELECT COUNT(*) FROM payment_attempts a JOIN orders o ON o.id = a.order_id "
        "WHERE a.store_id = ? AND a.status = 'failed' AND o.email = ? AND a.deleted_at IS NULL", [store.pk, order.email], default=0))
    if recent >= 5:
        await q.update(db, "customers", order.customer_id, {"risk_score": 80})
