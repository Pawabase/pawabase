"""What follows from each domain event.

The only module that knows the *consequences* of things happening. A service announces ``order.paid`` and stops; everything
below decides what that means for the audit trail, the merchant's bell, their webhook endpoints, the customer's receipt and the
analytics counters. Each listener does something trivial (an insert) or queues a job: an HTTP call to a merchant's endpoint, a
mail send or an image resample never runs inside the request.

Importing this module registers the listeners (``functions/_load.py`` does it once).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from . import q
from .events import DomainEvent, listener
from .money import Money
from . import audit
from .services import webhooks_out

log = logging.getLogger("sell4me.listeners")

# ── the audit trail ──────────────────────────────────────────────────────

AUDITED: dict[str, str] = {
    "product.created": "Created the product {title}",
    "product.updated": "Updated the product {title}",
    "product.archived": "Archived the product {title}",
    "order.paid": "Order #{number} was paid",
    "order.fulfilled": "Fulfilled order #{number}",
    "order.cancelled": "Cancelled order #{number}",
    "refund.created": "Refunded {amount} on order #{number}",
    "inventory.adjusted": "Adjusted stock for {sku} by {delta:+d}",
    "provider.connected": "Connected {provider}",
    "domain.verified": "Verified the domain {hostname}",
    "staff.invited": "Invited {email} as {role}",
    "staff.joined": "{email} joined the store",
    "store.launched": "Launched the store",
}


def _audit_fields(event: DomainEvent) -> dict[str, Any]:
    fields: dict[str, Any] = dict(event.payload)
    for key in ("product", "order", "variant", "domain", "invitation"):
        obj = event.get(key)
        if obj is None:
            continue
        for attribute in ("title", "number", "sku", "hostname", "email", "role"):
            value = obj.get(attribute) if isinstance(obj, dict) else getattr(obj, attribute, None)
            if value is not None:
                fields.setdefault(attribute, value)
    refund = event.get("refund")
    if refund is not None:
        fields["amount"] = Money(refund.amount_minor, refund.currency).format()
    return fields


def _resource_id(event: DomainEvent) -> Any:
    for key in ("order", "product", "variant", "refund", "domain", "invitation"):
        obj = event.get(key)
        if obj is not None:
            return obj.get("id")
    return None


@listener(*AUDITED)
async def write_audit_trail(c: Any, event: DomainEvent) -> None:
    template = AUDITED.get(event.name)
    if not template:
        return
    try:
        summary = template.format(**_audit_fields(event))
    except (KeyError, IndexError, ValueError):
        summary = event.name.replace(".", " ")
    await audit.record(await c.db(), store=event.store, actor=event.get("actor"), action=event.name, resource_type=event.name.split(".", 1)[0],
                       resource_id=event.get("resource_id") or _resource_id(event), summary=summary, changes=event.get("changes") or {},
                       ip_address=event.get("ip_address"))


# ── the merchant's bell ──────────────────────────────────────────────────

@listener("order.paid")
async def notify_new_order(c: Any, event: DomainEvent) -> None:
    order = event.order
    if order is None:
        return
    await q.insert(await c.db(), "notifications", {"store_id": event.store.pk, "kind": "order.created", "title": f"New order #{order.number}",
                   "body": f"{Money(order.total_minor, order.currency).format()} · {order.email}", "url": f"/orders/{order.pk}", "level": "success",
                   "required_permission": "orders.read"})


@listener("payment.failed")
async def notify_failed_payment(c: Any, event: DomainEvent) -> None:
    order, payment = event.order, event.payment
    if order is None:
        return
    await q.insert(await c.db(), "notifications", {"store_id": event.store.pk, "kind": "payment.failed", "title": f"Payment failed on order #{order.number}",
                   "body": (payment.failure_message if payment else None) or "The provider declined the payment.", "url": f"/orders/{order.pk}",
                   "level": "warning", "required_permission": "payments.read"})


@listener("inventory.low", "inventory.out")
async def notify_stock(c: Any, event: DomainEvent) -> None:
    variant, product = event.variant, event.product
    if variant is None or product is None:
        return
    out = event.name == "inventory.out"
    await q.insert(await c.db(), "notifications", {"store_id": event.store.pk, "kind": "inventory.low",
                   "title": f"{product.title} is {'out of stock' if out else 'running low'}",
                   "body": f"{variant.title} · no units remaining" if out else f"{variant.title} · {variant.available} left",
                   "url": f"/products/{product.pk}", "level": "critical" if out else "warning", "required_permission": "inventory.read"})


# ── the merchant's webhooks ──────────────────────────────────────────────

#: Domain events merchants can subscribe to, mapped to the public name their endpoint receives. Internal events are absent on
#: purpose: a merchant's integration should not break because we renamed something.
PUBLIC_EVENTS: dict[str, str] = {name: name for name in (
    "order.created", "order.paid", "order.fulfilled", "order.cancelled", "payment.succeeded", "payment.failed", "refund.created",
    "product.created", "product.updated", "inventory.low", "customer.created")}


def public_payload(event: DomainEvent) -> dict[str, Any]:
    """What a merchant's endpoint receives: listed by hand, so a column added later is not published by accident."""
    order, payment, refund = event.get("order"), event.get("payment"), event.get("refund")
    product, variant, customer = event.get("product"), event.get("variant"), event.get("customer")
    if order is not None and refund is None:
        return {"id": order.pk, "number": order.number, "email": order.email, "currency": order.currency, "subtotal_minor": order.subtotal_minor,
                "discount_minor": order.discount_minor, "shipping_minor": order.shipping_minor, "total_minor": order.total_minor, "status": order.status,
                "payment_status": order.payment_status, "fulfilment_status": order.fulfilment_status, "tracking_number": order.tracking_number}
    if refund is not None:
        return {"id": refund.pk, "order_id": refund.order_id, "amount_minor": refund.amount_minor, "currency": refund.currency, "reason": refund.reason}
    if payment is not None:
        return {"id": payment.pk, "reference": payment.reference, "order_id": payment.order_id, "provider": payment.provider,
                "amount_minor": payment.amount_minor, "currency": payment.currency, "status": payment.status}
    if product is not None:
        return {"id": product.pk, "title": product.title, "slug": product.slug, "status": product.status}
    if variant is not None:
        return {"id": variant.pk, "product_id": variant.product_id, "sku": variant.sku, "available": variant.available}
    if customer is not None:
        return {"id": customer.pk, "email": customer.email}
    return {}


@listener(*PUBLIC_EVENTS)
async def fan_out_to_webhooks(c: Any, event: DomainEvent) -> None:
    public = PUBLIC_EVENTS.get(event.name)
    if public and event.store is not None:
        queued = await webhooks_out.dispatch(c, event.store, public, public_payload(event))
        if queued:
            await c.dispatch("webhooks.sweep", {})


# ── email ────────────────────────────────────────────────────────────────

def _store_ctx(store: q.Row) -> dict[str, Any]:
    return {"name": store.name, "slug": store.slug, "support_email": store.support_email or store.email}


@listener("order.paid")
async def send_receipt(c: Any, event: DomainEvent) -> None:
    order, store = event.order, event.store
    if order is None or store is None:
        return
    db = await c.db()
    items = await q.find(db, "order_items", {"order_id": order.pk}, order="id")
    addresses = {a.kind: a for a in await q.find(db, "order_addresses", {"order_id": order.pk})}
    shipping = addresses.get("shipping")
    await c.dispatch("mail.send", {"template": "order_receipt", "to": order.email, "subject": f"Your {store.name} order #{order.number}", "context": {
        "store": _store_ctx(store),
        "order": {"number": order.number, "status_url": f"/orders/{order.number}/{order.cart_token}", "subtotal": Money(order.subtotal_minor, order.currency).format(),
                  "discount": Money(order.discount_minor, order.currency).format(), "shipping": Money(order.shipping_minor, order.currency).format(),
                  "total": Money(order.total_minor, order.currency).format(), "has_discount": (order.discount_minor or 0) > 0},
        "items": [{"title": i.title, "variant": i.variant_title, "quantity": i.quantity, "total": Money(i.total_minor, order.currency).format()} for i in items],
        "address": {"name": shipping.name, "line1": shipping.line1, "line2": shipping.line2, "city": shipping.city, "postal_code": shipping.postal_code,
                    "country": shipping.country} if shipping else None}})


@listener("order.fulfilled")
async def send_shipping_notice(c: Any, event: DomainEvent) -> None:
    order, store = event.order, event.store
    if order is None or store is None:
        return
    await c.dispatch("mail.send", {"template": "order_shipped", "to": order.email, "subject": f"Your {store.name} order is on its way", "context": {
        "store": _store_ctx(store), "order": {"number": order.number, "tracking_number": order.tracking_number, "tracking_url": order.tracking_url,
                                              "status_url": f"/orders/{order.number}/{order.cart_token}"}}})


@listener("refund.created")
async def send_refund_notice(c: Any, event: DomainEvent) -> None:
    refund, order, store = event.refund, event.order, event.store
    if refund is None or order is None or store is None:
        return
    await c.dispatch("mail.send", {"template": "order_refunded", "to": order.email, "subject": f"Refund for {store.name} order #{order.number}", "context": {
        "store": _store_ctx(store), "order": {"number": order.number},
        "refund": {"amount": Money(refund.amount_minor, refund.currency).format(), "reason": refund.reason}}})


@listener("staff.invited")
async def send_invitation(c: Any, event: DomainEvent) -> None:
    invitation, store = event.invitation, event.store
    if invitation is None or store is None:
        return
    await c.dispatch("mail.send", {"template": "staff_invitation", "to": invitation.email, "subject": f"You have been invited to {store.name}", "context": {
        "store": {"name": store.name}, "role": invitation.role, "accept_url": f"/invitations/{invitation.token}", "invited_by": event.get("invited_by_name")}})


@listener("cart.abandoned")
async def queue_cart_recovery(c: Any, event: DomainEvent) -> None:
    """Send the recovery email after a delay: immediately would reach someone who stepped away for two minutes."""
    record = event.record
    if record is not None and record.email:
        await c.dispatch("carts.recover_email", {"record_id": record.pk}, delay=3600)


@listener("store.created")
async def welcome_merchant(c: Any, event: DomainEvent) -> None:
    store, owner = event.store, event.get("owner")
    if store is None or not owner:
        return
    await c.dispatch("mail.send", {"template": "store_welcome", "to": owner.get("email"), "subject": f"{store.name} is ready", "context": {
        "store": {"name": store.name, "slug": store.slug}, "name": owner.get("full_name") or owner.get("email")}})


# ── media ────────────────────────────────────────────────────────────────

@listener("image.uploaded")
async def process_uploaded_image(c: Any, event: DomainEvent) -> None:
    """Generate the responsive and OpenGraph derivatives as a job: resampling a 4000px source takes seconds."""
    image = event.image
    if image is not None:
        await c.dispatch("image.process", {"image_id": image.pk})


# ── analytics, counters, payouts, campaigns ──────────────────────────────

@listener("order.paid", "refund.created")
async def refresh_today(c: Any, event: DomainEvent) -> None:
    if event.store is not None:
        await c.dispatch("analytics.aggregate_day", {"store_id": event.store.pk, "day": datetime.now(UTC).date().isoformat()})


@listener("order.paid")
async def bump_product_counters(c: Any, event: DomainEvent) -> None:
    """Keep the denormalised purchase counters in step (read on every product list; recomputing per row would be an aggregate per product)."""
    order = event.order
    if order is None:
        return
    db = await c.db()
    for item in await q.find(db, "order_items", {"order_id": order.pk}):
        if item.product_id:
            await q.increment(db, "products", item.product_id, purchase_count=item.quantity)


@listener("order.paid")
async def initiate_payout(c: Any, event: DomainEvent) -> None:
    """Pay the merchant their share now that the order is confirmed (a Paystack transfer is a network call the request should not wait on)."""
    if event.order is not None:
        await c.dispatch("payouts.initiate", {"order_id": event.order.pk})


@listener("order.paid")
async def refresh_attributed_campaign(c: Any, event: DomainEvent) -> None:
    order = event.order
    if order is None or not order.discount_id:
        return
    campaign = await q.first(await c.db(), "campaigns", {"store_id": order.store_id, "discount_id": order.discount_id})
    if campaign is not None:
        await c.dispatch("campaigns.refresh", {"campaign_id": campaign.pk})


@listener("payout.paid")
async def notify_payout(c: Any, event: DomainEvent) -> None:
    payout = event.payout
    if payout is None:
        return
    await q.insert(await c.db(), "notifications", {"store_id": event.store.pk, "kind": "payout.paid", "title": "A payout was sent",
                   "body": Money(payout.amount_minor, payout.currency).format(), "url": "/payments/payouts", "level": "success", "required_permission": "payouts.read"})
