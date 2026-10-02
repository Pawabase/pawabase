"""Resolving what a built page refers to, before it is rendered.

A block tree holds *references* — "the four newest products in collection 12",
"this featured product" — not the data itself. Something has to turn those into
rows, and where that happens decides whether a homepage is one query or thirty.

It happens here, once, server-side:

* The tree is walked first and every id it mentions collected, so two grids
  pointing at the same collection cost one query rather than two.
* Only what is referenced is loaded. A page with no `product_grid` fetches no
  products at all.
* Grids are keyed by collection id rather than flattened into one list, because
  a page may hold two grids drawn from two different collections and a single
  list could only ever serve one of them.

The alternative — shipping the catalogue and filtering in the browser — means a
store with 4,000 products sends 4,000 products to render twelve tiles.
"""

from __future__ import annotations

from typing import Any

from .. import q
from ..money import Money
from . import catalog

__all__ = ["block_context", "product_card"]

#: Most products any one grid may draw. The block's own `limit` is capped by the
#: registry; this is the backstop for a tree written before that cap existed.
GRID_CEILING = 24

#: Blocks that name a collection, and the prop they name it in. The prop is the
#: registry's field name (`app/services/builder.py`) — `collection`, not
#: `collection_id`. These said `collection_id` and `product_id`, props no block
#: has, so a grid set to a collection was never given that collection's
#: products and a featured product was never fetched by id; both silently fell
#: back to the newest products.
_COLLECTION_REFS = {"product_grid": "collection"}
#: Blocks that name a single product.
_PRODUCT_REFS = {"featured_product": "product"}


async def product_card(db: Any, product: q.Row, store: q.Row, *, variants: list[q.Row] | None = None, images: list[q.Row] | None = None) -> dict[str, Any]:
    """A product as a grid tile. The price is a range when the variants differ (one price on a product sold from 19 to 45 is wrong for most of it)."""
    if not variants:
        variants = await q.find(db, "product_variants", {"product_id": product.pk})
    if images is None:
        images = await q.find(db, "product_images", {"product_id": product.pk})
    images = sorted(images, key=lambda image: image.position or 0)
    prices = [v.price_minor for v in variants] or [0]
    low, high = min(prices), max(prices)
    # The "was" price belongs to the variant whose price is shown: the highest compare-at across all variants put a Large's old
    # price beside a Regular's current one and overstated the saving.
    compare = max(((v.compare_at_minor or 0) for v in variants if v.price_minor == low), default=0)
    return {
        "id": product.pk, "title": product.title, "slug": product.slug, "summary": product.summary,
        "image_url": images[0].url if images else None, "price": Money(low, store.currency).as_prop(),
        "price_max": Money(high, store.currency).as_prop() if high != low else None,
        "compare_at": Money(compare, store.currency).as_prop() if compare > low else None,
        "available": any(v.available > 0 for v in variants),
    }


async def _with_children(db: Any, products: list[q.Row]) -> list[tuple[q.Row, list[q.Row], list[q.Row]]]:
    ids = [p.pk for p in products]
    variants = await q.find(db, "product_variants", {"product_id": q.in_(ids)}) if ids else []
    images = await q.find(db, "product_images", {"product_id": q.in_(ids)}) if ids else []
    return [(p, [v for v in variants if v.product_id == p.pk], [i for i in images if i.product_id == p.pk]) for p in products]


async def block_context(db: Any, store: q.Row, tree: list[dict[str, Any]]) -> dict[str, Any]:
    """Everything the renderers will look up while drawing this tree (the client's ``BlockContext``)."""
    collection_ids, product_ids, wants_collections, wants_products = _references(tree)
    context: dict[str, Any] = {"products": [], "productsByCollection": {}, "collections": []}
    if wants_collections:
        collections = await q.find(db, "collections", {"store_id": store.pk, "is_published": True}, order="position, id", limit=24)
        context["collections"] = [{"id": e.pk, "title": e.title, "slug": e.slug, "image_url": e.image_url, "description": e.description} for e in collections]
    if wants_products or product_ids:
        fallback = await q.find(db, "products", {"store_id": store.pk, "status": "active"}, order="purchase_count DESC, id DESC", limit=GRID_CEILING)
        named = await q.find(db, "products", {"id": q.in_(list(product_ids)), "store_id": store.pk}) if product_ids else []
        seen: set[int] = set()
        ordered: list[q.Row] = []
        for product in [*named, *fallback]:  # a named product may not be a best seller: fetched by id and put first
            if product.pk not in seen:
                seen.add(product.pk)
                ordered.append(product)
        context["products"] = [await product_card(db, p, store, variants=v, images=i) for p, v, i in await _with_children(db, ordered)]
    for collection_id in collection_ids:
        collection = await q.first(db, "collections", {"id": collection_id, "store_id": store.pk})
        if collection is None:  # a grid pointing at a deleted collection falls through to the fallback list
            continue
        products = (await catalog.collection_products(db, collection))[:GRID_CEILING]
        context["productsByCollection"][str(collection_id)] = [await product_card(db, p, store, variants=v, images=i) for p, v, i in await _with_children(db, products)]
    return context


def _references(tree: list[dict[str, Any]]) -> tuple[set[int], set[int], bool, bool]:
    """Which ids this tree mentions, and which of the two lists it needs.

    One walk for all four answers. Walking per block type would mean four
    traversals of the same tree to learn things a single pass already knows.
    """
    collection_ids: set[int] = set()
    product_ids: set[int] = set()
    wants_collections = False
    wants_products = False

    def visit(blocks: Any) -> None:
        nonlocal wants_collections, wants_products
        if not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            props = block.get("props") or {}

            if block_type == "collection_list":
                wants_collections = True
            if block_type in ("product_grid", "featured_product"):
                wants_products = True

            if (field := _COLLECTION_REFS.get(str(block_type))) and (raw := props.get(field)):
                if (parsed := _as_id(raw)) is not None:
                    collection_ids.add(parsed)
            if (field := _PRODUCT_REFS.get(str(block_type))) and (raw := props.get(field)):
                if (parsed := _as_id(raw)) is not None:
                    product_ids.add(parsed)

            visit(block.get("children"))

    visit(tree)
    return collection_ids, product_ids, wants_collections, wants_products


def _as_id(value: Any) -> int | None:
    """A reference as an integer, or nothing.

    The builder stores ids as strings (a `<select>` value is always text), and
    a tree hand-edited through the API might hold anything at all.
    """
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
