"""Checkout: the one path from a basket to a charge.

    1. validate the basket    2. resolve the customer    3. reserve stock    4. build the order
    5. assess risk            6. start the payment

Stock is reserved *before* the order exists, because reserving is the step that can fail for a reason
the customer must hear about. If anything after step 3 fails, every reservation is released.
Confirmation is separate: :func:`complete` runs when the shopper returns from the provider and
*verifies* with the provider rather than trusting the redirect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .. import q
from ..events import emit
from ..payments import ProviderError
from . import carts as cart_service
from . import inventory, orders, payments, risk, shipping
from .inventory import InsufficientStock


class CheckoutError(Exception):
    """Checkout could not proceed, with a reason a shopper can read."""


@dataclass(slots=True)
class CheckoutResult:
    order: q.Row
    redirect_url: str | None
    reference: str


async def begin(c: Any, *, store: q.Row, cart: q.Row, email: str, shipping_address: dict[str, Any], billing_address: dict[str, Any] | None,
                shipping_rate_id: int | None, provider_key: str, return_url: str, cancel_url: str, accepts_marketing: bool = False,
                client_ip: str | None = None, user_agent: str | None = None) -> CheckoutResult:
    """Take a basket all the way to a payment URL. Every :class:`CheckoutError` message is written to be shown verbatim."""
    db = await c.db()
    items = await db.fetch(
        "SELECT ci.id, ci.quantity, v.id AS variant_id, v.weight_grams, p.requires_shipping FROM cart_items ci "
        "JOIN product_variants v ON v.id = ci.variant_id JOIN products p ON p.id = v.product_id "
        "WHERE ci.cart_id = ? AND ci.deleted_at IS NULL", [cart.pk])
    if not items:
        raise CheckoutError("Your basket is empty.")
    country = (shipping_address.get("country") or store.country or "US").upper()
    requires_shipping = any(i["requires_shipping"] for i in items)
    weight = sum((i["weight_grams"] or 0) * i["quantity"] for i in items)

    customer = await resolve_customer(c, store=store, email=email, address=shipping_address, accepts_marketing=accepts_marketing)
    summary = await cart_service.summarise(c, store=store, cart=cart, customer=customer)
    subtotal_minor = summary["amounts"]["subtotal_minor"]

    shipping_minor, shipping_method = 0, None
    if requires_shipping:
        try:
            available = await shipping.options_for(c, store=store, country=country, subtotal_minor=subtotal_minor, weight_grams=weight)
        except shipping.ShippingUnavailable as error:
            raise CheckoutError(str(error)) from error
        rate = await shipping.rate_for(c, store=store, rate_id=shipping_rate_id, country=country)
        if rate is None:  # nothing chosen, or a rate that does not apply: the cheapest valid option, never a form the customer cannot submit
            cheapest = min(available, key=lambda o: o["price_minor"])
            shipping_minor, shipping_method = cheapest["price_minor"], cheapest["name"]
        else:
            shipping_minor, shipping_method = rate.price_minor, rate.name
    # Re-summarised with shipping, because a free-shipping discount can only be priced once shipping is known.
    summary = await cart_service.summarise(c, store=store, cart=cart, customer=customer, shipping_minor=shipping_minor)
    amounts = summary["amounts"]

    reserved: list[tuple[int, int]] = []
    try:
        for item in items:
            await inventory.reserve(c, store=store, variant_id=item["variant_id"], quantity=item["quantity"])
            reserved.append((item["variant_id"], item["quantity"]))
    except InsufficientStock as error:
        for variant_id, quantity in reserved:
            await inventory.release_reservation(c, variant_id=variant_id, quantity=quantity)
        raise CheckoutError(str(error)) from error

    try:
        order = await orders.build_order_from_cart(
            c, store=store, cart=cart, email=email, shipping_address=_clean_address(shipping_address),
            billing_address=_clean_address(billing_address or shipping_address), shipping_minor=amounts["shipping_minor"],
            shipping_method=shipping_method, discount_minor=amounts["discount_minor"], customer=customer, client_ip=client_ip)
        now = datetime.now(UTC)
        await q.update(db, "carts", cart.pk, {"status": "checkout", "email": email, "customer_id": customer.pk, "reserved_at": now, "last_activity_at": now})
        await emit(c, "checkout.started", store=store, order=order, cart=cart)
        await risk.assess(c, store=store, order=order)
        result = await payments.start_payment(c, store=store, order=order, provider_key=provider_key, return_url=return_url,
                                              cancel_url=cancel_url, client_ip=client_ip, user_agent=user_agent)
    except (payments.PaymentError, ProviderError, orders.OrderError) as error:
        for variant_id, quantity in reserved:
            await inventory.release_reservation(c, variant_id=variant_id, quantity=quantity)
        raise CheckoutError(str(error)) from error
    except Exception:
        for variant_id, quantity in reserved:
            await inventory.release_reservation(c, variant_id=variant_id, quantity=quantity)
        raise
    return CheckoutResult(order=order, redirect_url=result.redirect_url, reference=result.reference)


async def complete(c: Any, *, store: q.Row, order: q.Row, provider_key: str, reference: str | None = None) -> q.Row:
    """Confirm an order when the shopper returns from the provider: ask the provider what happened. Idempotent."""
    db = await c.db()
    if order.is_paid:
        return order
    provider, credentials, _ = await payments.credentials_for(c, store, provider_key)
    payment = await q.first(db, "payments", {"order_id": order.pk}, order="id DESC")
    lookup = reference or (payment.reference if payment else None)
    if lookup is None:
        return order
    try:
        result = await provider.verify(credentials=credentials, reference=lookup)
    except ProviderError:
        return order  # unreachable or not yet known: the order stays pending; the webhook or reconciliation resolves it
    await payments.settle(c, store=store, provider_key=provider_key, result=result)
    return await q.get(db, "orders", order.pk)


async def resolve_customer(c: Any, *, store: q.Row, email: str, address: dict[str, Any] | None = None, accepts_marketing: bool = False) -> q.Row:
    """The customer record for this email, upserted on ``(store, email)``. An existing record only ever has blanks filled."""
    db = await c.db()
    email = email.strip().lower()
    customer = await q.first(db, "customers", {"store_id": store.pk, "email": email})
    if customer is None:
        customer = await q.insert(db, "customers", {
            "store_id": store.pk, "email": email, "first_name": (address or {}).get("first_name"), "last_name": (address or {}).get("last_name"),
            "phone": (address or {}).get("phone"), "accepts_marketing": accepts_marketing, "tags": [], "orders_count": 0,
            "total_spent_minor": 0, "risk_score": 0})
        await emit(c, "customer.created", store=store, customer=customer)
        return customer
    changed: dict[str, Any] = {}
    for field in ("first_name", "last_name", "phone"):
        value = (address or {}).get(field)
        if value and not customer.get(field):
            changed[field] = value
    if accepts_marketing and not customer.accepts_marketing:
        changed["accepts_marketing"] = True
    if changed:
        await q.update(db, "customers", customer.pk, changed)
        customer = await q.get(db, "customers", customer.pk)
    return customer


_ADDRESS_FIELDS = ("first_name", "last_name", "company", "line1", "line2", "city", "province", "postal_code", "country", "phone")


def _clean_address(address: dict[str, Any] | None) -> dict[str, Any] | None:
    """Only the columns an order address has: the dict came from a request body."""
    if not address:
        return None
    cleaned = {k: (str(v).strip() or None) if v is not None else None for k, v in address.items() if k in _ADDRESS_FIELDS}
    if not cleaned.get("line1") or not cleaned.get("city"):
        raise CheckoutError("A shipping address needs at least a street and a city.")
    cleaned["country"] = (cleaned.get("country") or "US").upper()[:2]
    return cleaned
