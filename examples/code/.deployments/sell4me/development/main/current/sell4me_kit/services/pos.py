"""Point-of-Sale service layer: sessions, orders built from a terminal's cart, and cash or card payments.

Differences from the online checkout: there is **no cart token** (the cart lives in the browser and the order is built from
the line items the terminal submits, with prices re-read from the variants); **cash bypasses the provider** (a ``payments``
row with ``provider="cash"`` goes through the same ``mark_paid``/``book_charge`` path); **card** goes through Paystack with a
return URL pointing back at the terminal; **sessions are optional but encouraged** (running totals are kept correct when one is open).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from .. import q
from ..events import emit
from ..money import Money, allocate
from . import inventory, ledger
from . import orders as order_svc
from .payments import make_reference


class PosError(Exception):
    """Something went wrong during a POS operation."""


# ── devices ──────────────────────────────────────────────────────────────

async def device_for_token(db: Any, store: q.Row, token: str) -> q.Row | None:
    return await q.first(db, "pos_devices", {"store_id": store.pk, "token": token, "is_active": True})


async def device_for_domain(db: Any, host: str) -> q.Row | None:
    """The active device whose custom domain is this host (stored exactly as a browser sends it)."""
    hostname = _bare_hostname(host)
    if not hostname:
        return None
    return await q.first(db, "pos_devices", {"pos_domain": hostname, "pos_domain_status": "verified", "is_active": True})


def normalize_pos_domain(raw: str | None) -> str | None:
    """The hostname a device domain should store: scheme, path and case stripped, because dispatch matches it against the ``Host`` header."""
    return (_bare_hostname(raw) or None) if raw is not None else None


def _bare_hostname(value: str) -> str:
    """The host of a URL, lowercased: scheme and path gone, port kept (a register and a dashboard can differ only by port in development)."""
    text = (value or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("/", 1)[0].rstrip(".")


async def create_device(db: Any, store: q.Row, label: str, pos_domain: str | None = None) -> q.Row:
    return await q.insert(db, "pos_devices", {
        "store_id": store.pk, "label": label, "token": secrets.token_urlsafe(32), "pos_domain": pos_domain,
        "pos_domain_status": "pending" if pos_domain else None,
        "pos_domain_verification_token": secrets.token_urlsafe(24) if pos_domain else None,
        "print_receipt_auto": False, "is_active": True})


# ── sessions ─────────────────────────────────────────────────────────────

async def open_session(c: Any, *, store: q.Row, member: q.Row, device: q.Row | None = None, opening_float_minor: int = 0) -> q.Row:
    """Start a cashier session. A terminal cannot run two sessions at once, but a store can run several terminals."""
    db = await c.db()
    if device is not None and await q.exists(db, "pos_sessions", {"device_id": device.pk, "status": "open"}):
        raise PosError(f"Device '{device.label}' already has an open session. Close it before starting a new one.")
    return await q.insert(db, "pos_sessions", {"store_id": store.pk, "device_id": device.pk if device else None, "opened_by_id": member.user_id,
                                               "status": "open", "opening_float_minor": opening_float_minor, "opened_at": datetime.now(UTC)})


async def close_session(c: Any, *, session: q.Row, member: q.Row, closing_count_minor: int, note: str | None = None) -> q.Row:
    """Close a session and compute the end-of-day figures from every POS order in it."""
    db = await c.db()
    if session.status != "open":
        raise PosError("Session is already closed.")
    orders = await q.find(db, "orders", {"pos_session_id": session.pk, "store_id": session.store_id})
    total_sales = sum(o.total_minor for o in orders)
    total_refunds = sum(o.refunded_minor or 0 for o in orders)
    cash = card = 0
    for order in orders:
        for payment in await q.find(db, "payments", {"order_id": order.pk}):
            if payment.status != "succeeded":
                continue
            if payment.provider == "cash":
                cash += payment.amount_minor
            else:
                card += payment.amount_minor
    expected_cash = (session.opening_float_minor or 0) + cash
    by_variant: dict[Any, dict[str, Any]] = {}
    for order in orders:
        for item in await q.find(db, "order_items", {"order_id": order.pk}):
            row = by_variant.setdefault(item.variant_id, {"variant_id": item.variant_id, "product_title": item.title, "variant_title": item.variant_title,
                                                          "sku": item.sku, "quantity_sold": 0, "quantity_refunded": 0, "gross_minor": 0, "refunded_minor": 0})
            row["quantity_sold"] += item.quantity
            row["quantity_refunded"] += item.quantity_refunded or 0
            row["gross_minor"] += item.total_minor
            if order.total_minor and order.refunded_minor:  # approximate per line: proportional to the order's refund
                row["refunded_minor"] += int(item.total_minor / order.total_minor * order.refunded_minor)
    async with c.tx() as tx:
        await q.update(tx, "pos_sessions", session.pk, {
            "status": "closed", "closed_by_id": member.user_id, "closed_at": datetime.now(UTC), "closing_count_minor": closing_count_minor,
            "expected_cash_minor": expected_cash, "total_sales_minor": total_sales, "total_refunds_minor": total_refunds, "order_count": len(orders),
            "cash_tendered_minor": cash, "card_tendered_minor": card, "note": note})
        await tx.execute("DELETE FROM pos_session_items WHERE session_id = ?", [session.pk])  # idempotent close support
        for row in by_variant.values():
            await q.insert(tx, "pos_session_items", {"session_id": session.pk, "store_id": session.store_id, **row})
    return await q.get(db, "pos_sessions", session.pk)


def session_summary(session: q.Row, currency: str) -> dict[str, Any]:
    variance = session.variance_minor
    return {
        "id": session.pk, "status": session.status, "opened_at": session.opened_at, "closed_at": session.closed_at,
        "opening_float": Money(session.opening_float_minor or 0, currency).as_prop(), "closing_count": Money(session.closing_count_minor or 0, currency).as_prop(),
        "expected_cash": Money(session.expected_cash_minor or 0, currency).as_prop(), "variance": Money(variance or 0, currency).as_prop(),
        "variance_minor": variance, "total_sales": Money(session.total_sales_minor or 0, currency).as_prop(),
        "total_refunds": Money(session.total_refunds_minor or 0, currency).as_prop(), "order_count": session.order_count or 0,
        "cash_tendered": Money(session.cash_tendered_minor or 0, currency).as_prop(), "card_tendered": Money(session.card_tendered_minor or 0, currency).as_prop(),
        "note": session.note,
    }


# ── orders and payments ──────────────────────────────────────────────────

async def create_pos_order(c: Any, *, store: q.Row, member: q.Row, session: q.Row | None, line_items: list[dict[str, Any]], customer_id: int | None = None,
                           customer_email: str | None = None, note: str | None = None, discount_minor: int = 0) -> q.Row:
    """Build a pending/unpaid order from a POS cart (``[{variant_id, quantity}]``). Prices are always re-read: the client is never trusted for a price."""
    if not line_items:
        raise PosError("Cannot create a POS order with no items.")
    db = await c.db()
    variant_ids = [int(li["variant_id"]) for li in line_items]
    variants = await db.fetch(
        f"SELECT v.*, p.title AS product_title FROM product_variants v JOIN products p ON p.id = v.product_id WHERE v.store_id = ? AND v.deleted_at IS NULL "
        f"AND v.id IN ({', '.join('?' for _ in variant_ids)})", [store.pk, *variant_ids])
    by_id = {v["id"]: q.Row(v) for v in variants}
    if len(by_id) != len(set(variant_ids)):
        raise PosError(f"Unknown variants: {set(variant_ids) - set(by_id)}")
    lines = []
    for li in line_items:
        variant = by_id[int(li["variant_id"])]
        qty = max(1, int(li.get("quantity") or 1))
        lines.append({"variant": variant, "quantity": qty, "unit_price_minor": variant.price_minor, "line_total": variant.price_minor * qty})
    subtotal = sum(l["line_total"] for l in lines)
    discount_minor = max(0, min(discount_minor, subtotal))
    shares = allocate(discount_minor, [l["line_total"] for l in lines])
    total = subtotal - discount_minor  # POS: no shipping, no tax by default

    customer, email = None, customer_email or f"pos-{secrets.token_hex(6)}@pos.internal"
    if customer_id:
        customer = await q.first(db, "customers", {"id": customer_id, "store_id": store.pk})
    elif customer_email:
        customer, _ = await q.get_or_create(db, "customers", {"store_id": store.pk, "email": customer_email.strip().lower()},
                                            {"first_name": "", "last_name": "", "tags": [], "orders_count": 0, "total_spent_minor": 0, "risk_score": 0})
        email = customer.email
    reserved: list[tuple[int, int]] = []
    try:
        for l in lines:
            v = l["variant"]
            if v.track_inventory and not v.allow_backorder and (v.stock - v.reserved) < l["quantity"]:
                raise PosError(f"Not enough stock for '{v.title}': {v.stock - v.reserved} available, {l['quantity']} requested.")
            await inventory.reserve(c, store=store, variant_id=v.pk, quantity=l["quantity"])
            reserved.append((v.pk, l["quantity"]))
        async with c.tx() as tx:
            order = await q.insert(tx, "orders", {
                "store_id": store.pk, "number": await order_svc.next_order_number(tx, store), "customer_id": customer.pk if customer else None, "email": email,
                "currency": store.currency, "subtotal_minor": subtotal, "discount_minor": discount_minor, "shipping_minor": 0, "tax_minor": 0, "total_minor": total,
                "status": "pending", "payment_status": "unpaid", "fulfilment_status": "unfulfilled", "source": "pos", "note": note, "placed_at": datetime.now(UTC),
                "pos_session_id": session.pk if session else None, "refunded_minor": 0, "risk_score": 0, "risk_flags": [], "tags": []})
            for l, share in zip(lines, shares, strict=True):
                v = l["variant"]
                await q.insert(tx, "order_items", {"order_id": order.pk, "variant_id": v.pk, "product_id": v.product_id, "title": v["product_title"], "variant_title": v.title,
                                                   "sku": v.sku, "quantity": l["quantity"], "unit_price_minor": l["unit_price_minor"], "discount_minor": share,
                                                   "total_minor": l["line_total"] - share, "cost_minor": v.cost_minor, "requires_shipping": False,
                                                   "tax_minor": 0, "quantity_fulfilled": 0, "quantity_refunded": 0})
    except BaseException:
        for variant_id, quantity in reserved:
            await inventory.release_reservation(c, variant_id=variant_id, quantity=quantity)
        raise
    await order_svc.record_event(db, order, "order.created", f"POS order created for {Money(total, store.currency).format()} by {member.role}",
                                 data={"source": "pos", "member_id": member.pk, "items": len(lines)})
    await emit(c, "order.created", store=store, order=order, customer=customer)
    return order


async def record_cash_payment(c: Any, *, store: q.Row, order: q.Row, amount_tendered_minor: int, actor_id: str | None = None) -> q.Row:
    """Record a cash payment and move the order to paid. ``amount_tendered_minor`` may exceed the total (change is given); only the
    exact charge amount belongs in the ledger, and the change due is returned in ``raw_response.change_minor`` for the terminal."""
    db = await c.db()
    if order.payment_status == "paid":
        raise PosError(f"Order #{order.number} is already paid.")
    if amount_tendered_minor < order.total_minor:
        raise PosError(f"Amount tendered ({Money(amount_tendered_minor, order.currency).format()}) is less than the order total "
                       f"({Money(order.total_minor, order.currency).format()}).")
    reference = make_reference("cash")
    change = amount_tendered_minor - order.total_minor
    payment = await q.insert(db, "payments", {
        "store_id": store.pk, "order_id": order.pk, "provider": "cash", "provider_reference": reference, "reference": reference, "currency": order.currency,
        "amount_minor": order.total_minor, "provider_fee_minor": 0, "platform_fee_minor": 0, "refunded_minor": 0, "status": "succeeded", "method": "cash",
        "captured_at": datetime.now(UTC), "raw_response": {"provider": "cash", "tendered_minor": amount_tendered_minor, "change_minor": change, "actor_id": actor_id}})
    order = await order_svc.mark_paid(c, store=store, order=order, payment_reference=reference, amount_minor=order.total_minor)
    await ledger.book_charge(c, store=store, order=order, payment=payment)
    await order_svc.record_event(db, order, "payment.succeeded", f"Cash payment of {Money(order.total_minor, order.currency).format()} received"
                                 + (f" · change {Money(change, order.currency).format()}" if change else ""),
                                 data={"provider": "cash", "tendered_minor": amount_tendered_minor, "change_minor": change}, actor_id=actor_id, customer_visible=True)
    await emit(c, "order.paid", store=store, order=order, payment=payment)
    return payment
