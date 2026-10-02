"""What shipping costs, and where a store will ship to.

Checkout asks :func:`options_for` and is handed a list; it never reads zones or rates itself,
so a live carrier quote later is a change to this module and nothing else. Zones match
most-specific-first: ``GB`` beats ``*``. Bounds are inclusive at the bottom, exclusive at the top.
"""

from __future__ import annotations

from typing import Any

from .. import q
from ..money import Money


class ShippingUnavailable(Exception):
    """The store does not ship to this destination, or no rate covers the basket."""


async def options_for(
    c: Any, *, store: q.Row, country: str, subtotal_minor: int, weight_grams: int = 0, requires_shipping: bool = True
) -> list[dict[str, Any]]:
    if not requires_shipping:
        return [{"id": None, "name": "No shipping required", "description": "This order contains only digital items.",
                 "price": Money(0, store.currency).as_prop(), "price_minor": 0, "delivery_estimate": None}]
    db = await c.db()
    zone = await _match_zone(db, store, country)
    if zone is None:
        raise ShippingUnavailable(f"This store does not ship to {country}.")
    options: list[dict[str, Any]] = []
    for rate in await q.find(db, "shipping_rates", {"zone_id": zone.pk, "is_active": True}, order="position"):
        if not _rate_applies(rate, subtotal_minor=subtotal_minor, weight_grams=weight_grams):
            continue
        options.append({"id": rate.pk, "name": rate.name, "description": rate.description,
                        "price": Money(rate.price_minor, store.currency).as_prop(), "price_minor": rate.price_minor,
                        "delivery_estimate": rate.delivery_estimate})
    if not options:
        raise ShippingUnavailable("No shipping rate covers this order. The merchant may need to add one.")
    return options


async def rate_for(c: Any, *, store: q.Row, rate_id: int | None, country: str) -> q.Row | None:
    """A rate the customer chose, re-checked against the destination (the id came from the browser)."""
    if rate_id is None:
        return None
    db = await c.db()
    rate = await q.first(db, "shipping_rates", {"id": rate_id, "store_id": store.pk})
    if rate is None:
        return None
    zone = await _match_zone(db, store, country)
    if zone is None or zone.pk != rate.zone_id:
        return None
    return rate


async def _match_zone(db: Any, store: q.Row, country: str) -> q.Row | None:
    code = (country or "").upper()
    wildcard: q.Row | None = None
    for zone in await q.find(db, "shipping_zones", {"store_id": store.pk}, order="position"):
        countries = {str(x).upper() for x in (zone.countries or [])}
        if code in countries:
            return zone
        if "*" in countries and wildcard is None:
            wildcard = zone
    return wildcard


def _rate_applies(rate: q.Row, *, subtotal_minor: int, weight_grams: int) -> bool:
    if rate.kind == "weight":
        if rate.min_weight_grams is not None and weight_grams < rate.min_weight_grams:
            return False
        if rate.max_weight_grams is not None and weight_grams >= rate.max_weight_grams:
            return False
        return True
    if rate.kind == "price":
        if rate.min_subtotal_minor is not None and subtotal_minor < rate.min_subtotal_minor:
            return False
        if rate.max_subtotal_minor is not None and subtotal_minor >= rate.max_subtotal_minor:
            return False
        return True
    return True


async def seed_default_zone(db: Any, store: q.Row) -> q.Row:
    """Give a new store somewhere to ship to, so a first checkout works before the merchant has thought about shipping."""
    zone, created = await q.get_or_create(db, "shipping_zones", {"store_id": store.pk, "name": "Everywhere"},
                                          {"countries": ["*"], "position": 100})
    if created:
        await q.insert(db, "shipping_rates", {"zone_id": zone.pk, "store_id": store.pk, "name": "Standard shipping",
                       "description": "Delivered by post.", "kind": "flat", "price_minor": 500,
                       "delivery_estimate": "3–5 business days", "position": 0})
        await q.insert(db, "shipping_rates", {"zone_id": zone.pk, "store_id": store.pk, "name": "Free shipping",
                       "description": "On orders over 50.", "kind": "price", "price_minor": 0, "min_subtotal_minor": 5000,
                       "delivery_estimate": "5–7 business days", "position": 1})
    return zone
