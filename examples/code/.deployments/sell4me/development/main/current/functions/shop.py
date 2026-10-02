"""The public shop: pages, catalogue, basket, checkout, order status, receipts, SEO files and the help widget.

A completely separate surface from the dashboard, and separate in the way that matters: the storefront names its tenant in the path
(``/shop/{store}/...``; ``GET /hosts/resolve?host=`` maps a custom domain or ``<slug>.<suffix>`` to that slug), never from a membership. A shopper has no
account, so there is no session: the **basket token** is held by the client and sent as ``x-cart-token`` (or ``cart_token`` in the body), and every response that
touches the basket returns the token in use. An order is read with its own unguessable token, a ticket with its own.

The original rendered Inertia pages; here every endpoint returns the data the page was built from, and the storefront frontend (any framework, on any host)
draws it. Page chrome (theme, header, cart count) is one call, ``shop.info``, instead of being repeated in every page object.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import ApiError, bad_request, not_found, unprocessable
from sell4me_kit.money import Money
from sell4me_kit.payments import ProviderError, get_provider
from sell4me_kit.payments.sandbox import forced_outcome, sandbox_charges
from sell4me_kit.services import analytics, carts, pagecss, pagedata, shipping, storefront as storefront_service
from sell4me_kit.services import builder as builder_service
from sell4me_kit.services import catalog
from sell4me_kit.services import checkout as checkout_service
from sell4me_kit.services import helpdesk
from sell4me_kit.services import receipt as receipt_service
from sell4me_kit.services.inventory import InsufficientStock
from sell4me_kit.services.storefront import resolve_store
from sell4me_kit.urls import storefront_url

PAGE_SIZE = 24
RECEIPT_BUCKET = "exports"
_SORTS = {"featured": "purchase_count DESC, id DESC", "newest": "id DESC", "title": "title"}


def _public(name: str, method: str, path: str, summary: str, *, original: str, fields: list[dict[str, Any]] | None = None, rate: dict[str, int] | None = None):
    """A shop endpoint: open to anyone holding the API key (shoppers are anonymous). Writes are rate-limited, because nobody is signed in to be accountable."""
    return endpoint(name, method, path, area="shop", summary=summary, policy="public", fields=fields, original=original, rate_limit=rate or ({} if method == "GET" else {"limit": 120, "window": 60}))


async def _track(c: Ctx, kind: str, **fields: Any) -> None:
    await analytics.track(await c.db(), store=c.store, kind=kind, session_token=c.cart_token, referrer=c.headers.get("referer"), **fields)


async def _basket(c: Ctx) -> q.Row:
    return await carts.find_or_create(c, c.store, c.cart_token)


async def _cart_count(c: Ctx) -> int:
    if not c.cart_token:
        return 0
    cart = await q.first(await c.db(), "carts", {"token": c.cart_token, "store_id": c.store.pk})
    if cart is None or cart.status not in ("open", "checkout"):
        return 0
    return int(await (await c.db()).scalar("SELECT COALESCE(SUM(quantity), 0) FROM cart_items WHERE cart_id = ? AND deleted_at IS NULL", [cart.pk], default=0))


async def _header_tree(c: Ctx) -> list[dict[str, Any]] | None:
    """The merchant's built header, or nothing (which means the default one). The draft for a merchant on a signed preview link."""
    entry = await q.first(await c.db(), "storefront_pages", {"store_id": c.store.pk, "kind": "header"})
    if entry is None:
        return None
    if c.previewing:
        return entry.draft or None
    return (entry.published or None) if entry.is_published else None


async def _published_page(c: Ctx, *, slug: str | None = None, kind: str | None = None) -> q.Row | None:
    """Preview serves any page, published or not, and only with a valid signed token. A slug is only ever a ``page``: the header shares the table and would otherwise answer at ``/pages/header``."""
    where: dict[str, Any] = {"store_id": c.store.pk, "slug": slug, "kind": "page"} if slug is not None else {"store_id": c.store.pk, "kind": kind}
    if c.previewing:
        return await q.first(await c.db(), "storefront_pages", where)
    entry = await q.first(await c.db(), "storefront_pages", {**where, "is_published": True})
    return entry if entry is not None and entry.published else None


async def _built(c: Ctx, entry: q.Row) -> dict[str, Any]:
    tree = (entry.draft if c.previewing else entry.published) or []
    await _track(c, "page_view")
    # The store's name, never the platform's: a shopper's browser tab says whose shop they are in.
    title = c.store.name if entry.kind == "home" else f"{entry.title} · {c.store.name}"
    return {"page": {"title": entry.title, "path": entry.path, "seo_title": entry.seo_title or title, "seo_description": entry.seo_description, "og_image_url": entry.og_image_url,
                     "noindex": bool(entry.noindex or c.previewing), "settings": entry.settings or {}},
            "tree": tree, "context": await pagedata.block_context(await c.db(), c.store, tree), "page_css": await pagecss.css_for(tree), "preview": c.previewing}


# ── resolving a shop ─────────────────────────────────────────────────────

@_public("shop.resolve", "GET", "/hosts/resolve", "Map a hostname (a verified custom domain, or <slug>.<STOREFRONT_SUFFIX>) to the shop it belongs to",
         original="the Host-header dispatch in routes/web/__init__.py", fields=None)
async def resolve_host(c: Ctx):
    host = (c.arg("host") or "").strip().lower()
    found = await resolve_store(await c.db(), await c.settings(), host)
    if found is None:
        raise not_found("A shop at that address")
    store, domain = found
    return {"store": store.slug, "name": store.name, "status": store.status, "custom_domain": bool(domain and not domain.is_platform)}


@_public("shop.info", "GET", "/shop/{store}", "The page chrome: theme, store identity, the built header (and its CSS), the basket's item count", original="shop_props() on every page")
async def info(c: Ctx):
    await c.shop()
    db, store = await c.db(), c.store
    theme = await storefront_service.ensure_theme(db, store)
    header = await _header_tree(c)
    return {"store": {"name": store.name, "slug": store.slug, "currency": store.currency, "country": store.country, "help_desk_enabled": store.help_desk_enabled,
                      "logo_url": theme.logo_url}, "theme": storefront_service.theme_prop(theme, store), "cart_count": await _cart_count(c), "header": header,
            "header_css": await pagecss.css_for(header) if header else "", "preview": c.previewing}


# ── pages ────────────────────────────────────────────────────────────────

@_public("shop.home", "GET", "/shop/{store}/home", "The homepage: always a block tree, through the same renderers the builder draws with (a store that never opened the builder gets the starter tree, live)",
         original="GET /")
async def home(c: Ctx):
    await c.shop()
    db, store = await c.db(), c.store
    entry = await _published_page(c, kind="home")
    if entry is not None:
        return await _built(c, entry)
    theme = await storefront_service.ensure_theme(db, store)
    tree = builder_service.default_tree(store.name)
    await _track(c, "page_view")
    return {"page": {"title": store.name, "path": "/", "seo_title": theme.seo_title or store.name, "seo_description": theme.seo_description, "og_image_url": theme.og_image_url,
                     "noindex": False, "settings": {}}, "tree": tree, "context": await pagedata.block_context(db, store, tree), "page_css": await pagecss.css_for(tree),
            "preview": c.previewing, "starter": True}


@_public("shop.page", "GET", "/shop/{store}/pages/{slug}", "A page the merchant built", original="GET /pages/{slug}")
async def built_page(c: Ctx):
    await c.shop()
    entry = await _published_page(c, slug=c.params["slug"])
    if entry is None:
        raise not_found("That page")
    return await _built(c, entry)


# ── catalogue ────────────────────────────────────────────────────────────

@_public("shop.products", "GET", "/shop/{store}/products", "The product listing (featured, newest or by title)", original="GET /products")
async def products(c: Ctx):
    await c.shop()
    db, store = await c.db(), c.store
    page, per_page = c.page_params(PAGE_SIZE, PAGE_SIZE)
    sort = c.arg("sort", "featured")
    total = await q.count(db, "products", {"store_id": store.pk, "status": "active"})
    rows = await q.find(db, "products", {"store_id": store.pk, "status": "active"}, order=_SORTS.get(sort, _SORTS["featured"]), limit=per_page, offset=(page - 1) * per_page)
    return {"products": [await pagedata.product_card(db, p, store) for p in rows], "sort": sort, "pagination": {"page": page, "pages": max(1, -(-total // per_page)), "total": total}}


@_public("shop.product", "GET", "/shop/{store}/products/{slug}", "One product: images, options, variants (stock shown as a number only when it is low enough to matter), SEO, related", original="GET /products/{slug}")
async def product(c: Ctx):
    await c.shop()
    db, store = await c.db(), c.store
    p = await q.first(db, "products", {"store_id": store.pk, "slug": c.params["slug"], "status": "active"})
    if p is None:
        raise not_found("That product")
    variants = await q.find(db, "product_variants", {"product_id": p.pk}, order="position")
    options = await q.find(db, "product_options", {"product_id": p.pk}, order="position")
    images = await q.find(db, "product_images", {"product_id": p.pk}, order="position")
    value_rows = await db.fetch(
        f"SELECT vov.variant_id, pov.value FROM variant_option_values vov JOIN product_option_values pov ON pov.id = vov.option_value_id JOIN product_options po ON po.id = pov.option_id "
        f"WHERE vov.deleted_at IS NULL AND vov.variant_id IN ({', '.join('?' for _ in variants) or 'NULL'}) ORDER BY po.position", [v.pk for v in variants])
    by_variant: dict[int, list[str]] = {}
    for r in value_rows:
        by_variant.setdefault(int(r["variant_id"]), []).append(r["value"])
    option_out = []
    for o in options:
        values = await q.find(db, "product_option_values", {"option_id": o.pk}, order="position")
        option_out.append({"name": o.name, "values": [v.value for v in values]})
    await _track(c, "product_view", product_id=p.pk)
    # A counter on the row so a listing never aggregates events per product; the events table stays the source of truth for rebuilding it.
    await q.increment(db, "products", p.pk, view_count=1)
    related = await q.find(db, "products", {"store_id": store.pk, "status": "active", "id": q.ne(p.pk)}, order="purchase_count DESC", limit=4)
    return {"product": {"id": p.pk, "title": p.title, "slug": p.slug, "description": p.description, "summary": p.summary, "vendor": p.vendor,
                        "images": [{"url": i.url, "alt": i.alt} for i in images], "options": option_out,
                        "variants": [{"id": v.pk, "title": v.title, "sku": v.sku, "price": Money(v.price_minor, store.currency).as_prop(),
                                      "compare_at": Money(v.compare_at_minor, store.currency).as_prop() if v.compare_at_minor and v.compare_at_minor > v.price_minor else None,
                                      "available": v.available > 0, "stock_state": v.stock_state,
                                      # Exact stock on everything would tell a competitor a merchant's whole inventory.
                                      "remaining": v.available if v.track_inventory and v.available <= 10 else None, "options": by_variant.get(v.pk, [])} for v in variants],
                        "seo": {"title": p.seo_title or p.title, "description": p.seo_description or p.summary}},
            "related": [await pagedata.product_card(db, r, store) for r in related]}


@_public("shop.collections", "GET", "/shop/{store}/collections", "Published collections", original="GET /collections")
async def collections(c: Ctx):
    await c.shop()
    rows = await q.find(await c.db(), "collections", {"store_id": c.store.pk, "is_published": True}, order="position, title")
    return {"collections": [{"title": r.title, "slug": r.slug, "description": r.description, "image_url": r.image_url} for r in rows]}


@_public("shop.collection", "GET", "/shop/{store}/collections/{slug}", "One collection and its products (manual, or computed from its rules)", original="GET /collections/{slug}")
async def collection(c: Ctx):
    await c.shop()
    db, store = await c.db(), c.store
    col = await q.first(db, "collections", {"store_id": store.pk, "slug": c.params["slug"], "is_published": True})
    if col is None:
        raise not_found("That collection")
    items = await catalog.collection_products(db, col)
    await _track(c, "collection_view", collection_id=col.pk)
    return {"collection": {"title": col.title, "slug": col.slug, "description": col.description, "image_url": col.image_url,
                           "seo": {"title": col.seo_title or col.title, "description": col.seo_description}}, "products": [await pagedata.product_card(db, p, store) for p in items[:96]]}


@_public("shop.search", "GET", "/shop/{store}/search", "Search products by title, summary or type", original="GET /search")
async def search(c: Ctx):
    await c.shop()
    db, store = await c.db(), c.store
    term = (c.arg("q") or "").strip().lower()
    rows: list[q.Row] = []
    if len(term) >= 2:
        like = f"%{term}%"
        rows = [q.Row(r) for r in await db.fetch("SELECT * FROM products WHERE store_id = ? AND deleted_at IS NULL AND status = 'active' AND (LOWER(title) LIKE ? OR LOWER(COALESCE(summary,'')) LIKE ? "
                                                  "OR LOWER(COALESCE(product_type,'')) LIKE ?) LIMIT 48", [store.pk, like, like, like])]
    return {"query": term, "products": [await pagedata.product_card(db, p, store) for p in rows]}


# ── basket ───────────────────────────────────────────────────────────────

@_public("shop.cart", "GET", "/shop/{store}/cart", "The basket as money and as props (created on first use; the token in the response is the one to keep)", original="GET /cart")
async def cart_show(c: Ctx):
    await c.shop()
    cart = await _basket(c)
    return {"cart_token": cart.token, "cart": await carts.summarise(c, store=c.store, cart=cart)}


@_public("shop.cart_add", "POST", "/shop/{store}/cart/add", "Put a variant in the basket (availability is a courtesy; the reservation at checkout is the guarantee)",
         fields=[{"name": "variant_id", "type": "integer", "required": True}, {"name": "quantity", "type": "integer"}, {"name": "cart_token", "type": "string"}], original="POST /cart/add")
async def cart_add(c: Ctx):
    await c.shop()
    db = await c.db()
    cart = await _basket(c)
    try:
        variant_id = int(c.input.get("variant_id") or 0)
        quantity = max(1, int(c.input.get("quantity") or 1))
    except (TypeError, ValueError):
        raise unprocessable("Choose an option first.", "validation_failed", {"variant_id": "Choose an option first."}) from None
    try:
        await carts.add_item(c, cart=cart, variant_id=variant_id, quantity=quantity)
    except (InsufficientStock, ValueError) as error:
        raise unprocessable(str(error), "cannot_add") from None
    variant = await q.first(db, "product_variants", {"id": variant_id, "store_id": c.store.pk})
    if variant:
        await _track(c, "add_to_cart", product_id=variant.product_id, variant_id=variant_id, quantity=quantity, value_minor=variant.price_minor * quantity)
        await q.increment(db, "products", variant.product_id, cart_count=1)
    return {"cart_token": cart.token, "cart": await carts.summarise(c, store=c.store, cart=cart), "message": "Added to your basket."}


@_public("shop.cart_update", "POST", "/shop/{store}/cart/update", "Set a line's quantity (0 removes it)",
         fields=[{"name": "item_id", "type": "integer", "required": True}, {"name": "quantity", "type": "integer", "required": True}, {"name": "cart_token", "type": "string"}], original="POST /cart/update")
async def cart_update(c: Ctx):
    await c.shop()
    cart = await _basket(c)
    try:
        item_id, quantity = int(c.input.get("item_id") or 0), int(c.input.get("quantity") or 0)
    except (TypeError, ValueError):
        raise bad_request("Say which line and how many.", "validation_failed") from None
    try:
        await carts.set_quantity(c, cart=cart, item_id=item_id, quantity=quantity)
    except InsufficientStock as error:
        raise unprocessable(str(error), "cannot_update") from None
    return {"cart_token": cart.token, "cart": await carts.summarise(c, store=c.store, cart=cart)}


@_public("shop.cart_discount", "POST", "/shop/{store}/cart/discount", "Apply a discount code, or clear it (an empty code). The refusal is written for a shopper and says something they can act on",
         fields=[{"name": "code", "type": "string"}, {"name": "cart_token", "type": "string"}], original="POST /cart/discount")
async def cart_discount(c: Ctx):
    await c.shop()
    cart = await _basket(c)
    code = (c.input.get("code") or "").strip()
    if not code:
        await carts.clear_discount(c, cart)
        message, ok = "Discount removed.", True
    else:
        result = await carts.apply_discount(c, store=c.store, cart=cart, code=code)
        ok, message = result.ok, f"{code.upper()} applied." if result.ok else (result.reason or "That code can't be used.")
    cart = await q.get(await c.db(), "carts", cart.pk)
    return {"cart_token": cart.token, "applied": ok, "message": message, "cart": await carts.summarise(c, store=c.store, cart=cart)}


@_public("shop.cart_recover", "GET", "/shop/{store}/cart/recover/{token}", "Restore an abandoned basket from the link in the recovery email: reopens the original cart and returns its token",
         original="GET /cart/recover/{token}")
async def cart_recover(c: Ctx):
    await c.shop()
    db = await c.db()
    record = await q.first(db, "abandoned_carts", {"recovery_token": c.params["token"], "store_id": c.store.pk})
    if record is None:
        raise not_found("That link")
    cart = await q.get(db, "carts", record.cart_id)
    if cart is None:
        raise not_found("That basket")
    if cart.status == "abandoned":
        await q.update(db, "carts", cart.pk, {"status": "open", "last_activity_at": datetime.now(UTC)})
        cart = await q.get(db, "carts", cart.pk)
    return {"cart_token": cart.token, "cart": await carts.summarise(c, store=c.store, cart=cart)}


# ── checkout ─────────────────────────────────────────────────────────────

async def _provider_key(c: Ctx, requested: str | None) -> str | None:
    """Paystack, run by the platform for every store: a shop need not connect a bank account to be *charged*, only to be *paid out*. ``sandbox`` only when SANDBOX_ENABLED."""
    settings, currency = await c.settings(), c.store.currency
    if requested == "sandbox":
        return "sandbox" if settings.sandbox_enabled else None
    if settings.paystack_secret_key and settings.paystack_public_key and get_provider("paystack").supports_currency(currency):
        return "paystack"
    return "sandbox" if settings.sandbox_enabled else None


@_public("shop.checkout", "GET", "/shop/{store}/checkout", "What the checkout form needs: the basket, shipping options for the destination, the payment provider", original="GET /checkout")
async def checkout_show(c: Ctx):
    await c.shop()
    store = c.store
    cart = await _basket(c)
    summary = await carts.summarise(c, store=store, cart=cart)
    if not summary["items"]:
        raise unprocessable("Your basket is empty.", "empty_cart")
    provider = await _provider_key(c, c.arg("provider"))
    if provider is None:
        raise ApiError(503, "checkout_unavailable", "Checkout is not available for this shop yet.")
    country = (c.arg("country") or store.country or "US").upper()
    weight = int(await (await c.db()).scalar("SELECT COALESCE(SUM(v.weight_grams * ci.quantity), 0) FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id "
                                             "WHERE ci.cart_id = ? AND ci.deleted_at IS NULL", [cart.pk], default=0))
    try:
        options = await shipping.options_for(c, store=store, country=country, subtotal_minor=summary["amounts"]["subtotal_minor"], weight_grams=weight)
    except shipping.ShippingUnavailable:
        options = []
    await _track(c, "begin_checkout", value_minor=summary["amounts"]["total_minor"])
    return {"cart_token": cart.token, "cart": summary, "shipping_options": options, "default_country": store.country,
            "providers": [{"key": provider, "label": get_provider(provider).label, "is_default": True, "is_test_mode": provider == "sandbox" or not (await c.settings()).app_url.startswith("https://")}]}


_ADDRESS = ("first_name", "last_name", "company", "line1", "line2", "city", "province", "postal_code", "country", "phone")


def _url(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else None


@_public("shop.checkout_start", "POST", "/shop/{store}/checkout", "Reserve stock, create the order and start the payment as one operation with a defined failure path; returns the provider URL to send the shopper to",
         fields=[{"name": "email", "type": "string", "required": True}, {"name": "first_name", "type": "string"}, {"name": "last_name", "type": "string"}, {"name": "line1", "type": "string"},
                 {"name": "line2", "type": "string"}, {"name": "city", "type": "string"}, {"name": "province", "type": "string"}, {"name": "postal_code", "type": "string"},
                 {"name": "country", "type": "string"}, {"name": "phone", "type": "string"}, {"name": "company", "type": "string"}, {"name": "shipping_rate_id", "type": "integer"},
                 {"name": "same_billing", "type": "boolean"}, {"name": "accepts_marketing", "type": "boolean"}, {"name": "provider", "type": "string"},
                 {"name": "return_url", "type": "string"}, {"name": "cancel_url", "type": "string"}, {"name": "cart_token", "type": "string"}],
         original="POST /checkout", rate={"limit": 30, "window": 60})
async def checkout_start(c: Ctx):
    await c.shop()
    store, data = c.store, c.input
    cart = await _basket(c)
    email = (data.get("email") or "").strip().lower()
    if "@" not in email:
        raise unprocessable("Enter a valid email address.", "validation_failed", {"email": "Enter a valid email address."})
    provider_key = await _provider_key(c, data.get("provider"))
    if provider_key is None:
        raise ApiError(503, "checkout_unavailable", "Checkout is not available for this shop yet.")
    address = {k: data.get(k) for k in _ADDRESS}
    address["country"] = data.get("country") or store.country
    settings = await c.settings()
    # The provider needs absolute URLs and the storefront's origin is the client's, so it may name them; the default is the shop's own hostname.
    return_url = _url(data.get("return_url")) or storefront_url(settings, store, f"/checkout/return?cart={cart.token}")
    cancel_url = _url(data.get("cancel_url")) or storefront_url(settings, store, "/cart")
    try:
        result = await checkout_service.begin(c, store=store, cart=cart, email=email, shipping_address=address, billing_address=address if data.get("same_billing", True) else None,
                                              shipping_rate_id=int(data["shipping_rate_id"]) if data.get("shipping_rate_id") else None, provider_key=provider_key,
                                              return_url=return_url, cancel_url=cancel_url, accepts_marketing=bool(data.get("accepts_marketing")),
                                              client_ip=c.client_ip, user_agent=c.headers.get("user-agent"))
    except checkout_service.CheckoutError as error:
        raise unprocessable(str(error), "checkout_failed") from None
    except ProviderError as error:
        raise ApiError(502, "provider_error", f"The payment provider refused: {error}") from None
    order = result.order
    return {"cart_token": cart.token, "order": {"number": order.number, "token": order.cart_token, "total": Money(order.total_minor, order.currency).as_prop()}, "provider": provider_key,
            "reference": result.reference, "redirect_url": result.redirect_url,
            "status_path": f"/shop/{store.slug}/orders/{order.number}/{order.cart_token}"}


@_public("shop.checkout_return", "GET", "/shop/{store}/checkout/return", "Confirm the order when the shopper comes back from the provider. Verifies rather than trusts: the provider's own answer decides, and it is idempotent",
         original="GET /checkout/return", rate={"limit": 60, "window": 60})
async def checkout_return(c: Ctx):
    await c.shop(during_maintenance=True)
    db, store = await c.db(), c.store
    token = c.arg("cart") or c.cart_token
    if not token:
        raise not_found("That order")
    order = await q.first(db, "orders", {"store_id": store.pk, "cart_token": token}, order="id DESC")
    if order is None:
        raise not_found("That order")
    payment = await q.first(db, "payments", {"order_id": order.pk}, order="id DESC")
    if payment is not None and not order.is_paid:
        order = await checkout_service.complete(c, store=store, order=order, provider_key=payment.provider, reference=c.arg("reference") or c.arg("trxref") or None)
    if order.is_paid:
        await _track(c, "purchase", order_id=order.pk, value_minor=order.total_minor)
    items = await q.find(db, "order_items", {"order_id": order.pk})
    return {"order": {"number": order.number, "email": order.email, "status": order.status, "payment_status": order.payment_status, "total": Money(order.total_minor, order.currency).as_prop(),
                      "status_path": f"/shop/{store.slug}/orders/{order.number}/{order.cart_token}",
                      "items": [{"title": i.title, "variant_title": i.variant_title, "quantity": i.quantity, "total": Money(i.total_minor, order.currency).as_prop()} for i in items]},
            "receipt": (await receipt_service.build(db, order)).as_prop() if order.is_paid else None,
            "receipt_path": f"/shop/{store.slug}/orders/{order.number}/{order.cart_token}/receipt/png" if order.is_paid else None}


# ── sandbox payments (development only) ──────────────────────────────────

async def _sandbox_charge(c: Ctx, provider_reference: str) -> Any:
    if not (await c.settings()).sandbox_enabled:
        raise not_found("That payment")
    charge = await sandbox_charges.get(provider_reference)
    if charge is None or charge.metadata.get("store") != c.store.slug:
        raise not_found("That payment")
    return charge


@_public("shop.sandbox_charge", "GET", "/shop/{store}/sandbox/{provider_reference}", "The sandbox provider's 'hosted page' data: what is being charged (only when SANDBOX_ENABLED)", original="GET /sandbox/pay/{ref}")
async def sandbox_show(c: Ctx):
    await c.shop(during_maintenance=True)
    charge = await _sandbox_charge(c, c.params["provider_reference"])
    return {"provider_reference": charge.provider_reference, "reference": charge.reference, "amount": Money(charge.amount_minor, charge.currency).as_prop(), "status": charge.status,
            "email": charge.email, "return_url": charge.return_url, "cancel_url": charge.cancel_url, "forced_outcome_hint": "An amount ending in .01 or .02 is declined."}


@_public("shop.sandbox_settle", "POST", "/shop/{store}/sandbox/{provider_reference}/settle", "Decide a sandbox charge (succeeded or failed; default: the outcome the amount forces) and confirm the order",
         fields=[{"name": "outcome", "type": "string"}], original="POST /sandbox/pay/{ref}")
async def sandbox_settle(c: Ctx):
    await c.shop(during_maintenance=True)
    charge = await _sandbox_charge(c, c.params["provider_reference"])
    outcome = c.input.get("outcome") if c.input.get("outcome") in ("succeeded", "failed") else forced_outcome(charge.amount_minor)
    charge = await sandbox_charges.settle(charge.provider_reference, outcome)
    db, store = await c.db(), c.store
    order = await q.first(db, "orders", {"store_id": store.pk, "id": int(charge.metadata.get("order_id") or 0)})
    if order is not None:
        order = await checkout_service.complete(c, store=store, order=order, provider_key="sandbox", reference=charge.reference)
    return {"status": charge.status, "order": {"number": order.number, "payment_status": order.payment_status, "status": order.status} if order else None}


# ── orders and receipts ──────────────────────────────────────────────────

async def _order_for(c: Ctx) -> q.Row:
    """Reached by a link carrying the cart token the order was placed with: unguessable and specific to that order, which lets a guest check their order without an account
    and stops anyone reading another's by incrementing the number."""
    try:
        number = int(c.params["number"])
    except (TypeError, ValueError):
        raise not_found("That order") from None
    order = await q.first(await c.db(), "orders", {"store_id": c.store.pk, "number": number})
    if order is None or not order.cart_token or not hmac.compare_digest(str(order.cart_token), str(c.params["token"])):
        raise not_found("That order")
    return order


@_public("shop.order", "GET", "/shop/{store}/orders/{number}/{token}", "A customer's view of their own order, by its own unguessable token", original="GET /orders/{number}/{token}", rate={"limit": 60, "window": 60})
async def order_status(c: Ctx):
    await c.shop(during_maintenance=True)
    db = await c.db()
    order = await _order_for(c)
    items = await q.find(db, "order_items", {"order_id": order.pk})
    addresses = {a.kind: a for a in await q.find(db, "order_addresses", {"order_id": order.pk})}
    events = await q.find(db, "order_events", {"order_id": order.pk, "is_customer_visible": True}, order="id DESC", limit=20)
    cur = order.currency
    ship = addresses.get("shipping")
    return {"receipt": (await receipt_service.build(db, order)).as_prop() if order.is_paid else None, "receipt_path": f"/shop/{c.store.slug}/orders/{order.number}/{c.params['token']}/receipt",
            "order": {"number": order.number, "email": order.email, "status": order.status, "payment_status": order.payment_status, "fulfilment_status": order.fulfilment_status,
                      "placed_at": order.placed_at, "tracking_number": order.tracking_number, "tracking_url": order.tracking_url, "shipping_method": order.shipping_method,
                      "subtotal": Money(order.subtotal_minor, cur).as_prop(), "discount": Money(order.discount_minor, cur).as_prop(), "shipping": Money(order.shipping_minor, cur).as_prop(),
                      "total": Money(order.total_minor, cur).as_prop(), "refunded": Money(order.refunded_minor or 0, cur).as_prop(),
                      "items": [{"title": i.title, "variant_title": i.variant_title, "quantity": i.quantity, "total": Money(i.total_minor, cur).as_prop()} for i in items],
                      "shipping_address": {"name": ship.name, "line1": ship.line1, "line2": ship.line2, "city": ship.city, "province": ship.province, "postal_code": ship.postal_code,
                                           "country": ship.country} if ship else None,
                      "timeline": [{"kind": e.kind, "message": e.message, "created_at": e.created_at} for e in events]}}


@_public("shop.receipt", "GET", "/shop/{store}/orders/{number}/{token}/receipt/{fmt}", "The receipt for a paid order as a png or pdf: a short-lived signed URL to the file (refused until the order is paid: a receipt for money that has not arrived says something untrue)",
         original="GET /orders/{number}/{token}/receipt/{fmt}", rate={"limit": 30, "window": 60})
async def order_receipt(c: Ctx):
    await c.shop(during_maintenance=True)
    order = await _order_for(c)
    fmt = c.params["fmt"]
    if not order.is_paid:
        raise not_found("A receipt for that order (it has not been paid yet)")
    if fmt not in ("png", "pdf"):
        raise not_found("A receipt in that format (png or pdf)")
    data = await receipt_service.build(await c.db(), order)
    body, content_type = (receipt_service.render_png(data), "image/png") if fmt == "png" else (receipt_service.render_pdf(data), "application/pdf")
    key = f"{c.store.slug}/receipts/receipt-{c.store.slug}-{order.number}.{fmt}"
    await c.runtime.storage_put(RECEIPT_BUCKET, key, body, content_type)
    return {"url": await c.runtime.storage_signed_url(RECEIPT_BUCKET, key, "GET", 300), "expires_in": 300, "content_type": content_type, "filename": key.rsplit("/", 1)[-1]}


# ── SEO files ────────────────────────────────────────────────────────────

@_public("shop.sitemap", "GET", "/shop/{store}/sitemap.xml", "An XML sitemap of everything public (returned as {content_type, body} for the storefront host to serve)", original="GET /sitemap.xml")
async def sitemap(c: Ctx):
    await c.shop()
    db, store = await c.db(), c.store
    base = storefront_url(await c.settings(), store).rstrip("/")
    urls = [f"{base}/", f"{base}/products", f"{base}/collections"]
    urls += [f"{base}/products/{r['slug']}" for r in await db.fetch("SELECT slug FROM products WHERE store_id = ? AND status = 'active' AND deleted_at IS NULL", [store.pk])]
    urls += [f"{base}/collections/{r['slug']}" for r in await db.fetch("SELECT slug FROM collections WHERE store_id = ? AND is_published = ? AND deleted_at IS NULL", [store.pk, True])]
    body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls) + "</urlset>\n"
    return {"content_type": "application/xml", "body": body}


@_public("shop.robots", "GET", "/shop/{store}/robots.txt", "robots.txt honouring the merchant's indexing choice (a store set to noindex gets a real Disallow: /)", original="GET /robots.txt")
async def robots(c: Ctx):
    await c.shop()
    theme = await storefront_service.ensure_theme(await c.db(), c.store)
    base = storefront_url(await c.settings(), c.store).rstrip("/")
    if (theme.robots_policy or "").startswith("noindex"):
        return {"content_type": "text/plain", "body": "User-agent: *\nDisallow: /\n"}
    return {"content_type": "text/plain", "body": f"User-agent: *\nAllow: /\nDisallow: /cart\nDisallow: /checkout\nSitemap: {base}/sitemap.xml\n"}


# ── the help widget ──────────────────────────────────────────────────────

async def _ticket(c: Ctx) -> q.Row:
    ticket = await q.first(await c.db(), "help_tickets", {"store_id": c.store.pk, "token": c.params["token"]})
    if ticket is None:
        raise not_found("That conversation")
    return ticket


async def _thread(c: Ctx, ticket: q.Row) -> dict[str, Any]:
    messages = await q.find(await c.db(), "help_messages", {"ticket_id": ticket.pk}, order="id")
    return {"ticket": helpdesk.serialize_ticket(ticket), "messages": [helpdesk.serialize_message(m) for m in messages], "channel": helpdesk.ticket_channel(ticket)}


@_public("shop.help_create", "POST", "/shop/{store}/help/tickets", "A shopper asks a question: opens a ticket with its first message (no account; the ticket token is the capability)",
         fields=[{"name": "name", "type": "string"}, {"name": "email", "type": "string", "required": True}, {"name": "message", "type": "text", "required": True}, {"name": "opt_in_email", "type": "boolean"}],
         original="POST /help/tickets", rate={"limit": 10, "window": 60})
async def help_create(c: Ctx):
    await c.shop()
    store, data = c.store, c.input
    if not store.help_desk_enabled:
        raise not_found("The help desk")
    message, email = (data.get("message") or "").strip(), (data.get("email") or "").strip()
    if not message or "@" not in email:
        raise unprocessable("A name, a valid email and a question are required.", "invalid_request")
    ticket = await helpdesk.open_ticket(c, store=store, name=(data.get("name") or "").strip(), email=email, message=message[:4000], opt_in_email=bool(data.get("opt_in_email")))
    return await _thread(c, ticket)


@_public("shop.help_show", "GET", "/shop/{store}/help/tickets/{token}", "A ticket's conversation, with the realtime channel for live replies (this is also the page behind the link in the reply email)",
         original="GET /help/tickets/{token}, GET /help/{token}")
async def help_show(c: Ctx):
    await c.shop()
    return await _thread(c, await _ticket(c))


@_public("shop.help_reply", "POST", "/shop/{store}/help/tickets/{token}/reply", "A shopper follows up (this reopens the ticket)", fields=[{"name": "message", "type": "text", "required": True}],
         original="POST /help/tickets/{token}/reply", rate={"limit": 30, "window": 60})
async def help_reply(c: Ctx):
    await c.shop()
    ticket = await _ticket(c)
    body = (c.input.get("message") or "").strip()
    if not body:
        raise unprocessable("Say something first.", "invalid_request")
    await helpdesk.add_customer_reply(c, c.store, ticket, body[:4000])
    return await _thread(c, await q.get(await c.db(), "help_tickets", ticket.pk))
