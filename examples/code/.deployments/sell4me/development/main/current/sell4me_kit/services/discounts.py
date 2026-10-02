"""Deciding whether a discount applies, and by how much.

One function, :func:`evaluate`, answers both and nothing else is allowed to: a second
implementation would eventually disagree about expiry or a usage limit and charge an amount
the merchant did not authorise. It returns an :class:`Evaluation` rather than raising because
"this code has expired" is something the storefront must *show*; every refusal carries a
reason written for a shopper.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .. import q
from ..money import Money, percent_of
from . import segments


@dataclass(slots=True)
class Evaluation:
    ok: bool
    amount_minor: int = 0
    free_shipping: bool = False
    discount: q.Row | None = None
    reason: str | None = None

    def as_prop(self, currency: str) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.discount.code if self.discount else None,
            "title": self.discount.title if self.discount else None,
            "amount": Money(self.amount_minor, currency).as_prop(),
            "free_shipping": self.free_shipping,
            "reason": self.reason,
        }


async def evaluate(
    c: Any,
    *,
    store: q.Row,
    code: str,
    lines: list[dict[str, Any]],
    customer: q.Row | None = None,
    shipping_minor: int = 0,
) -> Evaluation:
    """What *code* takes off this basket. ``lines`` are ``{variant_id, product_id, quantity, line_total_minor}``."""
    db = await c.db()
    discount = await q.first(db, "discounts", {"store_id": store.pk, "code": code.strip().upper()})
    if discount is None:
        return Evaluation(ok=False, reason="That code isn't recognised.")

    now = datetime.now(UTC)
    if not discount.is_active:
        return Evaluation(ok=False, reason="That code is no longer available.")
    starts, ends = q.parse_dt(discount.starts_at), q.parse_dt(discount.ends_at)
    if starts and starts > now:
        return Evaluation(ok=False, reason="That code isn't active yet.")
    if ends and ends < now:
        return Evaluation(ok=False, reason="That code has expired.")
    if discount.is_exhausted:
        return Evaluation(ok=False, reason="That code has been fully redeemed.")

    if discount.per_customer_limit is not None:
        if customer is None:
            return Evaluation(ok=False, reason="Sign in to use that code — it's limited per customer.")
        used = await q.count(db, "discount_usages", {"discount_id": discount.pk, "customer_id": customer.pk})
        if used >= discount.per_customer_limit:
            return Evaluation(ok=False, reason="You've already used that code.")

    if discount.segment_id is not None:
        if customer is None or not await segments.contains(db, discount.segment_id, customer):
            return Evaluation(ok=False, reason="That code isn't available on this account.")

    eligible = await _eligible_total(db, discount, lines)
    subtotal = sum(int(line.get("line_total_minor") or 0) for line in lines)

    if subtotal < (discount.minimum_order_minor or 0):
        shortfall = discount.minimum_order_minor - subtotal
        return Evaluation(ok=False, reason=f"Spend {Money(shortfall, store.currency).format()} more to use that code.")

    if discount.kind == "free_shipping":
        return Evaluation(ok=True, amount_minor=0, free_shipping=True, discount=discount)
    if eligible <= 0:
        return Evaluation(ok=False, reason="That code doesn't apply to anything in your basket.")

    amount = percent_of(eligible, discount.value) if discount.kind == "percentage" else int(discount.value)
    if discount.maximum_discount_minor is not None:
        amount = min(amount, discount.maximum_discount_minor)
    # Never more than the goods it applies to: a $50 fixed discount on a $30 basket must not go negative.
    amount = min(amount, eligible)
    return Evaluation(ok=True, amount_minor=amount, discount=discount)


async def _eligible_total(db: Any, discount: q.Row, lines: list[dict[str, Any]]) -> int:
    if discount.scope == "order":
        return sum(int(line.get("line_total_minor") or 0) for line in lines)
    ids = {int(value) for value in (discount.scope_ids or [])}
    if not ids:
        return 0
    if discount.scope == "products":
        return sum(int(l.get("line_total_minor") or 0) for l in lines if int(l.get("product_id") or 0) in ids)
    product_ids: set[int] = set()
    for entry in await q.find(db, "collection_products", {"collection_id": q.in_(list(ids))}):
        product_ids.add(entry.product_id)
    return sum(int(l.get("line_total_minor") or 0) for l in lines if int(l.get("product_id") or 0) in product_ids)


async def apply_automatic(
    c: Any, *, store: q.Row, lines: list[dict[str, Any]], customer: q.Row | None = None, shipping_minor: int = 0
) -> Evaluation | None:
    """The best automatic discount for this basket, if any. One applies: stacking could give away more than the goods are worth."""
    db = await c.db()
    now = datetime.now(UTC)
    best: Evaluation | None = None
    for candidate in await q.find(db, "discounts", {"store_id": store.pk, "is_automatic": True, "is_active": True}):
        starts, ends = q.parse_dt(candidate.starts_at), q.parse_dt(candidate.ends_at)
        if (starts and starts > now) or (ends and ends < now):
            continue
        result = await evaluate(c, store=store, code=candidate.code, lines=lines, customer=customer, shipping_minor=shipping_minor)
        if result.ok and (best is None or result.amount_minor > best.amount_minor):
            best = result
    return best
