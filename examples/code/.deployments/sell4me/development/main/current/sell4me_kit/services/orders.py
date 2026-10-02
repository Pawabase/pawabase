"""Orders: creating them, moving them through their lifecycle, refunding them.

Every state change goes through a function here and every one writes an ``order_events`` row,
which is what makes an order's timeline trustworthy. ``status``, ``payment_status`` and
``fulfilment_status`` are three columns because they genuinely move independently: an order
can be paid and unshipped, or shipped and then refunded.

    pending --paid--> paid --> processing --> shipped --> delivered
       |                |                                     |
       +--cancelled     +--> refunded / partially_refunded <--+
                        +--> cancelled
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .. import q
from ..events import emit
from ..money import Money, allocate
from . import inventory

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("paid", "cancelled"),
    "paid": ("processing", "shipped", "cancelled", "refunded", "partially_refunded"),
    "processing": ("shipped", "cancelled", "refunded", "partially_refunded"),
    "shipped": ("delivered", "refunded", "partially_refunded"),
    "delivered": ("refunded", "partially_refunded"),
    "partially_refunded": ("refunded", "delivered", "shipped"),
    "cancelled": (),
    "refunded": (),
}


class OrderError(Exception):
    """An order was asked to do something its state does not allow."""


async def next_order_number(db: Any, store: q.Row) -> int:
    """The next per-store order number (the unique ``(store_id, number)`` index makes a race a retry, not a duplicate)."""
    highest = await db.scalar("SELECT MAX(number) FROM orders WHERE store_id = ?", [store.pk])
    return (int(highest) + 1) if highest else 1001


async def record_event(db: Any, order: q.Row, kind: str, message: str, *, data: dict[str, Any] | None = None,
                       actor_id: str | None = None, customer_visible: bool = False) -> q.Row:
    return await q.insert(db, "order_events", {"order_id": order.pk, "kind": kind, "message": message, "data": data or {},
                                               "actor_id": actor_id, "is_customer_visible": customer_visible})


def _assert_transition(order: q.Row, target: str) -> None:
    allowed = TRANSITIONS.get(order.status, ())
    if target not in allowed:
        raise OrderError(f"Order #{order.number} is {order.status}; it cannot become {target}. "
                         f"Allowed from here: {', '.join(allowed) or 'nothing — this is a final state'}.")


async def build_order_from_cart(
    c: Any, *, store: q.Row, cart: q.Row, email: str, shipping_address: dict[str, Any] | None,
    billing_address: dict[str, Any] | None, shipping_minor: int, shipping_method: str | None, discount_minor: int,
    tax_minor: int = 0, customer: q.Row | None = None, source: str = "storefront", client_ip: str | None = None,
    pos_session_id: int | None = None,
) -> q.Row:
    """Turn a cart into a pending order.

    Prices are re-read from the variants, not taken from cart lines, so a stale basket cannot be a
    pricing exploit. The order-level discount is allocated across lines with ``allocate`` so the shares
    sum exactly, which makes a later partial refund computable per line without rounding drift.
    """
    db = await c.db()
    rows = await db.fetch(
        "SELECT ci.quantity, v.id AS variant_id, v.title AS variant_title, v.sku, v.price_minor, v.cost_minor, v.product_id, "
        "p.title AS product_title, p.requires_shipping FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id "
        "JOIN products p ON p.id = v.product_id WHERE ci.cart_id = ? AND ci.deleted_at IS NULL ORDER BY ci.id", [cart.pk])
    if not rows:
        raise OrderError("Cannot create an order from an empty cart.")
    lines = [{**r, "line_total": r["price_minor"] * r["quantity"]} for r in rows]
    subtotal = sum(l["line_total"] for l in lines)
    discount_minor = max(0, min(discount_minor, subtotal))
    shares = allocate(discount_minor, [l["line_total"] for l in lines])
    total = subtotal + shipping_minor + tax_minor - discount_minor

    async with c.tx() as tx:
        order = await q.insert(tx, "orders", {
            "store_id": store.pk, "number": await next_order_number(tx, store), "customer_id": customer.pk if customer else None,
            "email": email, "currency": store.currency, "subtotal_minor": subtotal, "discount_minor": discount_minor,
            "shipping_minor": shipping_minor, "tax_minor": tax_minor, "total_minor": total, "status": "pending",
            "payment_status": "unpaid", "fulfilment_status": "unfulfilled", "discount_id": cart.discount_id,
            "discount_code": cart.discount_code, "shipping_method": shipping_method, "source": source,
            "cart_token": cart.token, "client_ip": client_ip, "pos_session_id": pos_session_id, "placed_at": datetime.now(UTC),
            "refunded_minor": 0, "risk_score": 0, "risk_flags": [], "tags": []})
        for line, share in zip(lines, shares, strict=True):
            await q.insert(tx, "order_items", {
                "order_id": order.pk, "variant_id": line["variant_id"], "product_id": line["product_id"], "title": line["product_title"],
                "variant_title": line["variant_title"], "sku": line["sku"], "quantity": line["quantity"],
                "unit_price_minor": line["price_minor"], "discount_minor": share, "total_minor": line["line_total"] - share,
                "cost_minor": line["cost_minor"], "requires_shipping": bool(line["requires_shipping"]),
                "tax_minor": 0, "quantity_fulfilled": 0, "quantity_refunded": 0})
        for kind, address in (("shipping", shipping_address), ("billing", billing_address)):
            if address:
                await q.insert(tx, "order_addresses", {"order_id": order.pk, "kind": kind, **address})
        await record_event(tx, order, "order.created", f"Order placed for {Money(total, store.currency).format()}",
                           data={"items": len(lines), "source": source}, customer_visible=True)
    assert order.subtotal_minor + order.shipping_minor + order.tax_minor - order.discount_minor == order.total_minor, (
        f"Order #{order.number} totals do not balance")
    await emit(c, "order.created", store=store, order=order, customer=customer)
    return order


async def mark_paid(c: Any, *, store: q.Row, order: q.Row, payment_reference: str | None = None, amount_minor: int | None = None) -> q.Row:
    """Move an order to paid and commit its stock. **Idempotent**: a webhook, a return-URL check and a
    reconciliation may all call it for one payment, and any may arrive twice."""
    db = await c.db()
    order = await q.get(db, "orders", order.pk) or order
    if order.payment_status in ("paid", "partially_refunded"):
        return order
    _assert_transition(order, "paid")
    for item in await q.find(db, "order_items", {"order_id": order.pk}):
        if item.variant_id:
            await inventory.commit_reservation(c, store=store, variant_id=item.variant_id, quantity=item.quantity, order_id=order.pk)
    now = datetime.now(UTC)
    await q.update(db, "orders", order.pk, {"status": "paid", "payment_status": "paid", "paid_at": now})
    if order.cart_token:
        await q.update_where(db, "carts", {"token": order.cart_token}, {"status": "converted", "converted_at": now})
    order = await q.get(db, "orders", order.pk)
    if order.customer_id:
        await _touch_customer(db, order)
    if order.discount_id:
        await _record_discount_usage(db, store, order)
    await record_event(db, order, "payment.succeeded",
                       f"Payment of {Money(amount_minor or order.total_minor, order.currency).format()} received",
                       data={"reference": payment_reference}, customer_visible=True)
    return order


async def _touch_customer(db: Any, order: q.Row) -> None:
    """Roll the order into the customer's lifetime figures with atomic increments."""
    customer = await q.get(db, "customers", order.customer_id)
    if customer is None:
        return
    await db.execute(
        "UPDATE customers SET orders_count = orders_count + 1, total_spent_minor = total_spent_minor + ?, last_order_at = ?, "
        "first_order_at = COALESCE(first_order_at, ?) WHERE id = ?",
        [order.total_minor, q.parse_dt(order.paid_at), q.parse_dt(order.paid_at), customer.pk])


async def _record_discount_usage(db: Any, store: q.Row, order: q.Row) -> None:
    _, created = await q.get_or_create(db, "discount_usages", {"discount_id": order.discount_id, "order_id": order.pk},
                                       {"store_id": store.pk, "customer_id": order.customer_id, "amount_minor": order.discount_minor})
    if created:
        await q.increment(db, "discounts", order.discount_id, usage_count=1)


async def fulfil(c: Any, *, store: q.Row, order: q.Row, tracking_number: str | None = None, tracking_url: str | None = None,
                 carrier: str | None = None, actor_id: str | None = None) -> q.Row:
    """Mark an order shipped."""
    db = await c.db()
    if not order.is_paid:
        raise OrderError(f"Order #{order.number} has not been paid.")
    _assert_transition(order, "shipped")
    await q.update(db, "orders", order.pk, {"status": "shipped", "fulfilment_status": "fulfilled", "tracking_number": tracking_number,
                                            "tracking_url": tracking_url, "fulfilled_at": datetime.now(UTC)})
    await db.execute("UPDATE order_items SET quantity_fulfilled = quantity WHERE order_id = ?", [order.pk])
    order = await q.get(db, "orders", order.pk)
    await record_event(db, order, "order.fulfilled",
                       f"Shipped{f' via {carrier}' if carrier else ''}" + (f" · {tracking_number}" if tracking_number else ""),
                       data={"tracking_number": tracking_number, "carrier": carrier}, actor_id=actor_id, customer_visible=True)
    await emit(c, "order.fulfilled", store=store, order=order, actor=actor_id)
    return order


async def mark_delivered(c: Any, *, store: q.Row, order: q.Row, actor_id: str | None = None) -> q.Row:
    db = await c.db()
    _assert_transition(order, "delivered")
    await q.update(db, "orders", order.pk, {"status": "delivered"})
    order = await q.get(db, "orders", order.pk)
    await record_event(db, order, "order.delivered", "Marked delivered", actor_id=actor_id, customer_visible=True)
    await emit(c, "order.delivered", store=store, order=order)
    return order


async def cancel(c: Any, *, store: q.Row, order: q.Row, reason: str | None = None, actor_id: str | None = None) -> q.Row:
    """Cancel an order and return its stock: restock if it was paid (units were sold), release if pending (units were reserved)."""
    db = await c.db()
    _assert_transition(order, "cancelled")
    was_paid = order.is_paid
    for item in await q.find(db, "order_items", {"order_id": order.pk}):
        if not item.variant_id:
            continue
        if was_paid:
            variant = await q.get(db, "product_variants", item.variant_id)
            if variant is not None:
                await inventory.adjust(c, store=store, variant=variant, delta=item.quantity, reason="returned",
                                       note=f"Order #{order.number} cancelled", reference_type="order", reference_id=order.pk, actor_id=actor_id)
        else:
            await inventory.release_reservation(c, variant_id=item.variant_id, quantity=item.quantity)
    await q.update(db, "orders", order.pk, {"status": "cancelled", "cancelled_at": datetime.now(UTC)})
    order = await q.get(db, "orders", order.pk)
    await record_event(db, order, "order.cancelled", reason or "Order cancelled", actor_id=actor_id, customer_visible=True)
    await emit(c, "order.cancelled", store=store, order=order, reason=reason)
    return order


def order_summary(order: q.Row) -> dict[str, Any]:
    """The money on an order, shaped for the API (one place, so every screen formats the same figures the same way)."""
    cur = order.currency
    return {k: Money(order.get(f"{k}_minor") or 0, cur).as_prop() for k in ("subtotal", "discount", "shipping", "tax", "total", "refunded")} | {
        "net": Money(order.net_minor, cur).as_prop()}
