"""The catalogue: products, variants, collections and stock.

Product writes go through ``catalog.sync_variants``, which reconciles the option matrix rather than rebuilding it, so adding a
colour does not zero the stock on variants that already existed. Stock writes go through ``inventory.adjust``, never a direct
assignment, which is what keeps the running total and the movement history from disagreeing.
"""

from __future__ import annotations

from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import bad_request, not_found, unprocessable
from sell4me_kit.events import emit
from sell4me_kit.money import Money, to_minor
from sell4me_kit.services import analytics, catalog, inventory
from sell4me_kit.audit import changes_between

PAGE_SIZE = 25


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    return [t.strip() for t in str(value or "").split(",") if t.strip()]


# ── products ─────────────────────────────────────────────────────────────

@endpoint("products.list", "GET", "/dash/{store}/products", area="catalog", permission="products.read", summary="List products (status, search, paging)",
          original="GET /products")
async def products_list(c: Ctx):
    await c.dashboard("products.read")
    db, store = await c.db(), c.store
    status, search = c.arg("status", "all"), (c.arg("q") or "").strip()
    page, per_page = c.page_params(PAGE_SIZE)
    where, params = ["store_id = ?", "deleted_at IS NULL"], [store.pk]
    if status != "all":
        where.append("status = ?")
        params.append(status)
    if search:
        where.append("LOWER(title) LIKE ?")
        params.append(f"%{search.lower()}%")
    clause = " AND ".join(where)
    total = int(await db.scalar(f"SELECT COUNT(*) FROM products WHERE {clause}", params, default=0))
    rows = [q.Row(r) for r in await db.fetch(f"SELECT * FROM products WHERE {clause} ORDER BY id DESC LIMIT {per_page} OFFSET {(page - 1) * per_page}", params)]
    ids = [r.pk for r in rows]
    variants = await q.find(db, "product_variants", {"product_id": q.in_(ids)}, order="position") if ids else []
    images = await q.find(db, "product_images", {"product_id": q.in_(ids)}, order="position") if ids else []
    data = []
    for product in rows:
        image = next((i for i in images if i.product_id == product.pk), None)
        data.append(catalog.product_prop(product, store.currency, variants=[v for v in variants if v.product_id == product.pk], image_url=image.url if image else None))
    counts = {"all": await q.count(db, "products", {"store_id": store.pk})}
    for state in ("active", "draft", "archived"):
        counts[state] = await q.count(db, "products", {"store_id": store.pk, "status": state})
    return {"data": data, "status": status, "search": search, "counts": counts,
            "pagination": {"page": page, "per_page": per_page, "total": total, "pages": max(1, -(-total // per_page))}}


async def _find(c: Ctx, product_id: int) -> q.Row:
    product = await q.first(await c.db(), "products", {"id": product_id, "store_id": c.store.pk})
    if product is None:
        raise not_found("That product")
    return product


async def _detail(c: Ctx, product: q.Row) -> dict[str, Any]:
    db, store = await c.db(), c.store
    variants = await q.find(db, "product_variants", {"product_id": product.pk}, order="position")
    options = await q.find(db, "product_options", {"product_id": product.pk}, order="position")
    images = await q.find(db, "product_images", {"product_id": product.pk}, order="position")
    entries = await q.find(db, "collection_products", {"product_id": product.pk})
    option_values = {o.pk: await q.find(db, "product_option_values", {"option_id": o.pk}, order="position") for o in options}
    out_variants = []
    for variant in variants:
        links = await db.fetch("SELECT ov.value FROM variant_option_values vo JOIN product_option_values ov ON ov.id = vo.option_value_id "
                               "WHERE vo.variant_id = ? AND vo.deleted_at IS NULL ORDER BY ov.option_id", [variant.pk])
        out_variants.append({**catalog.variant_prop(variant, store.currency), "options": [r["value"] for r in links]})
    return {
        "id": product.pk, "title": product.title, "slug": product.slug, "description": product.description, "summary": product.summary,
        "status": product.status, "product_type": product.product_type, "vendor": product.vendor, "tags": product.tags or [],
        "requires_shipping": product.requires_shipping, "seo_title": product.seo_title, "seo_description": product.seo_description,
        "images": [{"id": i.pk, "url": i.url, "alt": i.alt, "placeholder": i.placeholder, "dominant_color": i.dominant_color,
                    "processing": i.processed_at is None} for i in images],  # still being resized: the uploader polls until this clears
        "options": [{"id": o.pk, "name": o.name, "values": [v.value for v in option_values[o.pk]]} for o in options],
        "variants": out_variants, "collection_ids": [e.collection_id for e in entries], "created_at": product.created_at,
    }


@endpoint("products.show", "GET", "/dash/{store}/products/{product_id}", area="catalog", permission="products.read",
          summary="One product: details, variants, and its 30-day performance and stock movements", original="GET /products/{id}, GET /products/{id}/edit")
async def products_show(c: Ctx):
    await c.dashboard("products.read")
    product = await _find(c, c.int_arg("product_id", required=True))
    db = await c.db()
    since, until, _ = analytics.range_bounds("30d")
    performance = next((r for r in await analytics.product_performance(db, store=c.store, since=since, until=until, limit=500) if r["id"] == product.pk), None) or {
        "revenue": Money(0, c.store.currency).as_prop(), "units": 0, "views": 0, "add_to_carts": 0, "conversion_rate": None, "refunded_units": 0}
    return {"product": await _detail(c, product), "collections": await _collection_options(c), "performance": performance,
            "movements": await _movements(db, product_id=product.pk)}


@endpoint("products.options", "GET", "/dash/{store}/product-form", area="catalog", permission="products.create", summary="What the product form needs: collections",
          original="GET /products/new")
async def product_form(c: Ctx):
    await c.dashboard("products.create")
    return {"product": None, "collections": await _collection_options(c)}


_VARIANT_INPUT = ("options", "variants", "price", "compare_at", "cost", "sku", "barcode", "stock", "weight_grams", "track_inventory")
_PRODUCT_COLUMNS = ("title", "description", "summary", "status", "product_type", "vendor", "tags", "requires_shipping", "seo_title", "seo_description")


async def _write(c: Ctx, product: q.Row | None, *, partial: bool = False) -> q.Row:
    """Create or update a product. Prices arrive as human text (``"19.99"``) and are converted here with ``to_minor``, the *only*
    place a decimal becomes an integer: a price parsed anywhere else would eventually be parsed differently.

    ``partial`` (a PATCH) keeps everything that was not sent: the original's form always sent every field, an API client does not, and a missing ``status`` must
    not quietly turn an active product back into a draft."""
    db, shop, data = await c.db(), c.store, c.input
    if partial and product is not None:
        for column in _PRODUCT_COLUMNS:
            data.setdefault(column, product[column])
    touches_variants = not partial or any(key in data for key in _VARIANT_INPUT)
    title = (data.get("title") or "").strip()
    errors: dict[str, str] = {}
    if not title:
        errors["title"] = "A product needs a title."
    options = data.get("options") if isinstance(data.get("options"), list) else []
    variants = data.get("variants") if isinstance(data.get("variants"), list) else []
    if not partial and not options and not variants and not str(data.get("price") or "").strip():
        errors["price"] = "Set a price."  # a product with no options still needs a price, sent as a flat field
    if errors:
        raise unprocessable("Check the highlighted fields.", "validation_failed", errors)
    before = {"title": product.title, "status": product.status, "summary": product.summary} if product else None
    values = {
        "title": title, "description": data.get("description") or "", "summary": (data.get("summary") or "").strip() or None,
        "status": data.get("status") if data.get("status") in ("draft", "active", "archived") else "draft",
        "product_type": (data.get("product_type") or "").strip() or None, "vendor": (data.get("vendor") or "").strip() or None,
        "tags": _tags(data.get("tags")), "requires_shipping": bool(data.get("requires_shipping", True)),
        "seo_title": (data.get("seo_title") or "").strip() or None, "seo_description": (data.get("seo_description") or "").strip() or None,
    }
    if product is None:
        product = await q.insert(db, "products", {"store_id": shop.pk, "slug": await catalog.unique_slug(db, "products", shop, data.get("slug") or title),
                                                  "is_taxable": True, "view_count": 0, "cart_count": 0, "purchase_count": 0, **values})
    else:
        changes = dict(values)
        if data.get("slug"):
            changes["slug"] = await catalog.unique_slug(db, "products", shop, data["slug"], exclude_id=product.pk)
        await q.update(db, "products", product.pk, changes)
        product = await q.get(db, "products", product.pk)
    if touches_variants:
        if partial and not options and not variants:
            # Flat fields on an existing product act on its one variant: refused when it has options, where "the price" has no single meaning.
            if await q.exists(db, "product_options", {"product_id": product.pk}):
                raise unprocessable("This product has options: send `variants` to change their prices or stock.", "validation_failed", {"variants": "Send the variants to change."})
            variants = [_variant_spec(data, shop.currency)]  # only the keys that were sent: sync_variants overwrites nothing else
        elif not options and not variants:  # normalise the simple form into the matrix form, so sync_variants has one input format
            variants = [{"price_minor": to_minor(data.get("price") or 0, shop.currency),
                         "compare_at_minor": to_minor(data["compare_at"], shop.currency) if data.get("compare_at") else None,
                         "cost_minor": to_minor(data["cost"], shop.currency) if data.get("cost") else None,
                         "sku": (data.get("sku") or "").strip() or None, "barcode": (data.get("barcode") or "").strip() or None,
                         "stock": int(data.get("stock") or 0), "weight_grams": int(data.get("weight_grams") or 0),
                         "track_inventory": bool(data.get("track_inventory", True))}]
        else:
            variants = [_variant_spec(spec, shop.currency) for spec in variants]
        await catalog.sync_variants(c, store=shop, product=product, options=options, variants=variants)
    # Only the *order* of images: they are created and deleted by the media endpoints, which own the stored object as well as the row.
    if isinstance(data.get("image_ids"), list):
        for position, image_id in enumerate(data["image_ids"]):
            try:
                await q.update_where(db, "product_images", {"id": int(image_id), "product_id": product.pk}, {"position": position})
            except (TypeError, ValueError):
                continue
    if isinstance(data.get("collection_ids"), list):
        await db.execute("DELETE FROM collection_products WHERE product_id = ?", [product.pk])
        for position, collection_id in enumerate(data["collection_ids"]):
            try:
                await q.insert(db, "collection_products", {"collection_id": int(collection_id), "product_id": product.pk, "position": position})
            except (TypeError, ValueError):
                continue
    await emit(c, "product.updated" if before else "product.created", store=shop, product=product, actor={"id": c.user_id, "email": c.email},
               changes=changes_between(before, {"title": product.title, "status": product.status, "summary": product.summary}), ip_address=c.client_ip)
    return product


def _variant_spec(spec: dict[str, Any], currency: str) -> dict[str, Any]:
    """One row of the variant matrix with its money parsed. Fields the form did not send are left out entirely: ``sync_variants`` only
    overwrites what it is given, which is what lets an existing variant keep its stock when the matrix is edited for something else."""
    out: dict[str, Any] = {"options": spec.get("options") or []}
    if "price" in spec:
        out["price_minor"] = to_minor(spec.get("price") or 0, currency)
    if "compare_at" in spec:
        out["compare_at_minor"] = to_minor(spec["compare_at"], currency) if spec["compare_at"] else None
    if "cost" in spec:
        out["cost_minor"] = to_minor(spec["cost"], currency) if spec["cost"] else None
    for field in ("sku", "barcode"):
        if field in spec:
            out[field] = (str(spec[field] or "").strip()) or None
    for field in ("stock", "weight_grams"):
        if field in spec:
            try:
                out[field] = int(spec[field] or 0)
            except (TypeError, ValueError):
                pass
    if "track_inventory" in spec:
        out["track_inventory"] = bool(spec["track_inventory"])
    return out


_PRODUCT_FIELDS = [{"name": "title", "type": "string", "required": True, "max_length": 200}, {"name": "slug", "type": "string"}, {"name": "description", "type": "text"},
                   {"name": "summary", "type": "string"}, {"name": "status", "type": "string"}, {"name": "price", "type": "string"},
                   {"name": "compare_at", "type": "string"}, {"name": "cost", "type": "string"}, {"name": "sku", "type": "string"}, {"name": "barcode", "type": "string"},
                   {"name": "stock", "type": "integer"}, {"name": "weight_grams", "type": "integer"}, {"name": "track_inventory", "type": "boolean"},
                   {"name": "product_type", "type": "string"}, {"name": "vendor", "type": "string"}, {"name": "tags", "type": "json"},
                   {"name": "requires_shipping", "type": "boolean"}, {"name": "seo_title", "type": "string"}, {"name": "seo_description", "type": "string"},
                   {"name": "options", "type": "json"}, {"name": "variants", "type": "json"}, {"name": "image_ids", "type": "json"}, {"name": "collection_ids", "type": "json"}]


@endpoint("products.create", "POST", "/dash/{store}/products", area="catalog", permission="products.create", summary="Create a product", fields=_PRODUCT_FIELDS,
          original="POST /products")
async def products_create(c: Ctx):
    await c.dashboard("products.create")
    product = await _write(c, None)
    return await _detail(c, product)


@endpoint("products.update", "PATCH", "/dash/{store}/products/{product_id}", area="catalog", permission="products.update", summary="Update a product",
          fields=_PRODUCT_FIELDS, original="POST /products/{id}")
async def products_update(c: Ctx):
    await c.dashboard("products.update")
    product = await _find(c, c.int_arg("product_id", required=True))
    product = await _write(c, product, partial=True)
    return await _detail(c, product)


@endpoint("products.archive", "POST", "/dash/{store}/products/{product_id}/archive", area="catalog", permission="products.delete",
          summary="Archive a product (never a hard delete: order lines point at it)", original="POST /products/{id}/archive")
async def products_archive(c: Ctx):
    await c.dashboard("products.delete")
    product = await _find(c, c.int_arg("product_id", required=True))
    await q.update(await c.db(), "products", product.pk, {"status": "archived"})
    await emit(c, "product.archived", store=c.store, product=product, actor={"id": c.user_id, "email": c.email}, ip_address=c.client_ip)
    return {"id": product.pk, "status": "archived"}


# ── inventory ────────────────────────────────────────────────────────────

async def _movements(db: Any, *, product_id: int | None = None, store_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where, params = ["m.deleted_at IS NULL"], []
    if product_id is not None:
        where.append("v.product_id = ?")
        params.append(product_id)
    if store_id is not None:
        where.append("m.store_id = ?")
        params.append(store_id)
    rows = await db.fetch(f"SELECT m.*, v.sku AS sku, v.title AS variant_title, p.full_name AS actor_name FROM inventory_movements m "
                          f"JOIN product_variants v ON v.id = m.variant_id LEFT JOIN profiles p ON p.user_id = m.actor_id WHERE {' AND '.join(where)} ORDER BY m.id DESC LIMIT {limit}", params)
    return [{"id": r["id"], "delta": r["delta"], "balance_after": r["balance_after"], "reason": r["reason"], "note": r["note"], "sku": r["sku"],
             "variant_title": r["variant_title"], "actor": r["actor_name"] or "System", "created_at": r["created_at"]} for r in rows]


@endpoint("inventory.list", "GET", "/dash/{store}/inventory", area="catalog", permission="inventory.read", summary="Stock levels per variant (all, low, out, tracked)",
          original="GET /inventory")
async def inventory_list(c: Ctx):
    await c.dashboard("inventory.read")
    db, store = await c.db(), c.store
    view, search = c.arg("view", "all"), (c.arg("q") or "").strip()
    where, params = ["v.store_id = ?", "v.deleted_at IS NULL"], [store.pk]
    if search:
        where.append("(LOWER(COALESCE(v.sku, '')) LIKE ? OR LOWER(p.title) LIKE ?)")
        params += [f"%{search.lower()}%"] * 2
    rows = await db.fetch(f"SELECT v.*, p.title AS product_title, p.status AS product_status FROM product_variants v JOIN products p ON p.id = v.product_id "
                          f"WHERE {' AND '.join(where)} ORDER BY v.product_id, v.position LIMIT 500", params)
    rows = [q.Row(r) for r in rows]

    def keep(v: q.Row) -> bool:
        return (v.stock_state == "low_stock") if view == "low" else (v.stock_state == "out_of_stock") if view == "out" else v.track_inventory if view == "tracked" else True

    return {"data": [{**catalog.variant_prop(v, store.currency), "product_id": v.product_id, "product_title": v["product_title"], "product_status": v["product_status"]}
                     for v in rows if keep(v)],
            "view": view, "search": search,
            "counts": {"all": await q.count(db, "product_variants", {"store_id": store.pk}), "low": sum(1 for v in rows if v.stock_state == "low_stock"),
                       "out": sum(1 for v in rows if v.stock_state == "out_of_stock")},
            "movements": await _movements(db, store_id=store.pk)}


@endpoint("inventory.adjust", "POST", "/dash/{store}/inventory/{variant_id}", area="catalog", permission="inventory.update",
          summary="Adjust one variant's stock, by delta or to an absolute count, with a reason",
          fields=[{"name": "mode", "type": "string"}, {"name": "value", "type": "integer"}, {"name": "reason", "type": "string"}, {"name": "note", "type": "string"},
                  {"name": "allow_negative", "type": "boolean"}], original="POST /inventory/{variant_id}")
async def inventory_adjust(c: Ctx):
    """Accepts a delta (``+5``) or an absolute count (``mode: "set"``): a merchant counting a shelf thinks in absolutes, one receiving a delivery in deltas.
    Both become a signed movement, so the history reads the same either way."""
    await c.dashboard("inventory.update")
    db, store = await c.db(), c.store
    variant = await q.first(db, "product_variants", {"id": c.int_arg("variant_id", required=True), "store_id": store.pk})
    if variant is None:
        raise not_found("That variant")
    try:
        delta = (int(c.input.get("value") or 0) - variant.stock) if c.input.get("mode") == "set" else int(c.input.get("value") or 0)
    except (TypeError, ValueError):
        raise bad_request("Enter a whole number.") from None
    if delta == 0:
        return {"changed": False, "message": "Nothing to change.", "stock": variant.stock}
    try:
        movement = await inventory.adjust(c, store=store, variant=variant, delta=delta, reason=c.input.get("reason") or "adjustment",
                                          note=(c.input.get("note") or "").strip() or None, actor_id=c.user_id, allow_negative=bool(c.input.get("allow_negative")))
    except inventory.InsufficientStock as error:
        raise unprocessable(str(error), "insufficient_stock", {"available": error.available}) from error
    return {"changed": True, "delta": delta, "stock": movement.balance_after, "movement": dict(movement)}


# ── collections ──────────────────────────────────────────────────────────

async def _collection_options(c: Ctx) -> list[dict[str, Any]]:
    return [{"id": r.pk, "title": r.title} for r in await q.find(await c.db(), "collections", {"store_id": c.store.pk}, order="title")]


@endpoint("collections.list", "GET", "/dash/{store}/collections", area="catalog", permission="products.read", summary="List collections with product counts and the product picker's options",
          original="GET /collections")
async def collections_list(c: Ctx):
    await c.dashboard("products.read")
    db = await c.db()
    out = []
    for col in await q.find(db, "collections", {"store_id": c.store.pk}, order="position, title"):
        products = await catalog.collection_products(db, col, published_only=False)
        out.append({"id": col.pk, "title": col.title, "slug": col.slug, "description": col.description, "kind": col.kind, "rules": col.rules or [],
                    "image_url": col.image_url, "is_published": col.is_published, "product_count": len(products), "product_ids": [p.pk for p in products] if col.kind == "manual" else []})
    options = [{"id": r.pk, "title": r.title} for r in await q.find(db, "products", {"store_id": c.store.pk}, order="title", limit=500)]
    return {"data": out, "products": options}


@endpoint("collections.save", "POST", "/dash/{store}/collections", area="catalog", permission="products.update", summary="Create or update a collection (manual or automatic)",
          fields=[{"name": "id", "type": "integer"}, {"name": "title", "type": "string", "required": True}, {"name": "slug", "type": "string"}, {"name": "description", "type": "text"},
                  {"name": "image_url", "type": "string"}, {"name": "kind", "type": "string"}, {"name": "rules", "type": "json"}, {"name": "is_published", "type": "boolean"},
                  {"name": "product_ids", "type": "json"}, {"name": "seo_title", "type": "string"}, {"name": "seo_description", "type": "string"}],
          original="POST /collections")
async def collections_save(c: Ctx):
    await c.dashboard("products.update")
    db, shop, data = await c.db(), c.store, c.input
    title = (data.get("title") or "").strip()
    if not title:
        raise unprocessable("A collection needs a title.", "validation_failed", {"title": "A collection needs a title."})
    collection = await q.first(db, "collections", {"id": int(data["id"]), "store_id": shop.pk}) if data.get("id") else None
    values = {"title": title, "description": data.get("description") or "", "image_url": (data.get("image_url") or "").strip() or None,
              "kind": data.get("kind") if data.get("kind") in ("manual", "automatic") else "manual", "rules": data.get("rules") if isinstance(data.get("rules"), list) else [],
              "is_published": bool(data.get("is_published", True)), "seo_title": (data.get("seo_title") or "").strip() or None,
              "seo_description": (data.get("seo_description") or "").strip() or None}
    if collection is None:
        collection = await q.insert(db, "collections", {"store_id": shop.pk, "slug": await catalog.unique_slug(db, "collections", shop, data.get("slug") or title),
                                                         "position": 0, **values})
    else:
        await q.update(db, "collections", collection.pk, values)
        collection = await q.get(db, "collections", collection.pk)
    if collection.kind == "manual" and isinstance(data.get("product_ids"), list):
        await db.execute("DELETE FROM collection_products WHERE collection_id = ?", [collection.pk])
        for position, product_id in enumerate(data["product_ids"]):
            try:
                await q.insert(db, "collection_products", {"collection_id": collection.pk, "product_id": int(product_id), "position": position})
            except (TypeError, ValueError):
                continue
    return dict(collection)
