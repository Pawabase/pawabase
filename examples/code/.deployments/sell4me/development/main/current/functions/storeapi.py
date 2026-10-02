"""The merchant API: what a store's own server-to-server integrations call (``/api/v1/*``).

Deliberately small: a partial API is something integrations are written against and then broken by, so what is here is complete and real.

* **Key authentication.** The key travels in ``x-store-key`` (or ``Authorization: Bearer sk_...``) and is matched by SHA-256 hash; the plaintext exists only in
  the merchant's hands. These are the *store's* keys, separate from Pawabase's own API keys, which the gateway still wants in ``apikey``.
* **Scope enforcement.** A key's scopes are the dashboard's permission strings, so a key can never do more than the member who made it.
* **Usage recording.** Every call stamps the key's last-used time, address and count, which is what the Developers screen shows.

One answer for every authentication failure (missing, malformed, unknown, revoked, expired): distinguishing them tells a caller which guess was closer.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import ApiError, forbidden, not_found, unprocessable
from sell4me_kit.money import Money, to_minor
from sell4me_kit.services import orders as order_service
from sell4me_kit.services.catalog import unique_slug

_LIMIT = {"limit": 600, "window": 60}


def _api(name: str, method: str, path: str, summary: str, *, scope: str | None, original: str, fields: list[dict[str, Any]] | None = None):
    return endpoint(name, method, path, area="api", summary=summary + (f" (scope `{scope}`)" if scope else ""), policy="public", fields=fields, original=original, rate_limit=_LIMIT)


async def _authenticate(c: Ctx, scope: str | None) -> q.Row:
    """Resolve the key to its store (stashed on ``c.store``) and refuse unless it holds ``scope`` (or ``<resource>.*``)."""
    header = c.headers.get("x-store-key") or ""
    if not header:
        bearer = c.headers.get("authorization") or ""
        header = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
    unauthorized = ApiError(401, "unauthorized", "A valid API key is required.")
    if not header:
        raise unauthorized
    db = await c.db()
    key = await q.first(db, "api_keys", {"key_hash": hashlib.sha256(header.encode()).hexdigest()})
    if key is None or key.revoked_at is not None:
        raise unauthorized
    expires = q.parse_dt(key.expires_at)
    if expires and expires < datetime.now(UTC):
        raise unauthorized
    store = await q.get(db, "stores", key.store_id)
    if store is None:
        raise unauthorized
    # Recorded on every call, so the Developers screen can show a key not used in six months: a key that should be revoked.
    await q.update(db, "api_keys", key.pk, {"last_used_at": datetime.now(UTC), "last_used_ip": c.client_ip, "request_count": (key.request_count or 0) + 1})
    scopes = set(key.scopes or [])
    if scope and scope not in scopes and f"{scope.split('.', 1)[0]}.*" not in scopes:
        raise forbidden(f"This key does not have the {scope} scope.")
    c.store, c.api_key = store, key
    return store


def _order(order: q.Row, items: list[q.Row]) -> dict[str, Any]:
    """Money goes out as minor units *and* formatted: integrations compute with the first and display the second without reimplementing currency rules subtly differently."""
    return {"id": order.pk, "number": order.number, "email": order.email, "currency": order.currency, "status": order.status, "payment_status": order.payment_status,
            "fulfilment_status": order.fulfilment_status, "subtotal_minor": order.subtotal_minor, "discount_minor": order.discount_minor, "shipping_minor": order.shipping_minor,
            "total_minor": order.total_minor, "refunded_minor": order.refunded_minor, "total_formatted": Money(order.total_minor, order.currency).format(),
            "tracking_number": order.tracking_number, "created_at": order.created_at, "paid_at": order.paid_at,
            "line_items": [{"id": i.pk, "title": i.title, "variant_title": i.variant_title, "sku": i.sku, "quantity": i.quantity, "unit_price_minor": i.unit_price_minor, "total_minor": i.total_minor} for i in items]}


async def _order_out(c: Ctx, order: q.Row) -> dict[str, Any]:
    return _order(order, await q.find(await c.db(), "order_items", {"order_id": order.pk}))


def _product(p: q.Row, store: q.Row, variants: list[q.Row], images: list[q.Row]) -> dict[str, Any]:
    return {"id": p.pk, "title": p.title, "slug": p.slug, "description": p.description, "status": p.status,
            "images": [{"id": i.pk, "url": i.url, "position": i.position} for i in sorted(images, key=lambda i: i.position or 0)],
            "variants": [{"id": v.pk, "title": v.title, "sku": v.sku, "price_minor": v.price_minor, "price_formatted": Money(v.price_minor, store.currency).format(),
                          "compare_at_minor": v.compare_at_minor, "currency": store.currency, "stock": v.stock, "track_inventory": v.track_inventory,
                          "available": v.available if v.track_inventory else None} for v in variants]}


async def _product_out(c: Ctx, p: q.Row) -> dict[str, Any]:
    db = await c.db()
    return _product(p, c.store, await q.find(db, "product_variants", {"product_id": p.pk}, order="id"), await q.find(db, "product_images", {"product_id": p.pk}))


def _discount(d: q.Row) -> dict[str, Any]:
    return {"id": d.pk, "code": d.code, "title": d.title, "kind": d.kind, "value": d.value, "is_active": d.is_active, "state": d.state, "usage_count": d.usage_count, "usage_limit": d.usage_limit}


def _limit(c: Ctx) -> int:
    try:
        return min(100, max(1, int(c.arg("limit", 25))))
    except (TypeError, ValueError):
        return 25


async def _write_default_variant(c: Ctx, product: q.Row, data: dict[str, Any]) -> None:
    """The single-variant path: an integration managing a simple catalogue by hand, not building an options matrix."""
    db, store = await c.db(), c.store
    variant = await q.first(db, "product_variants", {"product_id": product.pk, "is_default": True}) or await q.first(db, "product_variants", {"product_id": product.pk}, order="id")
    values: dict[str, Any] = {}
    if "price" in data:
        values["price_minor"] = to_minor(data.get("price") or 0, store.currency)
    elif variant is None:
        values["price_minor"] = 0
    if "compare_at" in data:
        values["compare_at_minor"] = to_minor(data["compare_at"], store.currency) if data.get("compare_at") else None
    if "sku" in data:
        values["sku"] = str(data.get("sku") or "").strip() or None
    if "stock" in data:
        try:
            values["stock"] = int(data.get("stock") or 0)
        except (TypeError, ValueError):
            values["stock"] = 0
    if "track_inventory" in data:
        values["track_inventory"] = bool(data.get("track_inventory"))
    if variant is None:
        await q.insert(db, "product_variants", {"product_id": product.pk, "store_id": store.pk, "title": "Default", "is_default": True, "position": 0, **values})
    elif values:
        await q.update(db, "product_variants", variant.pk, values)


@_api("api.me", "GET", "/api/v1/me", "What this key is and what it can do: the first call an integrator makes", scope=None, original="GET /api/v1/me")
async def me(c: Ctx):
    await _authenticate(c, None)
    return {"key": {"name": c.api_key.name, "prefix": c.api_key.prefix, "scopes": c.api_key.scopes or []}, "store": {"id": c.store.pk, "name": c.store.name, "slug": c.store.slug, "currency": c.store.currency}}


@_api("api.orders", "GET", "/api/v1/orders", "List orders: keyset pagination (starting_after), because an offset walks the table and shifts under a caller when an order arrives", scope="orders.read", original="GET /api/v1/orders")
async def orders_list(c: Ctx):
    await _authenticate(c, "orders.read")
    db, limit = await c.db(), _limit(c)
    where: dict[str, Any] = {"store_id": c.store.pk}
    if c.arg("status"):
        where["status"] = c.arg("status")
    if c.arg("starting_after"):
        try:
            where["id"] = q.lt(int(c.arg("starting_after")))
        except (TypeError, ValueError):
            pass
    rows = await q.find(db, "orders", where, order="id DESC", limit=limit + 1)
    more, rows = len(rows) > limit, rows[:limit]
    return {"object": "list", "has_more": more, "next_cursor": rows[-1].pk if rows and more else None, "data": [await _order_out(c, o) for o in rows]}


async def _order_or_404(c: Ctx) -> q.Row:
    order = await q.first(await c.db(), "orders", {"id": c.int_arg("order_id", required=True), "store_id": c.store.pk})
    if order is None:
        raise not_found("That order")
    return order


@_api("api.order", "GET", "/api/v1/orders/{order_id}", "One order", scope="orders.read", original="GET /api/v1/orders/{id}")
async def order_get(c: Ctx):
    await _authenticate(c, "orders.read")
    return await _order_out(c, await _order_or_404(c))


@_api("api.order_fulfil", "POST", "/api/v1/orders/{order_id}/fulfil", "Mark a paid order shipped", scope="orders.fulfil", original="POST /api/v1/orders/{id}/fulfil",
      fields=[{"name": "tracking_number", "type": "string"}, {"name": "tracking_url", "type": "string"}, {"name": "carrier", "type": "string"}])
async def order_fulfil(c: Ctx):
    await _authenticate(c, "orders.fulfil")
    order = await _order_or_404(c)
    try:
        order = await order_service.fulfil(c, store=c.store, order=order, tracking_number=(str(c.input.get("tracking_number") or "").strip() or None),
                                           tracking_url=(str(c.input.get("tracking_url") or "").strip() or None), carrier=(str(c.input.get("carrier") or "").strip() or None))
    except order_service.OrderError as error:
        raise ApiError(409, "invalid_state", str(error)) from None
    return await _order_out(c, order)


@_api("api.order_deliver", "POST", "/api/v1/orders/{order_id}/deliver", "Mark a shipped order delivered", scope="orders.fulfil", original="POST /api/v1/orders/{id}/deliver")
async def order_deliver(c: Ctx):
    await _authenticate(c, "orders.fulfil")
    order = await _order_or_404(c)
    try:
        order = await order_service.mark_delivered(c, store=c.store, order=order)
    except order_service.OrderError as error:
        raise ApiError(409, "invalid_state", str(error)) from None
    return await _order_out(c, order)


@_api("api.order_cancel", "POST", "/api/v1/orders/{order_id}/cancel", "Cancel an order (stock is released; a paid order must be refunded instead)", scope="orders.cancel", original="POST /api/v1/orders/{id}/cancel",
      fields=[{"name": "reason", "type": "string"}])
async def order_cancel(c: Ctx):
    await _authenticate(c, "orders.cancel")
    order = await _order_or_404(c)
    try:
        order = await order_service.cancel(c, store=c.store, order=order, reason=(str(c.input.get("reason") or "").strip() or None))
    except order_service.OrderError as error:
        raise ApiError(409, "invalid_state", str(error)) from None
    return await _order_out(c, order)


@_api("api.products", "GET", "/api/v1/products", "List products", scope="products.read", original="GET /api/v1/products")
async def products_list(c: Ctx):
    await _authenticate(c, "products.read")
    where: dict[str, Any] = {"store_id": c.store.pk}
    if c.arg("status"):
        where["status"] = c.arg("status")
    rows = await q.find(await c.db(), "products", where, order="id DESC", limit=_limit(c))
    return {"object": "list", "data": [await _product_out(c, p) for p in rows]}


async def _product_or_404(c: Ctx) -> q.Row:
    product = await q.first(await c.db(), "products", {"id": c.int_arg("product_id", required=True), "store_id": c.store.pk})
    if product is None:
        raise not_found("That product")
    return product


@_api("api.product", "GET", "/api/v1/products/{product_id}", "One product", scope="products.read", original="GET /api/v1/products/{id}")
async def product_get(c: Ctx):
    await _authenticate(c, "products.read")
    return await _product_out(c, await _product_or_404(c))


_PRODUCT_FIELDS = [{"name": "title", "type": "string"}, {"name": "slug", "type": "string"}, {"name": "description", "type": "text"}, {"name": "status", "type": "string"},
                   {"name": "price", "type": "number"}, {"name": "compare_at", "type": "number"}, {"name": "sku", "type": "string"}, {"name": "stock", "type": "integer"},
                   {"name": "track_inventory", "type": "boolean"}, {"name": "image_url", "type": "string"}]


@_api("api.product_create", "POST", "/api/v1/products", "Create a product with its default variant", scope="products.create", original="POST /api/v1/products", fields=_PRODUCT_FIELDS)
async def product_create(c: Ctx):
    await _authenticate(c, "products.create")
    db, store, data = await c.db(), c.store, c.input
    title = str(data.get("title") or "").strip()
    if not title:
        raise unprocessable("title is required.", "invalid_request", {"title": "title is required."})
    product = await q.insert(db, "products", {"store_id": store.pk, "slug": await unique_slug(db, "products", store, data.get("slug") or title), "title": title[:200],
                                              "description": str(data.get("description") or ""), "status": data.get("status") if data.get("status") in ("draft", "active", "archived") else "draft"})
    await _write_default_variant(c, product, data)
    if data.get("image_url"):
        await q.insert(db, "product_images", {"product_id": product.pk, "url": str(data["image_url"]), "alt": title, "position": 0})
    return await _product_out(c, product)


@_api("api.product_update", "PATCH", "/api/v1/products/{product_id}", "Update a product (price, stock and SKU act on the default variant)", scope="products.update", original="PATCH /api/v1/products/{id}",
      fields=_PRODUCT_FIELDS)
async def product_update(c: Ctx):
    await _authenticate(c, "products.update")
    db, data = await c.db(), c.input
    product = await _product_or_404(c)
    changes: dict[str, Any] = {}
    if str(data.get("title") or "").strip():
        changes["title"] = str(data["title"]).strip()[:200]
    if "description" in data:
        changes["description"] = str(data["description"] or "")
    if data.get("status") in ("draft", "active", "archived"):
        changes["status"] = data["status"]
    if changes:
        await q.update(db, "products", product.pk, changes)
    if any(k in data for k in ("price", "compare_at", "sku", "stock", "track_inventory")):
        await _write_default_variant(c, product, data)
    if data.get("image_url"):
        last = await q.first(db, "product_images", {"product_id": product.pk}, order="position DESC")
        await q.insert(db, "product_images", {"product_id": product.pk, "url": str(data["image_url"]), "alt": changes.get("title", product.title), "position": (last.position + 1) if last else 0})
    return await _product_out(c, await q.get(db, "products", product.pk))


@_api("api.product_archive", "DELETE", "/api/v1/products/{product_id}", "Archive a product: matches the dashboard's delete, and nothing with order history behind it is ever hard-deleted",
      scope="products.delete", original="DELETE /api/v1/products/{id}")
async def product_archive(c: Ctx):
    await _authenticate(c, "products.delete")
    db = await c.db()
    product = await _product_or_404(c)
    await q.update(db, "products", product.pk, {"status": "archived"})
    return await _product_out(c, await q.get(db, "products", product.pk))


@_api("api.discounts", "GET", "/api/v1/discounts", "List discounts", scope="discounts.read", original="GET /api/v1/discounts")
async def discounts_list(c: Ctx):
    await _authenticate(c, "discounts.read")
    rows = await q.find(await c.db(), "discounts", {"store_id": c.store.pk}, order="id DESC", limit=100)
    return {"object": "list", "data": [_discount(d) for d in rows]}


@_api("api.discount_create", "POST", "/api/v1/discounts", "Create a discount code", scope="discounts.manage", original="POST /api/v1/discounts",
      fields=[{"name": "code", "type": "string", "required": True}, {"name": "kind", "type": "string"}, {"name": "value", "type": "integer"}, {"name": "title", "type": "string"}])
async def discount_create(c: Ctx):
    await _authenticate(c, "discounts.manage")
    db, store, data = await c.db(), c.store, c.input
    code = str(data.get("code") or "").strip().upper()
    kind = data.get("kind") if data.get("kind") in ("percentage", "fixed", "fixed_amount", "free_shipping") else "percentage"
    kind = "fixed_amount" if kind == "fixed" else kind  # the dashboard's name for it; the API has always accepted both
    if not code:
        raise unprocessable("code is required.", "invalid_request", {"code": "code is required."})
    if await q.exists(db, "discounts", {"store_id": store.pk, "code": code}):
        raise unprocessable("That code already exists.", "invalid_request", {"code": "That code already exists."})
    try:
        value = int(data.get("value") or 0)
    except (TypeError, ValueError):
        value = 0
    if kind == "percentage":
        value = max(1, min(100, value))
    return _discount(await q.insert(db, "discounts", {"store_id": store.pk, "code": code, "kind": kind, "value": value, "title": str(data.get("title") or "").strip()[:200] or None,
                                                      "is_active": True, "usage_count": 0, "scope": "order"}))
