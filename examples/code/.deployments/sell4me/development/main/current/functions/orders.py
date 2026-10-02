"""The order centre: the list, one order in detail, and the actions on it.

Every mutation goes through ``services.orders`` or ``services.payments``; nothing here sets a status column directly, so an order's
timeline can never disagree with its state. An id belonging to another merchant simply does not match and answers 404, which is
also right from a disclosure point of view (a 403 would confirm the order exists).
"""

from __future__ import annotations

from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import audit, q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import not_found, unprocessable
from sell4me_kit.money import Money
from sell4me_kit.services import orders as order_service
from sell4me_kit.services import payments
from sell4me_kit.services.orders import order_summary

PAGE_SIZE = 25

def _row(order: q.Row, currency: str, *, customer: str | None = None, items: int = 0) -> dict[str, Any]:
    return {"id": order.pk, "number": order.number, "email": order.email, "customer": customer, "status": order.status, "payment_status": order.payment_status,
            "fulfilment_status": order.fulfilment_status, "total": Money(order.total_minor, currency).as_prop(),
            "refunded": Money(order.refunded_minor or 0, currency).as_prop(), "items": items, "risk_score": order.risk_score, "source": order.source,
            "created_at": order.created_at, "paid_at": order.paid_at}


async def _find(c: Ctx, order_id: int) -> q.Row:
    order = await q.first(await c.db(), "orders", {"id": order_id, "store_id": c.store.pk})
    if order is None:
        raise not_found("That order")
    return order


@endpoint("orders.list", "GET", "/dash/{store}/orders", area="orders", permission="orders.read", summary="List orders: tabs (all, pending, paid, fulfilled, cancelled, refunded), search, paging",
          original="GET /orders")
async def orders_list(c: Ctx):
    await c.dashboard("orders.read")
    db, store = await c.db(), c.store
    tab, search = c.arg("tab", "all"), (c.arg("q") or "").strip()
    page, per_page = c.page_params(PAGE_SIZE)
    tab_sql = {"all": "1 = 1", "pending": "o.status = 'pending'", "paid": "o.payment_status = 'paid' AND o.fulfilment_status = 'unfulfilled'",
               "fulfilled": "o.fulfilment_status = 'fulfilled'", "cancelled": "o.status = 'cancelled'",
               "refunded": "o.payment_status IN ('refunded', 'partially_refunded')"}
    where, params = ["o.store_id = ?", "o.deleted_at IS NULL", tab_sql.get(tab, "1 = 1")], [store.pk]
    if search:
        cleaned = search.lstrip("#")
        clause = "(LOWER(o.email) LIKE ? OR LOWER(COALESCE(o.tracking_number, '')) LIKE ?"
        params += [f"%{search.lower()}%"] * 2
        if cleaned.isdigit():  # a leading # is stripped so #1042 and 1042 both work
            clause += " OR o.number = ?"
            params.append(int(cleaned))
        where.append(clause + ")")
    clause = " AND ".join(where)
    total = int(await db.scalar(f"SELECT COUNT(*) FROM orders o WHERE {clause}", params, default=0))
    rows = await db.fetch(
        f"SELECT o.*, c.first_name AS c_first, c.last_name AS c_last, c.email AS c_email, "
        f"(SELECT COALESCE(SUM(quantity), 0) FROM order_items i WHERE i.order_id = o.id AND i.deleted_at IS NULL) AS item_count "
        f"FROM orders o LEFT JOIN customers c ON c.id = o.customer_id WHERE {clause} ORDER BY o.id DESC LIMIT {per_page} OFFSET {(page - 1) * per_page}", params)
    data = []
    for r in rows:
        o = q.Row(r)
        customer = " ".join(p for p in (r["c_first"], r["c_last"]) if p) or r["c_email"]
        data.append(_row(o, store.currency, customer=customer, items=int(r["item_count"] or 0)))
    tabs = [{"key": key, "label": key.replace("_", " ").title(),
             "count": int(await db.scalar(f"SELECT COUNT(*) FROM orders o WHERE o.store_id = ? AND o.deleted_at IS NULL AND {sql}", [store.pk], default=0))}
            for key, sql in tab_sql.items()]
    return {"data": data, "tab": tab, "tabs": tabs, "search": search,
            "pagination": {"page": page, "per_page": per_page, "total": total, "pages": max(1, -(-total // per_page))}}


@endpoint("orders.show", "GET", "/dash/{store}/orders/{order_id}", area="orders", permission="orders.read",
          summary="One order: lines, addresses, payments, refunds, timeline, risk", original="GET /orders/{id}")
async def orders_show(c: Ctx):
    await c.dashboard("orders.read")
    db, store = await c.db(), c.store
    order = await _find(c, c.int_arg("order_id", required=True))
    customer = await q.get(db, "customers", order.customer_id)
    items = await q.find(db, "order_items", {"order_id": order.pk}, order="id")
    addresses = {a.kind: a for a in await q.find(db, "order_addresses", {"order_id": order.pk})}
    payments_ = await q.find(db, "payments", {"order_id": order.pk}, order="id DESC")
    refunds = await q.find(db, "refunds", {"order_id": order.pk}, order="id DESC")
    events = await db.fetch("SELECT e.*, p.full_name AS actor_name FROM order_events e LEFT JOIN profiles p ON p.user_id = e.actor_id "
                            "WHERE e.order_id = ? AND e.deleted_at IS NULL ORDER BY e.id DESC LIMIT 100", [order.pk])
    cur = store.currency
    return {
        "order": {**_row(order, cur, customer=customer.name if customer else None, items=sum(i.quantity for i in items)), "note": order.note, "tags": order.tags or [],
                  "shipping_method": order.shipping_method, "tracking_number": order.tracking_number, "tracking_url": order.tracking_url,
                  "discount_code": order.discount_code, "risk_flags": order.risk_flags or [], "client_ip": order.client_ip, "summary": order_summary(order)},
        "items": [{"id": i.pk, "title": i.title, "variant_title": i.variant_title, "sku": i.sku, "quantity": i.quantity, "quantity_refunded": i.quantity_refunded,
                   "refundable": i.refundable_quantity, "unit_price": Money(i.unit_price_minor, cur).as_prop(), "discount": Money(i.discount_minor or 0, cur).as_prop(),
                   "total": Money(i.total_minor, cur).as_prop()} for i in items],
        "addresses": {kind: {"name": a.name, "company": a.company, "line1": a.line1, "line2": a.line2, "city": a.city, "province": a.province,
                             "postal_code": a.postal_code, "country": a.country, "phone": a.phone} for kind, a in addresses.items()},
        "payments": [{"id": p.pk, "reference": p.reference, "provider": p.provider, "provider_reference": p.provider_reference, "status": p.status,
                      "amount": Money(p.amount_minor, p.currency).as_prop(),
                      "provider_fee": Money(p.provider_fee_minor, p.currency).as_prop() if p.provider_fee_minor is not None else None,
                      "platform_fee": Money(p.platform_fee_minor or 0, p.currency).as_prop(), "refunded": Money(p.refunded_minor or 0, p.currency).as_prop(),
                      "net": Money((p.amount_minor or 0) - (p.provider_fee_minor or 0) - (p.platform_fee_minor or 0) - (p.refunded_minor or 0), p.currency).as_prop(),
                      "card": f"{p.card_brand} ···· {p.card_last4}" if p.card_last4 else None, "method": p.method, "failure_message": p.failure_message,
                      "created_at": p.created_at} for p in payments_],
        "refunds": [{"id": r.pk, "amount": Money(r.amount_minor, r.currency).as_prop(), "reason": r.reason, "status": r.status, "created_at": r.created_at} for r in refunds],
        "timeline": [{"id": e["id"], "kind": e["kind"], "message": e["message"], "data": e["data"] or {}, "actor": e["actor_name"], "created_at": e["created_at"]} for e in events],
    }


@endpoint("orders.fulfil", "POST", "/dash/{store}/orders/{order_id}/fulfil", area="orders", permission="orders.fulfil", summary="Mark an order shipped, with tracking",
          fields=[{"name": "tracking_number", "type": "string"}, {"name": "tracking_url", "type": "string"}, {"name": "carrier", "type": "string"}], original="POST /orders/{id}/fulfil")
async def orders_fulfil(c: Ctx):
    await c.dashboard("orders.fulfil")
    order = await _find(c, c.int_arg("order_id", required=True))
    try:
        order = await order_service.fulfil(c, store=c.store, order=order, tracking_number=(c.input.get("tracking_number") or "").strip() or None,
                                           tracking_url=(c.input.get("tracking_url") or "").strip() or None, carrier=(c.input.get("carrier") or "").strip() or None, actor_id=c.user_id)
    except order_service.OrderError as error:
        raise unprocessable(str(error), "order_state") from error
    return {"id": order.pk, "status": order.status, "fulfilment_status": order.fulfilment_status, "message": f"Order #{order.number} marked shipped."}


@endpoint("orders.deliver", "POST", "/dash/{store}/orders/{order_id}/deliver", area="orders", permission="orders.fulfil", summary="Mark an order delivered", original="POST /orders/{id}/deliver")
async def orders_deliver(c: Ctx):
    await c.dashboard("orders.fulfil")
    order = await _find(c, c.int_arg("order_id", required=True))
    try:
        order = await order_service.mark_delivered(c, store=c.store, order=order, actor_id=c.user_id)
    except order_service.OrderError as error:
        raise unprocessable(str(error), "order_state") from error
    return {"id": order.pk, "status": order.status, "message": f"Order #{order.number} marked delivered."}


@endpoint("orders.cancel", "POST", "/dash/{store}/orders/{order_id}/cancel", area="orders", permission="orders.cancel",
          summary="Cancel an order and return its stock (does not refund: taking money back is a separate decision with its own permission)",
          fields=[{"name": "reason", "type": "string"}], original="POST /orders/{id}/cancel")
async def orders_cancel(c: Ctx):
    await c.dashboard("orders.cancel")
    order = await _find(c, c.int_arg("order_id", required=True))
    try:
        order = await order_service.cancel(c, store=c.store, order=order, reason=(c.input.get("reason") or "").strip() or None, actor_id=c.user_id)
    except order_service.OrderError as error:
        raise unprocessable(str(error), "order_state") from error
    return {"id": order.pk, "status": order.status, "message": f"Order #{order.number} cancelled and restocked."}


@endpoint("orders.refund", "POST", "/dash/{store}/orders/{order_id}/refund", area="orders", permission="refunds.create",
          summary="Refund some or all of an order; the amount is recomputed from the lines the merchant chose",
          fields=[{"name": "lines", "type": "json"}, {"name": "amount_minor", "type": "integer"}, {"name": "reason", "type": "string"}, {"name": "restock", "type": "boolean"},
                  {"name": "include_shipping", "type": "boolean"}], original="POST /orders/{id}/refund")
async def orders_refund(c: Ctx):
    """The amount is recomputed from the *lines* rather than taken from a submitted total, so a tampered field cannot refund more than the lines
    are worth. A refund naming no lines is an amount-only refund of the remaining balance."""
    await c.dashboard("refunds.create")
    db, store = await c.db(), c.store
    order = await _find(c, c.int_arg("order_id", required=True))
    payment = await q.first(db, "payments", {"order_id": order.pk, "status": q.in_(["succeeded", "partially_refunded"])}, order="id DESC")
    if payment is None:
        raise unprocessable("This order has no successful payment to refund.", "nothing_to_refund")
    lines = c.input.get("lines") if isinstance(c.input.get("lines"), list) else []
    items = {i.pk: i for i in await q.find(db, "order_items", {"order_id": order.pk})}
    resolved, amount = [], 0
    for line in lines:
        try:
            item_id, quantity = int(line.get("order_item_id")), int(line.get("quantity") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        item = items.get(item_id)
        if item is None or quantity <= 0:
            continue
        quantity = min(quantity, item.refundable_quantity)
        if quantity <= 0:
            continue
        # The line's own per-unit price after its share of the discount, not the list price, or a discounted order would refund more than it took.
        per_unit = item.total_minor // item.quantity if item.quantity else 0
        amount += per_unit * quantity
        resolved.append({"order_item_id": item_id, "quantity": quantity, "amount_minor": per_unit * quantity})
    if not resolved:
        try:
            amount = int(c.input.get("amount_minor") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            amount = payment.amount_minor - (payment.refunded_minor or 0)
    if c.input.get("include_shipping") and order.shipping_minor:
        amount += order.shipping_minor
    try:
        created = await payments.refund_payment(c, store=store, order=order, payment=payment, amount_minor=amount, reason=(c.input.get("reason") or "").strip() or None,
                                                line_items=resolved, restock=bool(c.input.get("restock", True)), actor_id=c.user_id)
    except payments.PaymentError as error:
        raise unprocessable(str(error), "refund_refused") from error
    return {"id": created.pk, "status": created.status, "amount": Money(created.amount_minor, created.currency).as_prop(),
            "message": f"Refunded {Money(created.amount_minor, created.currency).format()}."}


@endpoint("orders.note", "POST", "/dash/{store}/orders/{order_id}/note", area="orders", permission="orders.update", summary="Save an order's note (also a timeline entry)",
          fields=[{"name": "note", "type": "text"}], original="POST /orders/{id}/note")
async def orders_note(c: Ctx):
    await c.dashboard("orders.update")
    db = await c.db()
    order = await _find(c, c.int_arg("order_id", required=True))
    text = (c.input.get("note") or "").strip()
    await q.update(db, "orders", order.pk, {"note": text or None})
    if text:
        await order_service.record_event(db, order, "note.added", text[:500], actor_id=c.user_id)
    return {"id": order.pk, "note": text or None}
