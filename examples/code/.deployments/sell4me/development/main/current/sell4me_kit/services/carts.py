"""The basket, and what it is worth.

A cart is keyed by an opaque token the storefront holds, so a shopper who is not signed in keeps
their basket across page loads. :func:`summarise` is the single place a basket becomes money: the
cart page, the checkout page and the order builder all call it, so the total a customer is shown
and the total they are charged are computed by the same code.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from .. import q
from ..money import Money
from . import discounts as discount_service
from .inventory import InsufficientStock

ABANDON_AFTER_MINUTES = 60


async def find_or_create(c: Any, store: q.Row, token: str | None) -> q.Row:
    """The cart for this token, or a new one. A token naming a converted cart starts a fresh one."""
    db = await c.db()
    if token:
        cart = await q.first(db, "carts", {"token": token, "store_id": store.pk})
        if cart is not None and cart.status in ("open", "checkout"):
            return cart
    return await q.insert(db, "carts", {"store_id": store.pk, "token": secrets.token_urlsafe(24), "currency": store.currency,
                                        "status": "open", "last_activity_at": datetime.now(UTC)})


async def add_item(c: Any, *, cart: q.Row, variant_id: int, quantity: int = 1) -> q.Row:
    """Put something in the basket, or add to what is there. Availability is a courtesy; ``inventory.reserve`` is the guarantee."""
    if quantity < 1:
        raise ValueError("Quantity must be at least 1.")
    async with c.tx() as db:
        variant = await q.first(db, "product_variants", {"id": variant_id, "store_id": cart.store_id})
        if variant is None:
            raise ValueError("That item is not available in this store.")
        item = await q.first(db, "cart_items", {"cart_id": cart.pk, "variant_id": variant.pk})
        wanted = (item.quantity if item else 0) + quantity
        if variant.available < wanted:
            raise InsufficientStock(variant, wanted, variant.available)
        if item is None:
            item = await q.insert(db, "cart_items", {"cart_id": cart.pk, "variant_id": variant.pk, "quantity": quantity, "unit_price_minor": variant.price_minor})
        else:
            # Re-read the price on every touch: a basket left open a week must not hold last week's price.
            await q.update(db, "cart_items", item.pk, {"quantity": wanted, "unit_price_minor": variant.price_minor})
            item = await q.get(db, "cart_items", item.pk)
        await _touch(db, cart)
    return item


async def set_quantity(c: Any, *, cart: q.Row, item_id: int, quantity: int) -> None:
    db = await c.db()
    item = await q.first(db, "cart_items", {"id": item_id, "cart_id": cart.pk})
    if item is None:
        return
    if quantity <= 0:
        await q.soft_delete(db, "cart_items", item.pk)
        await _touch(db, cart)
        return
    variant = await q.get(db, "product_variants", item.variant_id)
    if variant.available < quantity:
        raise InsufficientStock(variant, quantity, variant.available)
    await q.update(db, "cart_items", item.pk, {"quantity": quantity, "unit_price_minor": variant.price_minor})
    await _touch(db, cart)


async def remove_item(c: Any, *, cart: q.Row, item_id: int) -> None:
    db = await c.db()
    item = await q.first(db, "cart_items", {"id": item_id, "cart_id": cart.pk})
    if item:
        await q.soft_delete(db, "cart_items", item.pk)
    await _touch(db, cart)


async def apply_discount(c: Any, *, store: q.Row, cart: q.Row, code: str, customer: q.Row | None = None) -> discount_service.Evaluation:
    """Attach a discount code, if it applies. Stored as a reference, so it stops applying when the basket drops below the minimum."""
    lines = await _lines(c, cart)
    result = await discount_service.evaluate(c, store=store, code=code, lines=lines, customer=customer)
    if result.ok and result.discount is not None:
        db = await c.db()
        await q.update(db, "carts", cart.pk, {"discount_id": result.discount.pk, "discount_code": result.discount.code})
        cart["discount_id"], cart["discount_code"] = result.discount.pk, result.discount.code
    return result


async def clear_discount(c: Any, cart: q.Row) -> None:
    db = await c.db()
    await q.update(db, "carts", cart.pk, {"discount_id": None, "discount_code": None})
    cart["discount_id"], cart["discount_code"] = None, None


async def summarise(c: Any, *, store: q.Row, cart: q.Row, customer: q.Row | None = None, shipping_minor: int = 0) -> dict[str, Any]:
    """The basket as money and as props. The discount is re-evaluated here, never read from a stored amount."""
    db = await c.db()
    rows = await db.fetch(
        "SELECT ci.id AS item_id, ci.quantity, v.id AS variant_id, v.title AS variant_title, v.sku, v.price_minor, v.is_default, "
        "v.stock, v.reserved, p.id AS product_id, p.slug AS product_slug, p.title AS product_title "
        "FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id JOIN products p ON p.id = v.product_id "
        "WHERE ci.cart_id = ? AND ci.deleted_at IS NULL ORDER BY ci.id", [cart.pk])
    lines: list[dict[str, Any]] = []
    subtotal = 0
    for r in rows:
        line_total = r["price_minor"] * r["quantity"]
        subtotal += line_total
        image = await q.first(db, "product_images", {"product_id": r["product_id"]}, order="position")
        lines.append({
            "id": r["item_id"], "variant_id": r["variant_id"], "product_id": r["product_id"], "product_slug": r["product_slug"],
            "title": r["product_title"], "variant_title": r["variant_title"] if not r["is_default"] else None, "sku": r["sku"],
            "image_url": image.url if image else None, "quantity": r["quantity"], "available": (r["stock"] or 0) - (r["reserved"] or 0),
            "unit_price": Money(r["price_minor"], cart.currency).as_prop(), "line_total": Money(line_total, cart.currency).as_prop(),
        })
    discount_minor, free_shipping, discount_state = 0, False, None
    if cart.discount_code:
        evaluation = await discount_service.evaluate(
            c, store=store, code=cart.discount_code,
            lines=[{"variant_id": l["variant_id"], "product_id": l["product_id"], "quantity": l["quantity"],
                    "line_total_minor": l["line_total"]["minor"]} for l in lines],
            customer=customer, shipping_minor=shipping_minor)
        discount_state = evaluation.as_prop(cart.currency)
        if evaluation.ok:
            discount_minor, free_shipping = evaluation.amount_minor, evaluation.free_shipping
    if free_shipping:
        shipping_minor = 0
    total = subtotal + shipping_minor - discount_minor
    return {
        "token": cart.token, "items": lines, "item_count": sum(l["quantity"] for l in lines), "currency": cart.currency,
        "subtotal": Money(subtotal, cart.currency).as_prop(), "discount": Money(discount_minor, cart.currency).as_prop(),
        "shipping": Money(shipping_minor, cart.currency).as_prop(), "total": Money(max(total, 0), cart.currency).as_prop(),
        "discount_code": cart.discount_code, "discount_state": discount_state, "free_shipping": free_shipping,
        "amounts": {"subtotal_minor": subtotal, "discount_minor": discount_minor, "shipping_minor": shipping_minor, "total_minor": max(total, 0)},
    }


async def _lines(c: Any, cart: q.Row) -> list[dict[str, Any]]:
    db = await c.db()
    rows = await db.fetch(
        "SELECT ci.quantity, v.id AS variant_id, v.product_id, v.price_minor FROM cart_items ci "
        "JOIN product_variants v ON v.id = ci.variant_id WHERE ci.cart_id = ? AND ci.deleted_at IS NULL", [cart.pk])
    return [{"variant_id": r["variant_id"], "product_id": r["product_id"], "quantity": r["quantity"], "line_total_minor": r["price_minor"] * r["quantity"]} for r in rows]


async def _touch(db: Any, cart: q.Row) -> None:
    """Record activity for the abandonment sweep (distinct from ``updated_at``, which a background write would reset)."""
    await q.update(db, "carts", cart.pk, {"last_activity_at": datetime.now(UTC)})
