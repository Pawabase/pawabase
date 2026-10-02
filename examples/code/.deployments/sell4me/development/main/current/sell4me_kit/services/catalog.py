"""Products, variants and collections.

The non-obvious part is variant generation. A product with options ``Size × Colour`` has a variant for
every combination, and the merchant can add a colour later without losing the price and stock set on the
existing variants. :func:`sync_variants` computes the cross product, keeps every variant whose combination
is still valid, creates the new ones, and *archives* (zero stock, parked last) those whose combination no
longer exists, because order lines point at them and deleting would blank historical invoices.
"""

from __future__ import annotations

import re
from itertools import product as cross_product
from typing import Any

from .. import q
from ..money import Money

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """A URL-safe slug. Empty input becomes ``untitled``, never an empty path."""
    return _SLUG_STRIP.sub("-", (value or "").lower()).strip("-")[:200] or "untitled"


async def unique_slug(db: Any, table: str, store: q.Row, value: str, *, exclude_id: int | None = None) -> str:
    """A slug not already taken in this store: ``classic-tee``, ``classic-tee-2``, ... (guessable and readable)."""
    base = slugify(value)
    candidate, counter = base, 2
    while True:
        where: dict[str, Any] = {"store_id": store.pk, "slug": candidate}
        if exclude_id is not None:
            where["id"] = q.ne(exclude_id)
        if not await q.exists(db, table, where):
            return candidate
        candidate = f"{base}-{counter}"
        counter += 1


_INHERITED = ("price_minor", "compare_at_minor", "cost_minor", "weight_grams", "track_inventory")
_VARIANT_FIELDS = ("price_minor", "compare_at_minor", "cost_minor", "sku", "barcode", "weight_grams")


def _closest_ancestor(key: frozenset[str], existing: dict[frozenset[str], Any]) -> dict[str, Any]:
    """What a new combination starts from: the existing variant whose values are the largest subset of *key*.
    Price, cost, compare-at and weight carry across (facts about the size); stock and SKU do not (a red small and a
    blue small are different objects on a shelf, and copying stock would invent inventory)."""
    best, best_size = None, 0
    for combination, variant in existing.items():
        if combination and combination < key and len(combination) > best_size:
            best, best_size = variant, len(combination)
    return {} if best is None else {f: best.get(f) for f in _INHERITED}


async def sync_variants(c: Any, *, store: q.Row, product: q.Row, options: list[dict[str, Any]],
                        variants: list[dict[str, Any]] | None = None) -> list[q.Row]:
    """Rebuild a product's options and reconcile its variants. Existing variants are matched by the combination of
    option *values*, not by id, which is what lets a colour be added without disturbing the price on ``Large / Navy``."""
    async with c.tx() as db:
        existing = await q.find(db, "product_variants", {"product_id": product.pk})
        by_combination: dict[frozenset[str], q.Row] = {}
        for variant in existing:
            links = await db.fetch(
                "SELECT ov.value FROM variant_option_values vo JOIN product_option_values ov ON ov.id = vo.option_value_id "
                "WHERE vo.variant_id = ? AND vo.deleted_at IS NULL", [variant.pk])
            by_combination[frozenset(r["value"] for r in links)] = variant
        await db.execute("DELETE FROM product_option_values WHERE option_id IN (SELECT id FROM product_options WHERE product_id = ?)", [product.pk])
        await db.execute("DELETE FROM product_options WHERE product_id = ?", [product.pk])
        if not options:
            return [await _ensure_default_variant(db, store, product, by_combination, variants)]
        value_rows: list[list[q.Row]] = []
        for position, spec in enumerate(options):
            option = await q.insert(db, "product_options", {"product_id": product.pk, "name": spec["name"], "position": position})
            created = [await q.insert(db, "product_option_values", {"option_id": option.pk, "value": str(v), "position": i})
                       for i, v in enumerate(spec.get("values") or []) if str(v).strip()]
            if created:
                value_rows.append(created)
        if not value_rows:
            return [await _ensure_default_variant(db, store, product, by_combination, variants)]
        # A spec naming no options is the *base* for every combination ("costs 19.99, comes in three sizes"), not a combination
        # of its own. Treating it as one made every generated variant price at zero.
        base: dict[str, Any] = {}
        overrides: dict[frozenset[str], dict[str, Any]] = {}
        for spec in variants or []:
            names = frozenset(str(v) for v in (spec.get("options") or []))
            if names:
                overrides[names] = spec
            elif not base:
                base = spec
        wanted: list[q.Row] = []
        seen: set[frozenset[str]] = set()
        for position, combination in enumerate(cross_product(*value_rows)):
            values = [v.value for v in combination]
            key = frozenset(values)
            seen.add(key)
            spec = {**base, **overrides.get(key, {})}
            title = " / ".join(values)
            variant = by_combination.get(key)
            if variant is None:
                spec = {**_closest_ancestor(key, by_combination), **spec}
                variant = await q.insert(db, "product_variants", {
                    "product_id": product.pk, "store_id": store.pk, "title": title, "position": position,
                    "price_minor": int(spec.get("price_minor") or 0), "compare_at_minor": spec.get("compare_at_minor"),
                    "cost_minor": spec.get("cost_minor"), "sku": spec.get("sku"), "barcode": spec.get("barcode"),
                    "stock": int(spec.get("stock") or 0), "reserved": 0, "weight_grams": int(spec.get("weight_grams") or 0),
                    "track_inventory": bool(spec.get("track_inventory", True)), "allow_backorder": False, "low_stock_threshold": 5,
                    "is_default": False})
            else:
                changes: dict[str, Any] = {"title": title, "position": position, "is_default": False}
                for field in _VARIANT_FIELDS:  # only what the merchant submitted: a variant's stock survives an option being added
                    if field in spec:
                        changes[field] = spec[field]
                if "stock" in spec:
                    changes["stock"] = int(spec["stock"] or 0)
                await q.update(db, "product_variants", variant.pk, changes)
                variant = await q.get(db, "product_variants", variant.pk)
            await db.execute("DELETE FROM variant_option_values WHERE variant_id = ?", [variant.pk])
            for value in combination:
                await q.insert(db, "variant_option_values", {"variant_id": variant.pk, "option_value_id": value.pk})
            wanted.append(variant)
        for combination, variant in by_combination.items():
            if combination not in seen:
                await q.update(db, "product_variants", variant.pk, {"stock": 0, "track_inventory": True, "position": 9999})
        return wanted


async def _ensure_default_variant(db: Any, store: q.Row, product: q.Row, existing: dict[frozenset[str], q.Row],
                                  variants: list[dict[str, Any]] | None) -> q.Row:
    """The single variant a product with no options has, so a cart line, an order line and a stock count point at the same kind of row."""
    spec = (variants or [{}])[0] if variants else {}
    variant = existing.get(frozenset()) or await q.first(db, "product_variants", {"product_id": product.pk}, order="id")
    if variant is None:
        return await q.insert(db, "product_variants", {
            "product_id": product.pk, "store_id": store.pk, "title": "Default", "is_default": True, "price_minor": int(spec.get("price_minor") or 0),
            "compare_at_minor": spec.get("compare_at_minor"), "cost_minor": spec.get("cost_minor"), "sku": spec.get("sku"),
            "barcode": spec.get("barcode"), "stock": int(spec.get("stock") or 0), "reserved": 0, "weight_grams": int(spec.get("weight_grams") or 0),
            "track_inventory": bool(spec.get("track_inventory", True)), "allow_backorder": False, "low_stock_threshold": 5, "position": 0})
    changes: dict[str, Any] = {"title": "Default", "is_default": True, "position": 0}
    for field in _VARIANT_FIELDS:
        if field in spec:
            changes[field] = spec[field]
    if "stock" in spec:
        changes["stock"] = int(spec["stock"] or 0)
    if "track_inventory" in spec:
        changes["track_inventory"] = bool(spec["track_inventory"])
    await q.update(db, "product_variants", variant.pk, changes)
    await db.execute("DELETE FROM variant_option_values WHERE variant_id = ?", [variant.pk])
    return await q.get(db, "product_variants", variant.pk)


async def collection_products(db: Any, collection: q.Row, *, published_only: bool = True) -> list[q.Row]:
    """The products in a collection, manual or automatic. An automatic collection's rules are resolved into a query, so a product
    created today joins a collection defined last month."""
    where: dict[str, Any] = {"store_id": collection.store_id}
    if published_only:
        where["status"] = "active"
    if collection.kind == "manual":
        entries = await q.find(db, "collection_products", {"collection_id": collection.pk}, order="position")
        ids = [e.product_id for e in entries]
        if not ids:
            return []
        products = await q.find(db, "products", {**where, "id": q.in_(ids)})
        order = {pid: i for i, pid in enumerate(ids)}
        return sorted(products, key=lambda p: order.get(p.pk, 9999))
    clauses, params = ["store_id = ?", "deleted_at IS NULL"], [collection.store_id]
    if published_only:
        clauses.append("status = 'active'")
    for rule in collection.rules or []:
        field, operator, value = rule.get("field"), rule.get("operator"), rule.get("value")
        if field == "tag" and operator == "contains":
            clauses.append("CAST(tags AS TEXT) LIKE ?")
            params.append(f'%"{value}"%')
        elif field == "vendor":
            clauses.append("vendor = ?")
            params.append(value)
        elif field == "product_type":
            clauses.append("product_type = ?")
            params.append(value)
        elif field == "title" and operator == "contains":
            clauses.append("LOWER(title) LIKE ?")
            params.append(f"%{str(value).lower()}%")
    return [q.Row(r) for r in await db.fetch(f"SELECT * FROM products WHERE {' AND '.join(clauses)} ORDER BY id DESC", params)]


def variant_prop(variant: q.Row, currency: str) -> dict[str, Any]:
    return {
        "id": variant.pk, "title": variant.title, "sku": variant.sku, "barcode": variant.barcode,
        "price": Money(variant.price_minor, currency).as_prop(), "price_minor": variant.price_minor,
        "compare_at": Money(variant.compare_at_minor, currency).as_prop() if variant.compare_at_minor else None,
        "cost": Money(variant.cost_minor, currency).as_prop() if variant.cost_minor else None,
        "stock": variant.stock, "reserved": variant.reserved, "available": variant.available if variant.track_inventory else None,
        "stock_state": variant.stock_state, "track_inventory": variant.track_inventory, "allow_backorder": variant.allow_backorder,
        "low_stock_threshold": variant.low_stock_threshold, "weight_grams": variant.weight_grams, "is_default": variant.is_default,
    }


def product_prop(product: q.Row, currency: str, *, variants: list[q.Row] | None = None, image_url: str | None = None) -> dict[str, Any]:
    """A product as the dashboard list shows it. The price is a *range* when variants differ."""
    rows = variants or []
    prices = [v.price_minor for v in rows] or [0]
    low, high = min(prices), max(prices)
    return {
        "id": product.pk, "title": product.title, "slug": product.slug, "status": product.status, "summary": product.summary,
        "vendor": product.vendor, "product_type": product.product_type, "tags": product.tags or [], "image_url": image_url,
        "variant_count": len(rows), "price": Money(low, currency).as_prop(), "price_max": Money(high, currency).as_prop() if high != low else None,
        "stock": sum(v.stock for v in rows if v.track_inventory), "stock_state": _worst_stock_state(rows),
        "requires_shipping": product.requires_shipping, "created_at": product.created_at,
    }


def _worst_stock_state(variants: list[q.Row]) -> str:
    """The state the merchant must act on, not an average: nine in stock and one sold out is ``out_of_stock``."""
    states = {v.stock_state for v in variants}
    for state in ("out_of_stock", "low_stock", "in_stock", "untracked"):
        if state in states:
            return state
    return "untracked"
