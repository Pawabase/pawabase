"""Build commerce.blueprint.json: a multi-store commerce backend (Sell4Me-class).

Run:  python build.py            → writes commerce.blueprint.json next to this file
Then: Studio → New project → A blueprint (JSON) → choose the file.

Model
-----
* A **store** is an Akountz organization. Merchants create an organization
  (POST /auth/v1/orgs), sign in for it ({"org": "<slug>"}), then register the
  store (POST /rest/v1/merchant/stores). Every store-owned row carries
  ``store_id`` = the organization slug, so store rules are one equality that is
  pushed into SQL: ``store_id = $auth.org``.
* Org roles decide staff power: owner/admin manage, member operates, viewer reads.
* **Shoppers** are users with the ``customer`` role (the default at sign-up).
  Anonymous visitors browse active products with only a publishable key.
* Money is in minor units (kobo/cents) as integers.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

HERE = Path(__file__).parent


# ── helpers ──────────────────────────────────────────────────────────────────


def F(name, type="string", **kw):
    return {"name": name, "type": type, **kw}


def ops(list_=None, get=None, create=None, update=None, delete=None):
    spec = {"list": list_, "get": get, "create": create, "update": update, "delete": delete}
    return {
        op: ({"enabled": True, "policy": p} if p is not False else {"enabled": False, "policy": None})
        for op, p in spec.items()
    }


def BT(name, resource, field):
    return {"name": name, "type": "belongs_to", "resource": resource, "field": field}


def HM(name, resource, field):
    return {"name": name, "type": "has_many", "resource": resource, "field": field}


def resource(name, description, fields, operations, *, relations=(), tags=(), owner=None, **extra):
    return {
        "name": name,
        "description": description,
        "id_type": "integer",
        "fields": fields,
        "operations": operations,
        "relations": list(relations),
        "owner_field": owner,
        "timestamps": True,
        "events": extra.pop("events", True),
        "realtime": extra.pop("realtime", False),
        "cache_ttl": extra.pop("cache_ttl", 0),
        "rate_limit": extra.pop("rate_limit", {}),
        "transformer": extra.pop("transformer", None),
        "tags": list(tags),
        **extra,
    }


def flow(name, description, nodes, edges, timeout=60):
    """nodes: [(id, block, config)], edges: [(source, target, handle?)] — laid out top-down."""
    children, indegree = defaultdict(list), defaultdict(int)
    for e in edges:
        children[e[0]].append(e[1])
        indegree[e[1]] += 1
    depth, queue = {}, deque([(n[0], 0) for n in nodes if indegree[n[0]] == 0])
    while queue:
        node, d = queue.popleft()
        if depth.get(node, -1) >= d:
            continue
        depth[node] = d
        queue.extend((c, d + 1) for c in children[node])
    columns = defaultdict(int)
    out_nodes = []
    for node_id, block, config in nodes:
        d = depth.get(node_id, 0)
        out_nodes.append(
            {"id": node_id, "position": {"x": 80 + columns[d] * 280, "y": 60 + d * 150}, "data": {"block": block, "config": config}}
        )
        columns[d] += 1
    out_edges = [
        {"id": f"{e[0]}-{e[2] if len(e) > 2 else 'next'}-{e[1]}", "source": e[0], "target": e[1], **({"sourceHandle": e[2]} if len(e) > 2 else {})}
        for e in edges
    ]
    return {"name": name, "description": description, "definition": {"nodes": out_nodes, "edges": out_edges},
            "enabled": True, "timeout": timeout, "record_runs": True}


def route(method, path, name, description, policy, handler, *, tags, input_schema=None, input_fields=None, rate=None, cache=0):
    return {"method": method, "path": path, "name": name, "description": description, "policy": policy,
            "input_schema": input_schema, "input_fields": input_fields, "response_schema": None, "transformer": None,
            "handler_type": "flow", "handler": handler, "rate_limit": rate or {}, "cache_ttl": cache, "tags": tags, "enabled": True}


CURRENCIES = ["NGN", "USD", "GBP", "EUR", "KES", "GHS", "ZAR"]
STAFF_ROLES = ["owner", "admin", "member", "viewer"]
EDITOR_ROLES = ["owner", "admin", "member"]
MANAGER_ROLES = ["owner", "admin"]
SLUG = r"^[a-z0-9][a-z0-9-]{1,62}$"
MONEY = {"type": "integer", "minimum": 0}


def money(name, **kw):
    return F(name, "integer", minimum=0, **kw)


# ── roles, schemas, transformers, policies ───────────────────────────────────

ROLES = [
    {"name": "platform_admin", "description": "Operates the whole marketplace", "permissions": ["*"]},
    {"name": "customer", "description": "A shopper (given at sign-up)", "permissions": ["shop.buy", "shop.review"]},
    {"name": "support_agent", "description": "Platform support: reads orders and tickets across stores",
     "permissions": ["support.read", "support.reply"]},
]

SCHEMAS = [
    {"name": "Address", "description": "A postal address.", "fields": [
        F("first_name", required=True, max_length=80), F("last_name", required=True, max_length=80),
        F("company", max_length=120), F("line1", required=True, max_length=160), F("line2", max_length=160),
        F("city", required=True, max_length=80), F("province", max_length=80), F("postal_code", max_length=20),
        F("country", required=True, pattern=r"^[A-Z]{2}$", example="NG"), F("phone", max_length=32)]},
    {"name": "CartLine", "description": "Add a variant to a cart.", "fields": [
        F("cart_token", max_length=64, description="Omit to start a new cart"), F("store_id", required=True, pattern=SLUG),
        F("variant_id", "integer", required=True), F("quantity", "integer", required=True, minimum=1, maximum=999)]},
    {"name": "CartQuantity", "description": "Change or remove a cart line.", "fields": [
        F("cart_token", required=True, max_length=64), F("item_id", "integer", required=True),
        F("quantity", "integer", required=True, minimum=0, maximum=999, description="0 removes the line")]},
    {"name": "DiscountApply", "description": "Apply a discount code to a cart.", "fields": [
        F("cart_token", required=True, max_length=64), F("code", required=True, max_length=40)]},
    {"name": "CheckoutInput", "description": "Turn a cart into an order.", "fields": [
        F("cart_token", required=True, max_length=64), F("email", "email", required=True), F("phone", max_length=32),
        F("shipping_address", "ref", schema="Address", required=True), F("billing_address", "ref", schema="Address", nullable=True),
        F("note", "text", max_length=1000), F("campaign_id", "integer", nullable=True)]},
    {"name": "FulfilInput", "description": "Ship an order.", "fields": [
        F("carrier", max_length=60), F("tracking_number", max_length=80), F("tracking_url", "url"), F("notify_customer", "boolean", default=True)]},
    {"name": "RefundInput", "description": "Refund part or all of an order.", "fields": [
        money("amount_minor", required=True), F("reason", required=True, enum=["customer_request", "damaged", "not_received", "wrong_item", "fraud", "other"]),
        F("note", "text", max_length=1000), F("restock", "boolean", default=False)]},
    {"name": "CancelInput", "description": "Cancel an order.", "fields": [
        F("reason", required=True, enum=["customer_request", "fraud", "inventory", "declined", "other"]), F("note", "text", max_length=1000)]},
    {"name": "InventoryAdjust", "description": "Change stock by a delta.", "fields": [
        F("variant_id", "integer", required=True), F("delta", "integer", required=True, minimum=-100000, maximum=100000),
        F("reason", required=True, enum=["received", "correction", "damaged", "lost", "returned", "count"]), F("note", max_length=240)]},
    {"name": "MarkPaid", "description": "Record an offline payment (cash, transfer, POS).", "fields": [
        F("method", required=True, enum=["cash", "bank_transfer", "pos_card", "mobile_money"]), F("reference", max_length=80)]},
    {"name": "TicketInput", "description": "A shopper contacts a store.", "fields": [
        F("store_id", required=True, pattern=SLUG), F("name", required=True, max_length=120), F("email", "email", required=True),
        F("subject", required=True, max_length=200), F("message", "text", required=True, max_length=5000), F("order_number", "integer")]},
    {"name": "TicketReply", "description": "Staff reply to a ticket.", "fields": [
        F("message", "text", required=True, max_length=5000), F("close", "boolean", default=False)]},
    {"name": "StoreSetup", "description": "Register the store for the active organization.", "fields": [
        F("name", required=True, max_length=120), F("currency", required=True, enum=CURRENCIES), F("country", required=True, pattern=r"^[A-Z]{2}$"),
        F("email", "email", required=True), F("timezone", default="Africa/Lagos", max_length=60)]},
    {"name": "ReviewInput", "description": "A shopper reviews a product.", "fields": [
        F("product_id", "integer", required=True), F("rating", "integer", required=True, minimum=1, maximum=5),
        F("title", max_length=120), F("body", "text", max_length=4000)]},
]

TRANSFORMERS = [
    {"name": "public_variant", "description": "What shoppers see of a variant: no cost, no reservations.",
     "definition": {"omit": ["cost_minor", "reserved"]}},
    {"name": "customer_order", "description": "Orders as shoppers see them.",
     "definition": {"omit": ["client_ip", "risk_score", "risk_flags", "cost_minor"]}},
]


def store_scope(roles):
    """Staff of the record's store, with one of *roles*. Pushed into SQL as store_id = <org>."""
    return {"all": [
        {"exists": "$auth.org"},
        {"in": ["$auth.org_role", roles]},
        {"eq": ["$record.store_id", "$auth.org"]},
    ]}


def store_input(roles):
    """Creating a row for your own store only."""
    return {"all": [
        {"exists": "$auth.org"},
        {"in": ["$auth.org_role", roles]},
        {"eq": ["$input.store_id", "$auth.org"]},
    ]}


PLATFORM = {"role": ["platform_admin"]}

POLICIES = [
    {"name": "store_staff", "description": "Any staff member of the record's store (owner, admin, member, viewer).", "condition": store_scope(STAFF_ROLES)},
    {"name": "store_editor", "description": "Store staff who can operate: owner, admin, member.", "condition": store_scope(EDITOR_ROLES)},
    {"name": "store_manager", "description": "Store owners and admins.", "condition": store_scope(MANAGER_ROLES)},
    {"name": "store_editor_create", "description": "Operating staff creating rows for their own store.", "condition": store_input(EDITOR_ROLES)},
    {"name": "store_manager_create", "description": "Owners and admins creating rows for their own store.", "condition": store_input(MANAGER_ROLES)},
    {"name": "platform_admin", "description": "Marketplace operators.", "condition": PLATFORM},
    {"name": "catalog_read", "description": "Everyone sees active items; staff see everything in their store.",
     "condition": {"any": [{"eq": ["$record.status", "active"]}, store_scope(STAFF_ROLES), PLATFORM]}},
    {"name": "published_read", "description": "Everyone sees published items; staff see everything in their store.",
     "condition": {"any": [{"eq": ["$record.is_published", True]}, store_scope(STAFF_ROLES), PLATFORM]}},
    {"name": "store_or_owner", "description": "The store's staff, the shopper the row belongs to, or platform admins.",
     "condition": {"any": [store_scope(STAFF_ROLES), {"owner": "user_id"}, PLATFORM]}},
    {"name": "store_or_support", "description": "The store's staff, or platform support.",
     "condition": {"any": [store_scope(STAFF_ROLES), {"role": ["platform_admin", "support_agent"]}]}},
    {"name": "shopper", "description": "Signed-in shoppers.", "condition": {"all": [{"authenticated": True}, {"role": ["customer", "platform_admin"]}]}},
    {"name": "store_owner_setup", "description": "The owner of the active organization.", "condition": {"all": [{"exists": "$auth.org"}, {"eq": ["$auth.org_role", "owner"]}]}},
    {"name": "store_operator", "description": "Operating staff of the active organization (for routes).", "condition": {"all": [{"exists": "$auth.org"}, {"in": ["$auth.org_role", EDITOR_ROLES]}]}},
    {"name": "store_member", "description": "Any staff of the active organization (for routes).", "condition": {"all": [{"exists": "$auth.org"}, {"in": ["$auth.org_role", STAFF_ROLES]}]}},
    {"name": "approved_reviews", "description": "Everyone sees approved reviews; staff see all reviews of their store; authors see their own.",
     "condition": {"any": [{"eq": ["$record.status", "approved"]}, store_scope(STAFF_ROLES), {"owner": "user_id"}]}},
]

STAFF, EDITOR, MANAGER = "store_staff", "store_editor", "store_manager"
EDITOR_NEW, MANAGER_NEW = "store_editor_create", "store_manager_create"
S = F("store_id", required=True, pattern=SLUG, description="The store (organization slug)")


# ── resources ────────────────────────────────────────────────────────────────

RESOURCES = [
    # Stores and storefront
    resource("stores", "Merchants' stores. slug is the store's organization.", [
        F("slug", required=True, pattern=SLUG), F("name", required=True, max_length=120), F("email", "email", required=True),
        F("phone", max_length=32), F("currency", required=True, enum=CURRENCIES), F("country", required=True, pattern=r"^[A-Z]{2}$"),
        F("timezone", default="Africa/Lagos"), F("logo_url", "url"), F("description", "text"),
        F("status", enum=["active", "paused", "suspended"], default="active"), F("plan", enum=["starter", "growth", "scale"], default="starter"),
        F("order_prefix", default="#", max_length=8), F("platform_fee_bps", "integer", default=150, minimum=0, maximum=3000),
        F("owner_user_id", read_only=True)],
        ops(list_="public", get="public", create=False, update={"all": [{"eq": ["$record.slug", "$auth.org"]}, {"in": ["$auth.org_role", MANAGER_ROLES]}]}, delete="platform_admin"),
        tags=["Stores"], cache_ttl=60),
    resource("themes", "Storefront look: colours, fonts, sections.", [
        S, F("name", required=True, max_length=80), F("is_active", "boolean", default=False),
        F("colors", "json"), F("fonts", "json"), F("sections", "json"), F("header_links", "json"), F("footer_links", "json"),
        F("announcement", max_length=200), F("seo_title", max_length=70), F("seo_description", max_length=160)],
        ops(list_="public", get="public", create=MANAGER_NEW, update=MANAGER, delete=MANAGER), tags=["Storefront"], cache_ttl=60),
    resource("pages", "Storefront pages (about, FAQ, policies).", [
        S, F("title", required=True, max_length=160), F("handle", required=True, pattern=r"^[a-z0-9-]+$"),
        F("body", "text"), F("is_published", "boolean", default=False), F("seo_title", max_length=70), F("seo_description", max_length=160)],
        ops(list_="published_read", get="published_read", create=EDITOR_NEW, update=EDITOR, delete=MANAGER), tags=["Storefront"], cache_ttl=60),
    resource("domains", "Custom domains pointed at a storefront.", [
        S, F("hostname", required=True, max_length=253), F("is_primary", "boolean", default=False),
        F("status", enum=["pending", "verified", "failed"], default="pending"), F("verification_token", read_only=True)],
        ops(list_=STAFF, get=STAFF, create=MANAGER_NEW, update=MANAGER, delete=MANAGER), tags=["Storefront"]),

    # Catalog
    resource("products", "The catalog.", [
        S, F("title", required=True, max_length=200), F("handle", required=True, pattern=r"^[a-z0-9-]+$", description="URL slug, unique per store"),
        F("summary", max_length=300), F("description", "text"), F("status", enum=["draft", "active", "archived"], default="draft"),
        F("product_type", max_length=80), F("vendor", max_length=80), F("tags", "array", items={"type": "string"}, default=[]),
        F("requires_shipping", "boolean", default=True), F("is_taxable", "boolean", default=True),
        money("price_from_minor", description="Lowest variant price, for listings"), F("image_url", "url"),
        F("rating_avg", "number", minimum=0, maximum=5), F("rating_count", "integer", default=0),
        F("view_count", "integer", default=0), F("cart_count", "integer", default=0), F("purchase_count", "integer", default=0),
        F("seo_title", max_length=70), F("seo_description", max_length=160), F("published_at", "datetime")],
        ops(list_="catalog_read", get="catalog_read", create=EDITOR_NEW, update=EDITOR, delete=MANAGER),
        relations=[HM("variants", "product_variants", "product_id"), HM("options", "product_options", "product_id"),
                   HM("images", "product_images", "product_id"), HM("reviews", "reviews", "product_id")],
        tags=["Catalog"], realtime=True),
    resource("product_options", "Option names per product (Size, Colour).", [
        S, F("product_id", "integer", required=True), F("name", required=True, max_length=40),
        F("values", "array", items={"type": "string"}, default=[]), F("position", "integer", default=0)],
        ops(list_="public", get="public", create=EDITOR_NEW, update=EDITOR, delete=EDITOR),
        relations=[BT("product", "products", "product_id")], tags=["Catalog"]),
    resource("product_variants", "Purchasable variants with price and stock.", [
        S, F("product_id", "integer", required=True), F("title", required=True, max_length=120, description="e.g. Large / Blue"),
        F("options", "json", description='{"Size": "L", "Colour": "Blue"}'), F("sku", max_length=64), F("barcode", max_length=64),
        money("price_minor", required=True), money("compare_at_minor"), money("cost_minor"),
        F("stock", "integer", default=0), F("reserved", "integer", default=0, minimum=0),
        F("track_inventory", "boolean", default=True), F("allow_backorder", "boolean", default=False),
        F("low_stock_threshold", "integer", default=5, minimum=0), F("weight_grams", "integer", minimum=0),
        F("position", "integer", default=0), F("is_default", "boolean", default=False)],
        ops(list_="public", get="public", create=EDITOR_NEW, update=EDITOR, delete=MANAGER),
        relations=[BT("product", "products", "product_id")], tags=["Catalog"], transformer="public_variant", realtime=True),
    resource("product_images", "Product media.", [
        S, F("product_id", "integer", required=True), F("variant_id", "integer"), F("url", "url", required=True),
        F("storage_key", max_length=300), F("alt", max_length=200), F("position", "integer", default=0),
        F("width", "integer"), F("height", "integer")],
        ops(list_="public", get="public", create=EDITOR_NEW, update=EDITOR, delete=EDITOR),
        relations=[BT("product", "products", "product_id")], tags=["Catalog"]),
    resource("collections", "Curated or rule-based product groups.", [
        S, F("title", required=True, max_length=160), F("handle", required=True, pattern=r"^[a-z0-9-]+$"),
        F("description", "text"), F("image_url", "url"), F("kind", enum=["manual", "automatic"], default="manual"),
        F("rules", "json", description='[{"field": "tags", "op": "contains", "value": "summer"}]'),
        F("is_published", "boolean", default=False), F("position", "integer", default=0),
        F("seo_title", max_length=70), F("seo_description", max_length=160)],
        ops(list_="published_read", get="published_read", create=EDITOR_NEW, update=EDITOR, delete=MANAGER),
        relations=[HM("products", "collection_products", "collection_id")], tags=["Catalog"], cache_ttl=60),
    resource("collection_products", "Products in manual collections.", [
        S, F("collection_id", "integer", required=True), F("product_id", "integer", required=True), F("position", "integer", default=0)],
        ops(list_="public", get="public", create=EDITOR_NEW, update=EDITOR, delete=EDITOR),
        relations=[BT("collection", "collections", "collection_id"), BT("product", "products", "product_id")], tags=["Catalog"]),
    resource("inventory_movements", "Every stock change, with its reason. Written by flows.", [
        S, F("variant_id", "integer", required=True), F("delta", "integer", required=True), F("balance_after", "integer"),
        F("reason", enum=["received", "correction", "damaged", "lost", "returned", "count", "sale", "cancel", "refund_restock"], required=True),
        F("note", max_length=240), F("reference_type", max_length=40), F("reference_id", max_length=64), F("actor_id")],
        ops(list_=STAFF, get=STAFF, create=False, update=False, delete=False),
        relations=[BT("variant", "product_variants", "variant_id")], tags=["Inventory"]),

    # Customers
    resource("customers", "A shopper's profile per store.", [
        S, F("user_id", description="The shopper's account, when signed in"), F("email", "email", required=True),
        F("first_name", max_length=80), F("last_name", max_length=80), F("phone", max_length=32),
        F("accepts_marketing", "boolean", default=False), F("tags", "array", items={"type": "string"}, default=[]), F("note", "text"),
        F("orders_count", "integer", default=0), money("total_spent_minor", default=0),
        F("first_order_at", "datetime"), F("last_order_at", "datetime"), F("risk_score", "integer", default=0)],
        ops(list_="store_or_owner", get="store_or_owner", create=EDITOR_NEW, update=EDITOR, delete=MANAGER),
        relations=[HM("orders", "orders", "customer_id"), HM("addresses", "customer_addresses", "customer_id")], tags=["Customers"]),
    resource("customer_addresses", "Saved addresses.", [
        S, F("customer_id", "integer", required=True), F("user_id"), F("label", max_length=40),
        F("address", "ref", schema="Address", required=True), F("is_default", "boolean", default=False)],
        ops(list_="store_or_owner", get="store_or_owner", create="shopper", update="store_or_owner", delete="store_or_owner"),
        owner="user_id", relations=[BT("customer", "customers", "customer_id")], tags=["Customers"]),
    resource("segments", "Saved customer groups for campaigns and discounts.", [
        S, F("name", required=True, max_length=80), F("description", max_length=240),
        F("rules", "json", description='{"all": [{"field": "total_spent_minor", "op": "gte", "value": 5000000}]}'),
        F("is_system", "boolean", default=False), F("cached_count", "integer", default=0), F("counted_at", "datetime")],
        ops(list_=STAFF, get=STAFF, create=EDITOR_NEW, update=EDITOR, delete=MANAGER), tags=["Marketing"]),
    resource("wishlists", "Shoppers' saved products.", [
        S, F("product_id", "integer", required=True), F("variant_id", "integer"), F("user_id", read_only=True)],
        ops(list_={"owner": "user_id"}, get={"owner": "user_id"}, create="shopper", update=False, delete={"owner": "user_id"}),
        owner="user_id", relations=[BT("product", "products", "product_id")], tags=["Customers"]),
    resource("reviews", "Product reviews, moderated by the store.", [
        S, F("product_id", "integer", required=True), F("user_id", read_only=True), F("author_name", max_length=80),
        F("rating", "integer", required=True, minimum=1, maximum=5), F("title", max_length=120), F("body", "text", max_length=4000),
        F("status", enum=["pending", "approved", "rejected"], default="pending"), F("verified_purchase", "boolean", default=False),
        F("reply", "text", max_length=2000)],
        ops(list_="approved_reviews", get="approved_reviews", create=False, update=EDITOR, delete=MANAGER),
        relations=[BT("product", "products", "product_id")], tags=["Customers"]),

    # Carts and orders
    resource("carts", "Shopping carts, addressed by an unguessable token.", [
        S, F("token", required=True, max_length=64), F("user_id"), F("customer_id", "integer"), F("email", "email"),
        F("currency", enum=CURRENCIES), F("status", enum=["open", "abandoned", "converted", "expired"], default="open"),
        F("discount_id", "integer"), F("discount_code", max_length=40), money("discount_minor", default=0),
        money("subtotal_minor", default=0), F("item_count", "integer", default=0), F("last_activity_at", "datetime"), F("converted_at", "datetime")],
        ops(list_=STAFF, get=STAFF, create=False, update=False, delete=MANAGER),
        relations=[HM("items", "cart_items", "cart_id")], tags=["Checkout"]),
    resource("cart_items", "Lines in a cart.", [
        S, F("cart_id", "integer", required=True), F("variant_id", "integer", required=True), F("product_id", "integer", required=True),
        F("quantity", "integer", required=True, minimum=1), money("unit_price_minor", required=True)],
        ops(list_=STAFF, get=STAFF, create=False, update=False, delete=False),
        relations=[BT("variant", "product_variants", "variant_id"), BT("product", "products", "product_id")], tags=["Checkout"]),
    resource("orders", "Orders. Created by checkout, the POS or staff; moved through payment and fulfilment by flows.", [
        S, F("number", "integer", required=True), F("user_id"), F("customer_id", "integer"), F("email", "email", required=True),
        F("phone", max_length=32), F("currency", enum=CURRENCIES, required=True),
        money("subtotal_minor", default=0), money("discount_minor", default=0), money("shipping_minor", default=0),
        money("tax_minor", default=0), money("total_minor", default=0), money("refunded_minor", default=0),
        F("status", enum=["open", "cancelled", "closed"], default="open"),
        F("payment_status", enum=["pending", "paid", "partially_refunded", "refunded", "voided"], default="pending"),
        F("fulfilment_status", enum=["unfulfilled", "partial", "fulfilled", "returned"], default="unfulfilled"),
        F("discount_id", "integer"), F("discount_code", max_length=40), F("shipping_method", max_length=80),
        F("carrier", max_length=60), F("tracking_number", max_length=80), F("tracking_url", "url"),
        F("shipping_address", "ref", schema="Address", nullable=True), F("billing_address", "ref", schema="Address", nullable=True),
        F("note", "text"), F("tags", "array", items={"type": "string"}, default=[]),
        F("source", enum=["web", "pos", "api", "draft"], default="web"), F("campaign_id", "integer"), F("cart_id", "integer"),
        F("payment_reference", max_length=64), F("cancel_reason", max_length=40),
        F("risk_score", "integer", default=0), F("risk_flags", "json"), F("client_ip", max_length=64),
        F("placed_at", "datetime"), F("paid_at", "datetime"), F("fulfilled_at", "datetime"), F("cancelled_at", "datetime")],
        ops(list_="store_or_owner", get="store_or_owner", create=False, update=MANAGER, delete=False),
        relations=[HM("items", "order_items", "order_id"), HM("events", "order_events", "order_id"),
                   HM("payments", "payments", "order_id"), HM("refunds", "refunds", "order_id"), BT("customer", "customers", "customer_id")],
        tags=["Orders"], transformer="customer_order", realtime=False),
    resource("order_items", "Lines of an order, with prices and titles frozen at purchase.", [
        S, F("order_id", "integer", required=True), F("user_id"), F("variant_id", "integer"), F("product_id", "integer"),
        F("title", required=True, max_length=200), F("variant_title", max_length=120), F("sku", max_length=64), F("image_url", "url"),
        F("quantity", "integer", required=True, minimum=1), money("unit_price_minor", required=True), money("total_minor", required=True),
        money("cost_minor"), F("quantity_fulfilled", "integer", default=0), F("quantity_refunded", "integer", default=0)],
        ops(list_="store_or_owner", get="store_or_owner", create=False, update=False, delete=False),
        relations=[BT("order", "orders", "order_id"), BT("variant", "product_variants", "variant_id")], tags=["Orders"]),
    resource("order_events", "The order timeline.", [
        S, F("order_id", "integer", required=True), F("user_id"),
        F("kind", required=True, enum=["placed", "paid", "fulfilled", "cancelled", "refunded", "note", "email_sent", "payment_failed"]),
        F("message", required=True, max_length=300), F("data", "json"), F("actor_id"), F("is_customer_visible", "boolean", default=True)],
        ops(list_="store_or_owner", get="store_or_owner", create=False, update=False, delete=False),
        relations=[BT("order", "orders", "order_id")], tags=["Orders"]),
    resource("abandoned_carts", "Carts left behind, with recovery status.", [
        S, F("cart_id", "integer", required=True), F("email", "email"), money("value_minor", default=0), F("currency", enum=CURRENCIES),
        F("item_count", "integer", default=0), F("recovery_status", enum=["pending", "emailed", "recovered", "lost"], default="pending"),
        F("recovery_token", max_length=64), F("notified_at", "datetime"), F("recovered_at", "datetime"), F("recovered_order_id", "integer")],
        ops(list_=STAFF, get=STAFF, create=False, update=EDITOR, delete=MANAGER), tags=["Marketing"]),

    # Money
    resource("payments", "Money received for orders. Written by flows only.", [
        S, F("order_id", "integer", required=True), F("provider", enum=["gateway", "cash", "bank_transfer", "pos_card", "mobile_money"], required=True),
        F("reference", required=True, max_length=64), F("provider_reference", max_length=120), F("currency", enum=CURRENCIES),
        money("amount_minor", required=True), money("provider_fee_minor", default=0), money("platform_fee_minor", default=0),
        money("refunded_minor", default=0), F("status", enum=["pending", "captured", "failed", "refunded", "partially_refunded"], default="pending"),
        F("method", max_length=40), F("card_brand", max_length=20), F("card_last4", max_length=4), F("failure_message", max_length=240),
        F("captured_at", "datetime")],
        ops(list_="store_or_support", get="store_or_support", create=False, update=False, delete=False),
        relations=[BT("order", "orders", "order_id")], tags=["Finance"]),
    resource("refunds", "Refunds. Created by the refund flow.", [
        S, F("order_id", "integer", required=True), F("payment_id", "integer"), F("reference", required=True, max_length=64),
        money("amount_minor", required=True), F("currency", enum=CURRENCIES), F("reason", max_length=40), F("note", "text"),
        F("restock", "boolean", default=False), F("status", enum=["pending", "succeeded", "failed"], default="succeeded"), F("actor_id")],
        ops(list_="store_or_support", get="store_or_support", create=False, update=False, delete=False),
        relations=[BT("order", "orders", "order_id")], tags=["Finance"]),
    resource("ledger_entries", "Double-entry style money movements: sales, fees, refunds, payouts.", [
        S, F("kind", required=True, enum=["sale", "provider_fee", "platform_fee", "refund", "payout", "adjustment"]),
        F("amount_minor", "integer", required=True, description="Positive credits the store, negative debits it"),
        F("currency", enum=CURRENCIES), F("order_id", "integer"), F("payment_id", "integer"), F("refund_id", "integer"),
        F("payout_id", "integer"), F("description", max_length=240), F("occurred_at", "datetime")],
        ops(list_=STAFF, get=STAFF, create=False, update=False, delete=False), tags=["Finance"]),
    resource("payouts", "Transfers of the store's balance to its bank account.", [
        S, money("amount_minor", required=True), F("currency", enum=CURRENCIES), F("status", enum=["pending", "in_transit", "paid", "failed"], default="pending"),
        F("destination", max_length=120), F("period_start", "date"), F("period_end", "date"), F("paid_at", "datetime"), F("failure_message", max_length=240)],
        ops(list_=STAFF, get=STAFF, create=False, update="platform_admin", delete=False), tags=["Finance"]),

    # Marketing
    resource("discounts", "Codes and automatic discounts.", [
        S, F("code", required=True, pattern=r"^[A-Z0-9_-]{3,40}$"), F("title", max_length=120), F("description", max_length=240),
        F("kind", required=True, enum=["percentage", "fixed_amount", "free_shipping"]),
        F("value", "integer", required=True, minimum=0, description="Basis points for percentage (1500 = 15%), minor units for fixed"),
        money("minimum_order_minor"), money("maximum_discount_minor"), F("usage_limit", "integer", minimum=1), F("per_customer_limit", "integer", minimum=1),
        F("usage_count", "integer", default=0, read_only=True), F("starts_at", "datetime"), F("ends_at", "datetime"),
        F("is_active", "boolean", default=True), F("segment_id", "integer")],
        ops(list_=STAFF, get=STAFF, create=EDITOR_NEW, update=EDITOR, delete=MANAGER), tags=["Marketing"]),
    resource("discount_usages", "Who used which discount on which order.", [
        S, F("discount_id", "integer", required=True), F("order_id", "integer", required=True), F("customer_id", "integer"), money("amount_minor", required=True)],
        ops(list_=STAFF, get=STAFF, create=False, update=False, delete=False), tags=["Marketing"]),
    resource("campaigns", "Marketing campaigns with attribution.", [
        S, F("name", required=True, max_length=120), F("kind", enum=["email", "sms", "social", "ads", "influencer", "abandoned_cart"], required=True),
        F("description", "text"), F("status", enum=["draft", "scheduled", "active", "paused", "completed"], default="draft"),
        F("segment_id", "integer"), F("discount_id", "integer"), F("starts_at", "datetime"), F("ends_at", "datetime"),
        money("budget_minor"), money("spend_minor", default=0), F("orders_count", "integer", default=0), money("revenue_minor", default=0),
        F("recipients_count", "integer", default=0)],
        ops(list_=STAFF, get=STAFF, create=EDITOR_NEW, update=EDITOR, delete=MANAGER), tags=["Marketing"]),
    resource("gift_cards", "Store credit issued as codes.", [
        S, F("code", required=True, pattern=r"^[A-Z0-9-]{8,32}$"), money("initial_minor", required=True), money("balance_minor", required=True),
        F("currency", enum=CURRENCIES), F("customer_id", "integer"), F("expires_on", "date"), F("is_active", "boolean", default=True), F("note", max_length=240)],
        ops(list_=STAFF, get=STAFF, create=MANAGER_NEW, update=MANAGER, delete=False), tags=["Marketing"]),

    # Shipping and tax
    resource("shipping_zones", "Where the store ships.", [
        S, F("name", required=True, max_length=80), F("countries", "array", items={"type": "string"}, default=[]), F("position", "integer", default=0)],
        ops(list_="public", get="public", create=MANAGER_NEW, update=MANAGER, delete=MANAGER),
        relations=[HM("rates", "shipping_rates", "zone_id")], tags=["Shipping"], cache_ttl=120),
    resource("shipping_rates", "Prices per zone, by weight or order value.", [
        S, F("zone_id", "integer", required=True), F("name", required=True, max_length=80), F("description", max_length=200),
        F("kind", enum=["flat", "weight", "price"], default="flat"), money("price_minor", required=True),
        F("min_weight_grams", "integer"), F("max_weight_grams", "integer"), money("min_subtotal_minor"), money("max_subtotal_minor"),
        F("delivery_estimate", max_length=60), F("is_active", "boolean", default=True), F("position", "integer", default=0)],
        ops(list_="public", get="public", create=MANAGER_NEW, update=MANAGER, delete=MANAGER),
        relations=[BT("zone", "shipping_zones", "zone_id")], tags=["Shipping"], cache_ttl=120),
    resource("tax_rates", "Tax per country, in basis points.", [
        S, F("country", required=True, pattern=r"^[A-Z]{2}$"), F("name", required=True, max_length=60),
        F("rate_bps", "integer", required=True, minimum=0, maximum=5000), F("applies_to_shipping", "boolean", default=False)],
        ops(list_="public", get="public", create=MANAGER_NEW, update=MANAGER, delete=MANAGER), tags=["Shipping"], cache_ttl=300),

    # Point of sale
    resource("pos_devices", "Registered tills.", [
        S, F("label", required=True, max_length=60), F("receipt_header", "text"), F("receipt_footer", "text"),
        F("print_receipt_auto", "boolean", default=True), F("is_active", "boolean", default=True)],
        ops(list_=STAFF, get=STAFF, create=MANAGER_NEW, update=MANAGER, delete=MANAGER), tags=["POS"]),
    resource("pos_sessions", "A till shift from opening float to cash count.", [
        S, F("device_id", "integer", required=True), F("opened_by"), F("closed_by"), F("status", enum=["open", "closed"], default="open"),
        money("opening_float_minor", default=0), money("closing_count_minor"), money("expected_cash_minor"),
        money("total_sales_minor", default=0), F("order_count", "integer", default=0), F("note", "text"), F("opened_at", "datetime"), F("closed_at", "datetime")],
        ops(list_=STAFF, get=STAFF, create=EDITOR_NEW, update=EDITOR, delete=False),
        relations=[BT("device", "pos_devices", "device_id")], tags=["POS"]),

    # Support and operations
    resource("tickets", "Help-desk conversations between shoppers and a store.", [
        S, F("token", required=True, max_length=64), F("user_id"), F("customer_name", max_length=120), F("customer_email", "email", required=True),
        F("subject", required=True, max_length=200), F("order_number", "integer"),
        F("status", enum=["open", "pending", "closed"], default="open"), F("last_message_at", "datetime"), F("closed_at", "datetime")],
        ops(list_="store_or_owner", get="store_or_owner", create=False, update=EDITOR, delete=MANAGER),
        relations=[HM("messages", "ticket_messages", "ticket_id")], tags=["Support"]),
    resource("ticket_messages", "Messages in a ticket.", [
        S, F("ticket_id", "integer", required=True), F("user_id"), F("from_staff", "boolean", default=False),
        F("author_name", max_length=120), F("body", "text", required=True)],
        ops(list_="store_or_owner", get="store_or_owner", create=False, update=False, delete=False),
        relations=[BT("ticket", "tickets", "ticket_id")], tags=["Support"]),
    resource("notifications", "Staff notifications (new order, low stock, new review…).", [
        S, F("kind", required=True, max_length=40), F("title", required=True, max_length=160), F("body", max_length=500),
        F("url", max_length=300), F("level", enum=["info", "success", "warning", "danger"], default="info"), F("read_at", "datetime")],
        ops(list_=STAFF, get=STAFF, create=False, update=STAFF, delete=STAFF), tags=["Operations"], realtime=True),
    resource("daily_stats", "Per-store daily rollups for dashboards.", [
        S, F("day", "date", required=True), F("orders", "integer", default=0), money("gross_minor", default=0), money("discounts_minor", default=0),
        money("refunds_minor", default=0), money("net_minor", default=0), F("items_sold", "integer", default=0),
        F("new_customers", "integer", default=0), money("average_order_minor", default=0)],
        ops(list_=STAFF, get=STAFF, create=False, update=False, delete=False), tags=["Analytics"]),
]


# ── flow building blocks ─────────────────────────────────────────────────────

B = "{{ input.body.%s }}"


def body(field):
    return "{{ input.body.%s }}" % field


def err(node_id, status, code, message):
    return (node_id, "error.raise", {"status": status, "code": code, "message": message})


def own_store(node_id, record_path):
    """A condition node: the loaded record isn't in the caller's active store."""
    return (node_id, "control.if", {"condition": {"ne": [f"$steps.{record_path}.store_id", "$auth.org"]}})


CART_LINES_SQL = """SELECT ci.id, ci.variant_id, ci.product_id, ci.quantity, ci.unit_price_minor,
       ci.quantity * ci.unit_price_minor AS line_total_minor,
       p.title, p.handle, p.image_url, v.title AS variant_title, v.sku, v.cost_minor,
       v.stock, v.reserved, v.track_inventory, v.allow_backorder
FROM cart_items ci
JOIN product_variants v ON v.id = ci.variant_id
JOIN products p ON p.id = ci.product_id
WHERE ci.cart_id = ?
ORDER BY ci.id"""

CART_TOTALS_SQL = """SELECT COALESCE(SUM(quantity * unit_price_minor), 0) AS subtotal, COALESCE(SUM(quantity), 0) AS items
FROM cart_items WHERE cart_id = ?"""

DISCOUNT_AMOUNT_SQL = """SELECT COALESCE((
  SELECT CASE
    WHEN d.minimum_order_minor IS NOT NULL AND CAST(? AS INTEGER) < d.minimum_order_minor THEN 0
    WHEN d.kind = 'percentage' THEN CASE
      WHEN d.maximum_discount_minor IS NOT NULL AND CAST(? AS INTEGER) * d.value / 10000 > d.maximum_discount_minor
        THEN d.maximum_discount_minor
      ELSE CAST(? AS INTEGER) * d.value / 10000 END
    WHEN d.kind = 'fixed_amount' THEN CASE WHEN d.value > CAST(? AS INTEGER) THEN CAST(? AS INTEGER) ELSE d.value END
    ELSE 0 END
  FROM discounts d WHERE d.id = ? AND d.is_active = ?), 0) AS amount,
  COALESCE((SELECT d.kind FROM discounts d WHERE d.id = ?), '') AS kind"""


def recalc_and_reply(prefix, cart_ref):
    """Recompute a cart's subtotal and item count, then answer with the cart and its lines."""
    nodes = [
        (f"{prefix}totals", "db.query", {"sql": CART_TOTALS_SQL, "params": [f"{{{{ {cart_ref}.id }}}}"]}),
        (f"{prefix}now", "time.now", {}),
        (f"{prefix}save", "resource.update", {"resource": "carts", "id": f"{{{{ {cart_ref}.id }}}}", "data": {
            "subtotal_minor": f"{{{{ steps.{prefix}totals.output.0.subtotal | int }}}}",
            "item_count": f"{{{{ steps.{prefix}totals.output.0.items | int }}}}",
            "last_activity_at": f"{{{{ steps.{prefix}now.output }}}}"}}),
        (f"{prefix}lines", "db.query", {"sql": CART_LINES_SQL, "params": [f"{{{{ {cart_ref}.id }}}}"]}),
        (f"{prefix}reply", "response.return", {"body": {"cart": f"{{{{ steps.{prefix}save.output }}}}", "items": f"{{{{ steps.{prefix}lines.output }}}}"}}),
    ]
    edges = [(f"{prefix}totals", f"{prefix}now"), (f"{prefix}now", f"{prefix}save"), (f"{prefix}save", f"{prefix}lines"), (f"{prefix}lines", f"{prefix}reply")]
    return nodes, edges


# ── flows ────────────────────────────────────────────────────────────────────

FLOWS = []

# Cart: add a line ------------------------------------------------------------
tail_n, tail_e = recalc_and_reply("t_", "vars.cart")
FLOWS.append(flow("cart_add", "POST /storefront/cart/items: add a variant to a cart (creating the cart if needed), with stock checks.", [
    ("http", "trigger.http", {}),
    ("variant", "resource.get", {"resource": "product_variants", "id": body("variant_id")}),
    err("no_variant", 404, "variant_not_found", "No such product variant"),
    ("wrong_store", "control.if", {"condition": {"ne": ["$steps.variant.output.store_id", "$input.body.store_id"]}}),
    err("not_here", 404, "variant_not_found", "No such product variant in this store"),
    ("product", "resource.get", {"resource": "products", "id": "{{ steps.variant.output.product_id }}"}),
    ("inactive", "control.if", {"condition": {"ne": ["$steps.product.output.status", "active"]}}),
    err("not_for_sale", 409, "not_for_sale", "This product isn't for sale"),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": body("store_id")}, "limit": 1}),
    ("find", "resource.list", {"resource": "carts", "filters": {"token": "{{ input.body.cart_token }}", "store_id": body("store_id")}, "limit": 1}),
    ("found", "control.if", {"condition": {"truthy": "$steps.find.output.total"}}),
    ("use_found", "control.set", {"values": {"cart": "{{ steps.find.output.data.0 }}"}}),
    ("token", "util.id", {"kind": "token", "length": 32}),
    ("create_cart", "resource.create", {"resource": "carts", "data": {
        "store_id": body("store_id"), "token": "{{ steps.token.output }}", "user_id": "{{ auth.user_id }}",
        "email": body("email"), "currency": "{{ steps.store.output.data.0.currency }}", "status": "open"}}),
    ("use_new", "control.set", {"values": {"cart": "{{ steps.create_cart.output }}"}}),
    ("closed", "control.if", {"condition": {"not": {"in": ["$vars.cart.status", ["open", "abandoned"]]}}}),
    err("cart_closed", 409, "cart_closed", "This cart has already been checked out"),
    ("line", "resource.list", {"resource": "cart_items", "filters": {"cart_id": "{{ vars.cart.id }}", "variant_id": body("variant_id")}, "limit": 1}),
    ("has_line", "control.if", {"condition": {"truthy": "$steps.line.output.total"}}),
    ("sum_qty", "math.calculate", {"operation": "add", "values": ["{{ steps.line.output.data.0.quantity }}", body("quantity")], "digits": 0}),
    ("qty_existing", "control.set", {"values": {"qty": "{{ steps.sum_qty.output | int }}"}}),
    ("qty_new", "control.set", {"values": {"qty": "{{ input.body.quantity | int }}"}}),
    ("available", "math.calculate", {"operation": "subtract", "values": ["{{ steps.variant.output.stock }}", "{{ steps.variant.output.reserved }}"], "digits": 0}),
    ("short", "control.if", {"condition": {"all": [
        {"truthy": "$steps.variant.output.track_inventory"}, {"not": {"truthy": "$steps.variant.output.allow_backorder"}},
        {"gt": ["$vars.qty", "$steps.available.output"]}]}}),
    err("no_stock", 409, "insufficient_stock", "Not enough stock for that quantity"),
    ("upsert", "control.if", {"condition": {"truthy": "$steps.line.output.total"}}),
    ("update_line", "resource.update", {"resource": "cart_items", "id": "{{ steps.line.output.data.0.id }}", "data": {"quantity": "{{ vars.qty }}", "unit_price_minor": "{{ steps.variant.output.price_minor }}"}}),
    ("add_line", "resource.create", {"resource": "cart_items", "data": {
        "store_id": body("store_id"), "cart_id": "{{ vars.cart.id }}", "variant_id": body("variant_id"),
        "product_id": "{{ steps.variant.output.product_id }}", "quantity": "{{ vars.qty }}", "unit_price_minor": "{{ steps.variant.output.price_minor }}"}}),
    ("set_email", "control.if", {"condition": {"exists": "$input.body.email"}}),
    ("save_email", "resource.update", {"resource": "carts", "id": "{{ vars.cart.id }}", "data": {"email": body("email")}}),
    *tail_n,
], [
    ("http", "variant"), ("variant", "no_variant", "missing"), ("variant", "wrong_store"), ("wrong_store", "not_here", "true"),
    ("wrong_store", "product", "false"), ("product", "inactive"), ("inactive", "not_for_sale", "true"), ("inactive", "store", "false"),
    ("store", "find"), ("find", "found"), ("found", "use_found", "true"), ("found", "token", "false"), ("token", "create_cart"),
    ("create_cart", "use_new"), ("use_found", "closed"), ("use_new", "closed"), ("closed", "cart_closed", "true"),
    ("closed", "line", "false"), ("line", "has_line"), ("has_line", "sum_qty", "true"), ("sum_qty", "qty_existing"),
    ("has_line", "qty_new", "false"), ("qty_existing", "available"), ("qty_new", "available"), ("available", "short"),
    ("short", "no_stock", "true"), ("short", "upsert", "false"), ("upsert", "update_line", "true"), ("upsert", "add_line", "false"),
    ("update_line", "set_email"), ("add_line", "set_email"), ("set_email", "save_email", "true"), ("set_email", "t_totals", "false"),
    ("save_email", "t_totals"), *tail_e,
]))

# Cart: change or remove a line -------------------------------------------------
tail_n, tail_e = recalc_and_reply("t_", "steps.cart.output.data.0")
FLOWS.append(flow("cart_update", "POST /storefront/cart/update: change a line's quantity; 0 removes it.", [
    ("http", "trigger.http", {}),
    ("cart", "resource.list", {"resource": "carts", "filters": {"token": body("cart_token")}, "limit": 1}),
    ("no_cart", "control.if", {"condition": {"not": {"truthy": "$steps.cart.output.total"}}}),
    err("missing_cart", 404, "cart_not_found", "No such cart"),
    ("line", "resource.get", {"resource": "cart_items", "id": body("item_id")}),
    err("missing_line", 404, "line_not_found", "No such cart line"),
    ("foreign", "control.if", {"condition": {"ne": ["$steps.line.output.cart_id", "$steps.cart.output.data.0.id"]}}),
    err("not_in_cart", 404, "line_not_found", "No such cart line"),
    ("remove", "control.if", {"condition": {"eq": ["$input.body.quantity", 0]}}),
    ("delete_line", "resource.delete", {"resource": "cart_items", "id": body("item_id")}),
    ("variant", "resource.get", {"resource": "product_variants", "id": "{{ steps.line.output.variant_id }}"}),
    ("available", "math.calculate", {"operation": "subtract", "values": ["{{ steps.variant.output.stock }}", "{{ steps.variant.output.reserved }}"], "digits": 0}),
    ("short", "control.if", {"condition": {"all": [
        {"truthy": "$steps.variant.output.track_inventory"}, {"not": {"truthy": "$steps.variant.output.allow_backorder"}},
        {"gt": ["$input.body.quantity", "$steps.available.output"]}]}}),
    err("no_stock", 409, "insufficient_stock", "Not enough stock for that quantity"),
    ("set_qty", "resource.update", {"resource": "cart_items", "id": body("item_id"), "data": {"quantity": body("quantity")}}),
    *tail_n,
], [
    ("http", "cart"), ("cart", "no_cart"), ("no_cart", "missing_cart", "true"), ("no_cart", "line", "false"),
    ("line", "missing_line", "missing"), ("line", "foreign"), ("foreign", "not_in_cart", "true"), ("foreign", "remove", "false"),
    ("remove", "delete_line", "true"), ("remove", "variant", "false"), ("variant", "available"), ("available", "short"),
    ("short", "no_stock", "true"), ("short", "set_qty", "false"), ("delete_line", "t_totals"), ("set_qty", "t_totals"), *tail_e,
]))

# Cart: view ------------------------------------------------------------------
FLOWS.append(flow("cart_view", "GET /storefront/cart/{token}: a cart with its lines and product details.", [
    ("http", "trigger.http", {}),
    ("cart", "resource.list", {"resource": "carts", "filters": {"token": "{{ input.params.token }}"}, "limit": 1}),
    ("no_cart", "control.if", {"condition": {"not": {"truthy": "$steps.cart.output.total"}}}),
    err("missing", 404, "cart_not_found", "No such cart"),
    ("lines", "db.query", {"sql": CART_LINES_SQL, "params": ["{{ steps.cart.output.data.0.id }}"]}),
    ("reply", "response.return", {"body": {"cart": "{{ steps.cart.output.data.0 }}", "items": "{{ steps.lines.output }}"}}),
], [("http", "cart"), ("cart", "no_cart"), ("no_cart", "missing", "true"), ("no_cart", "lines", "false"), ("lines", "reply")]))

# Cart: discount codes --------------------------------------------------------
FLOWS.append(flow("apply_discount", "POST /storefront/cart/discount: validate a code (active, dates, usage, minimum) and price it on the cart.", [
    ("http", "trigger.http", {}),
    ("cart", "resource.list", {"resource": "carts", "filters": {"token": body("cart_token")}, "limit": 1}),
    ("no_cart", "control.if", {"condition": {"not": {"truthy": "$steps.cart.output.total"}}}),
    err("missing_cart", 404, "cart_not_found", "No such cart"),
    ("code", "resource.list", {"resource": "discounts", "filters": {"store_id": "{{ steps.cart.output.data.0.store_id }}", "code": "{{ input.body.code | upper }}"}, "limit": 1}),
    ("unknown", "control.if", {"condition": {"not": {"truthy": "$steps.code.output.total"}}}),
    err("invalid", 422, "invalid_code", "That code isn't valid"),
    ("now", "time.now", {}),
    ("inactive", "control.if", {"condition": {"any": [
        {"eq": ["$steps.code.output.data.0.is_active", False]},
        {"all": [{"exists": "$steps.code.output.data.0.starts_at"}, {"gt": ["$steps.code.output.data.0.starts_at", "$steps.now.output"]}]},
        {"all": [{"exists": "$steps.code.output.data.0.ends_at"}, {"lt": ["$steps.code.output.data.0.ends_at", "$steps.now.output"]}]}]}}),
    err("not_active", 422, "code_not_active", "That code isn't active right now"),
    ("used_up", "control.if", {"condition": {"all": [{"exists": "$steps.code.output.data.0.usage_limit"},
        {"gte": ["$steps.code.output.data.0.usage_count", "$steps.code.output.data.0.usage_limit"]}]}}),
    err("exhausted", 422, "code_used_up", "That code has been fully used"),
    ("too_small", "control.if", {"condition": {"all": [{"exists": "$steps.code.output.data.0.minimum_order_minor"},
        {"lt": ["$steps.cart.output.data.0.subtotal_minor", "$steps.code.output.data.0.minimum_order_minor"]}]}}),
    err("minimum", 422, "minimum_not_met", "Your cart doesn't reach this code's minimum order"),
    ("price", "db.query", {"sql": DISCOUNT_AMOUNT_SQL, "params": [
        "{{ steps.cart.output.data.0.subtotal_minor | int }}", "{{ steps.cart.output.data.0.subtotal_minor | int }}",
        "{{ steps.cart.output.data.0.subtotal_minor | int }}", "{{ steps.cart.output.data.0.subtotal_minor | int }}",
        "{{ steps.cart.output.data.0.subtotal_minor | int }}", "{{ steps.code.output.data.0.id }}", True, "{{ steps.code.output.data.0.id }}"]}),
    ("save", "resource.update", {"resource": "carts", "id": "{{ steps.cart.output.data.0.id }}", "data": {
        "discount_id": "{{ steps.code.output.data.0.id }}", "discount_code": "{{ steps.code.output.data.0.code }}",
        "discount_minor": "{{ steps.price.output.0.amount | int }}", "last_activity_at": "{{ steps.now.output }}"}}),
    ("reply", "response.return", {"body": {"cart": "{{ steps.save.output }}", "discount": {
        "code": "{{ steps.code.output.data.0.code }}", "kind": "{{ steps.code.output.data.0.kind }}", "amount_minor": "{{ steps.price.output.0.amount | int }}"}}}),
], [
    ("http", "cart"), ("cart", "no_cart"), ("no_cart", "missing_cart", "true"), ("no_cart", "code", "false"), ("code", "unknown"),
    ("unknown", "invalid", "true"), ("unknown", "now", "false"), ("now", "inactive"), ("inactive", "not_active", "true"),
    ("inactive", "used_up", "false"), ("used_up", "exhausted", "true"), ("used_up", "too_small", "false"),
    ("too_small", "minimum", "true"), ("too_small", "price", "false"), ("price", "save"), ("save", "reply"),
]))

FLOWS.append(flow("remove_discount", "DELETE /storefront/cart/{token}/discount: take a code off a cart.", [
    ("http", "trigger.http", {}),
    ("cart", "resource.list", {"resource": "carts", "filters": {"token": "{{ input.params.token }}"}, "limit": 1}),
    ("no_cart", "control.if", {"condition": {"not": {"truthy": "$steps.cart.output.total"}}}),
    err("missing", 404, "cart_not_found", "No such cart"),
    ("clear", "resource.update", {"resource": "carts", "id": "{{ steps.cart.output.data.0.id }}", "data": {"discount_id": None, "discount_code": None, "discount_minor": 0}}),
    ("reply", "response.return", {"body": "{{ steps.clear.output }}"}),
], [("http", "cart"), ("cart", "no_cart"), ("no_cart", "missing", "true"), ("no_cart", "clear", "false"), ("clear", "reply")]))

# Checkout --------------------------------------------------------------------
SHIPPING_SQL = """SELECT r.id, r.name, r.price_minor, r.delivery_estimate
FROM shipping_rates r JOIN shipping_zones z ON z.id = r.zone_id
WHERE r.store_id = ? AND r.is_active = ? AND CAST(z.countries AS TEXT) LIKE ?
  AND (r.min_subtotal_minor IS NULL OR r.min_subtotal_minor <= CAST(? AS INTEGER))
  AND (r.max_subtotal_minor IS NULL OR r.max_subtotal_minor >= CAST(? AS INTEGER))
  AND (r.min_weight_grams IS NULL OR r.min_weight_grams <= CAST(? AS INTEGER))
  AND (r.max_weight_grams IS NULL OR r.max_weight_grams >= CAST(? AS INTEGER))
ORDER BY r.price_minor, r.position
LIMIT 1"""

TOTALS_SQL = """SELECT t.subtotal, t.discount, t.shipping, t.tax, t.subtotal - t.discount + t.shipping + t.tax AS total
FROM (SELECT CAST(? AS INTEGER) AS subtotal, CAST(? AS INTEGER) AS discount,
             CASE WHEN ? = 'free_shipping' THEN 0 ELSE CAST(? AS INTEGER) END AS shipping,
             (CAST(? AS INTEGER) - CAST(? AS INTEGER)) * CAST(? AS INTEGER) / 10000 AS tax) t"""

C = "steps.cart.output.data.0"
FLOWS.append(flow("checkout", "POST /storefront/checkout: price the cart (discount, shipping by zone and weight, tax), create the order, reserve stock, open a payment.", [
    ("http", "trigger.http", {}),
    ("cart", "resource.list", {"resource": "carts", "filters": {"token": body("cart_token")}, "limit": 1}),
    ("no_cart", "control.if", {"condition": {"not": {"truthy": f"${C}.id"}}}),
    err("missing_cart", 404, "cart_not_found", "No such cart"),
    ("closed", "control.if", {"condition": {"not": {"in": [f"${C}.status", ["open", "abandoned"]]}}}),
    err("already", 409, "cart_closed", "This cart has already been checked out"),
    ("lines", "db.query", {"sql": CART_LINES_SQL, "params": [f"{{{{ {C}.id }}}}"]}),
    ("empty", "control.if", {"condition": {"empty": "$steps.lines.output"}}),
    err("no_items", 422, "empty_cart", "The cart is empty"),
    ("short", "db.query", {"sql": """SELECT COUNT(*) AS n FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id
WHERE ci.cart_id = ? AND v.track_inventory = ? AND v.allow_backorder = ? AND v.stock - v.reserved < ci.quantity""",
        "params": [f"{{{{ {C}.id }}}}", True, False]}),
    ("out", "control.if", {"condition": {"gt": ["$steps.short.output.0.n", 0]}}),
    err("no_stock", 409, "insufficient_stock", "Some items are no longer in stock"),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": f"{{{{ {C}.store_id }}}}"}, "limit": 1}),
    ("sums", "db.query", {"sql": """SELECT COALESCE(SUM(ci.quantity * ci.unit_price_minor), 0) AS subtotal,
       COALESCE(SUM(ci.quantity * COALESCE(v.weight_grams, 0)), 0) AS weight
FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id WHERE ci.cart_id = ?""", "params": [f"{{{{ {C}.id }}}}"]}),
    ("discount", "db.query", {"sql": DISCOUNT_AMOUNT_SQL, "params": [
        "{{ steps.sums.output.0.subtotal | int }}", "{{ steps.sums.output.0.subtotal | int }}", "{{ steps.sums.output.0.subtotal | int }}",
        "{{ steps.sums.output.0.subtotal | int }}", "{{ steps.sums.output.0.subtotal | int }}", f"{{{{ {C}.discount_id | default:0 }}}}", True,
        f"{{{{ {C}.discount_id | default:0 }}}}"]}),
    ("rate", "db.query", {"sql": SHIPPING_SQL, "params": [
        f"{{{{ {C}.store_id }}}}", True, '%"{{ input.body.shipping_address.country }}"%',
        "{{ steps.sums.output.0.subtotal | int }}", "{{ steps.sums.output.0.subtotal | int }}",
        "{{ steps.sums.output.0.weight | int }}", "{{ steps.sums.output.0.weight | int }}"]}),
    ("no_rate", "control.if", {"condition": {"empty": "$steps.rate.output"}}),
    err("cant_ship", 422, "no_shipping", "This store doesn't ship to that country"),
    ("tax", "db.query", {"sql": "SELECT COALESCE(MAX(rate_bps), 0) AS bps FROM tax_rates WHERE store_id = ? AND country = ?",
        "params": [f"{{{{ {C}.store_id }}}}", "{{ input.body.shipping_address.country }}"]}),
    ("totals", "db.query", {"sql": TOTALS_SQL, "params": [
        "{{ steps.sums.output.0.subtotal | int }}", "{{ steps.discount.output.0.amount | int }}", "{{ steps.discount.output.0.kind }}",
        "{{ steps.rate.output.0.price_minor | int }}", "{{ steps.sums.output.0.subtotal | int }}", "{{ steps.discount.output.0.amount | int }}",
        "{{ steps.tax.output.0.bps | int }}"]}),
    ("number", "db.query", {"sql": "SELECT COALESCE(MAX(number), 1000) + 1 AS n FROM orders WHERE store_id = ?", "params": [f"{{{{ {C}.store_id }}}}"]}),
    ("find_customer", "resource.list", {"resource": "customers", "filters": {"store_id": f"{{{{ {C}.store_id }}}}", "email": "{{ input.body.email | lower }}"}, "limit": 1}),
    ("known", "control.if", {"condition": {"truthy": "$steps.find_customer.output.total"}}),
    ("use_customer", "control.set", {"values": {"customer": "{{ steps.find_customer.output.data.0 }}"}}),
    ("new_customer", "resource.create", {"resource": "customers", "data": {
        "store_id": f"{{{{ {C}.store_id }}}}", "email": "{{ input.body.email | lower }}", "user_id": "{{ auth.user_id }}",
        "first_name": "{{ input.body.shipping_address.first_name }}", "last_name": "{{ input.body.shipping_address.last_name }}",
        "phone": body("phone")}}),
    ("use_new", "control.set", {"values": {"customer": "{{ steps.new_customer.output }}"}}),
    ("now", "time.now", {}),
    ("ref", "util.id", {"kind": "token", "length": 20}),
    ("order", "resource.create", {"resource": "orders", "data": {
        "store_id": f"{{{{ {C}.store_id }}}}", "number": "{{ steps.number.output.0.n | int }}", "user_id": "{{ auth.user_id }}",
        "customer_id": "{{ vars.customer.id }}", "email": "{{ input.body.email | lower }}", "phone": body("phone"),
        "currency": "{{ steps.store.output.data.0.currency }}",
        "subtotal_minor": "{{ steps.totals.output.0.subtotal | int }}", "discount_minor": "{{ steps.totals.output.0.discount | int }}",
        "shipping_minor": "{{ steps.totals.output.0.shipping | int }}", "tax_minor": "{{ steps.totals.output.0.tax | int }}",
        "total_minor": "{{ steps.totals.output.0.total | int }}", "status": "open", "payment_status": "pending",
        "fulfilment_status": "unfulfilled", "discount_id": f"{{{{ {C}.discount_id }}}}", "discount_code": f"{{{{ {C}.discount_code }}}}",
        "shipping_method": "{{ steps.rate.output.0.name }}", "shipping_address": body("shipping_address"),
        "billing_address": "{{ input.body.billing_address }}", "note": body("note"), "source": "web",
        "campaign_id": body("campaign_id"), "cart_id": f"{{{{ {C}.id }}}}", "payment_reference": "PAY-{{ steps.ref.output }}",
        "placed_at": "{{ steps.now.output }}"}}),
    ("each", "control.foreach", {"items": "{{ steps.lines.output }}"}),
    ("item", "resource.create", {"resource": "order_items", "data": {
        "store_id": f"{{{{ {C}.store_id }}}}", "order_id": "{{ steps.order.output.id }}", "user_id": "{{ auth.user_id }}",
        "variant_id": "{{ item.variant_id }}", "product_id": "{{ item.product_id }}", "title": "{{ item.title }}",
        "variant_title": "{{ item.variant_title }}", "sku": "{{ item.sku }}", "image_url": "{{ item.image_url }}",
        "quantity": "{{ item.quantity }}", "unit_price_minor": "{{ item.unit_price_minor }}", "total_minor": "{{ item.line_total_minor | int }}",
        "cost_minor": "{{ item.cost_minor }}"}}),
    ("tracked", "control.if", {"condition": {"truthy": "$item.track_inventory"}}),
    ("reserve_calc", "math.calculate", {"operation": "add", "values": ["{{ item.reserved }}", "{{ item.quantity }}"], "digits": 0}),
    ("reserve", "resource.update", {"resource": "product_variants", "id": "{{ item.variant_id }}", "data": {"reserved": "{{ steps.reserve_calc.output | int }}"}}),
    ("placed", "resource.create", {"resource": "order_events", "data": {
        "store_id": f"{{{{ {C}.store_id }}}}", "order_id": "{{ steps.order.output.id }}", "user_id": "{{ auth.user_id }}",
        "kind": "placed", "message": "Order placed", "is_customer_visible": True}}),
    ("payment", "resource.create", {"resource": "payments", "data": {
        "store_id": f"{{{{ {C}.store_id }}}}", "order_id": "{{ steps.order.output.id }}", "provider": "gateway",
        "reference": "PAY-{{ steps.ref.output }}", "currency": "{{ steps.store.output.data.0.currency }}",
        "amount_minor": "{{ steps.totals.output.0.total | int }}", "status": "pending"}}),
    ("convert", "resource.update", {"resource": "carts", "id": f"{{{{ {C}.id }}}}", "data": {"status": "converted", "converted_at": "{{ steps.now.output }}", "customer_id": "{{ vars.customer.id }}"}}),
    ("announce", "event.emit", {"event": "order.placed", "payload": {
        "order_id": "{{ steps.order.output.id }}", "store_id": f"{{{{ {C}.store_id }}}}", "number": "{{ steps.order.output.number }}",
        "cart_id": f"{{{{ {C}.id }}}}", "total_minor": "{{ steps.order.output.total_minor }}", "email": "{{ steps.order.output.email }}"}}),
    ("reply", "response.return", {"status": 201, "body": {
        "order": "{{ steps.order.output }}",
        "payment": {"reference": "PAY-{{ steps.ref.output }}", "amount_minor": "{{ steps.order.output.total_minor }}",
                    "currency": "{{ steps.order.output.currency }}", "status": "pending"},
        "shipping": "{{ steps.rate.output.0 }}"}}),
], [
    ("http", "cart"), ("cart", "no_cart"), ("no_cart", "missing_cart", "true"), ("no_cart", "closed", "false"),
    ("closed", "already", "true"), ("closed", "lines", "false"), ("lines", "empty"), ("empty", "no_items", "true"),
    ("empty", "short", "false"), ("short", "out"), ("out", "no_stock", "true"), ("out", "store", "false"), ("store", "sums"),
    ("sums", "discount"), ("discount", "rate"), ("rate", "no_rate"), ("no_rate", "cant_ship", "true"), ("no_rate", "tax", "false"),
    ("tax", "totals"), ("totals", "number"), ("number", "find_customer"), ("find_customer", "known"),
    ("known", "use_customer", "true"), ("known", "new_customer", "false"), ("new_customer", "use_new"),
    ("use_customer", "now"), ("use_new", "now"), ("now", "ref"), ("ref", "order"), ("order", "each"),
    ("each", "item", "each"), ("item", "tracked"), ("tracked", "reserve_calc", "true"), ("reserve_calc", "reserve"),
    ("each", "placed", "done"), ("placed", "payment"), ("payment", "convert"), ("convert", "announce"), ("announce", "reply"),
], timeout=120))

# Payments ----------------------------------------------------------------------
P = "input.event.payload"
FLOWS.append(flow("payment_gateway", "Inbound hook: a payment provider confirms a charge (charge.success) and the payment is captured once.", [
    ("hook", "trigger.webhook", {"hook": "payment-gateway"}),
    ("success", "control.if", {"condition": {"eq": ["$input.body.event", "charge.success"]}}),
    ("find", "resource.list", {"resource": "payments", "filters": {"reference": "{{ input.body.data.reference }}"}, "limit": 1}),
    ("known", "control.if", {"condition": {"truthy": "$steps.find.output.total"}}),
    ("unknown", "log.write", {"level": "warning", "message": "gateway charge for an unknown payment", "data": "{{ input.body.data }}"}),
    ("done_before", "control.if", {"condition": {"eq": ["$steps.find.output.data.0.status", "captured"]}}),
    ("duplicate", "log.write", {"level": "info", "message": "duplicate gateway notification ignored", "data": {"reference": "{{ input.body.data.reference }}"}}),
    ("short", "control.if", {"condition": {"lt": ["$input.body.data.amount", "$steps.find.output.data.0.amount_minor"]}}),
    ("fail", "resource.update", {"resource": "payments", "id": "{{ steps.find.output.data.0.id }}", "data": {"status": "failed", "failure_message": "Amount paid is less than the order total"}}),
    ("fail_event", "resource.create", {"resource": "order_events", "data": {"store_id": "{{ steps.find.output.data.0.store_id }}",
        "order_id": "{{ steps.find.output.data.0.order_id }}", "kind": "payment_failed", "message": "Payment amount was short", "is_customer_visible": False}}),
    ("details", "resource.update", {"resource": "payments", "id": "{{ steps.find.output.data.0.id }}", "data": {
        "provider_reference": "{{ input.body.data.id }}", "method": "{{ input.body.data.channel }}",
        "card_brand": "{{ input.body.data.authorization.card_type }}", "card_last4": "{{ input.body.data.authorization.last4 }}"}}),
    ("captured", "event.emit", {"event": "payment.captured", "payload": {
        "payment_id": "{{ steps.find.output.data.0.id }}", "order_id": "{{ steps.find.output.data.0.order_id }}",
        "fee_minor": "{{ input.body.data.fees | default:0 }}"}}),
], [("hook", "success"), ("success", "find", "true"), ("find", "known"), ("known", "unknown", "false"), ("known", "done_before", "true"),
    ("done_before", "duplicate", "true"), ("done_before", "short", "false"), ("short", "fail", "true"), ("fail", "fail_event"),
    ("short", "details", "false"), ("details", "captured")]))

FLOWS.append(flow("mark_paid", "POST /orders/{id}/mark-paid: record an offline payment (cash, transfer, POS card).", [
    ("http", "trigger.http", {}),
    ("order", "resource.get", {"resource": "orders", "id": "{{ input.params.id }}"}),
    err("missing", 404, "order_not_found", "No such order"),
    own_store("foreign", "order.output"),
    err("not_yours", 404, "order_not_found", "No such order"),
    ("state", "control.if", {"condition": {"ne": ["$steps.order.output.payment_status", "pending"]}}),
    err("paid", 409, "not_pending", "This order isn't awaiting payment"),
    ("cancelled", "control.if", {"condition": {"eq": ["$steps.order.output.status", "cancelled"]}}),
    err("closed", 409, "order_cancelled", "This order was cancelled"),
    ("ref", "util.id", {"kind": "token", "length": 12}),
    ("payment", "resource.create", {"resource": "payments", "data": {
        "store_id": "{{ steps.order.output.store_id }}", "order_id": "{{ steps.order.output.id }}", "provider": body("method"),
        "method": body("method"), "reference": "OFF-{{ steps.ref.output }}", "provider_reference": body("reference"),
        "currency": "{{ steps.order.output.currency }}", "amount_minor": "{{ steps.order.output.total_minor }}", "status": "pending"}}),
    ("captured", "event.emit", {"event": "payment.captured", "payload": {"payment_id": "{{ steps.payment.output.id }}", "order_id": "{{ steps.order.output.id }}", "fee_minor": 0}}),
    ("reply", "response.return", {"body": {"payment": "{{ steps.payment.output }}", "status": "capturing"}}),
], [("http", "order"), ("order", "missing", "missing"), ("order", "foreign"), ("foreign", "not_yours", "true"), ("foreign", "state", "false"),
    ("state", "paid", "true"), ("state", "cancelled", "false"), ("cancelled", "closed", "true"), ("cancelled", "ref", "false"),
    ("ref", "payment"), ("payment", "captured"), ("captured", "reply")]))

FLOWS.append(flow("capture_payment", "On payment.captured: settle the payment, mark the order paid, write sale and fee ledger entries, then announce order.paid.", [
    ("captured", "trigger.event", {"event": "payment.captured"}),
    ("payment", "resource.get", {"resource": "payments", "id": f"{{{{ {P}.payment_id }}}}"}),
    ("order", "resource.get", {"resource": "orders", "id": f"{{{{ {P}.order_id }}}}"}),
    ("already", "control.if", {"condition": {"ne": ["$steps.order.output.payment_status", "pending"]}}),
    ("skip", "log.write", {"level": "info", "message": "order already settled", "data": {"order_id": "{{ steps.order.output.id }}"}}),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": "{{ steps.order.output.store_id }}"}, "limit": 1}),
    ("fee_raw", "math.calculate", {"operation": "multiply", "values": ["{{ steps.payment.output.amount_minor }}", "{{ steps.store.output.data.0.platform_fee_bps | default:0 }}"], "digits": 0}),
    ("platform_fee", "math.calculate", {"operation": "divide", "values": ["{{ steps.fee_raw.output }}", 10000], "digits": 0}),
    ("now", "time.now", {}),
    ("settle", "resource.update", {"resource": "payments", "id": "{{ steps.payment.output.id }}", "data": {
        "status": "captured", "captured_at": "{{ steps.now.output }}", "provider_fee_minor": f"{{{{ {P}.fee_minor | int }}}}",
        "platform_fee_minor": "{{ steps.platform_fee.output | int }}"}}),
    ("paid", "resource.update", {"resource": "orders", "id": "{{ steps.order.output.id }}", "data": {"payment_status": "paid", "paid_at": "{{ steps.now.output }}"}}),
    ("event", "resource.create", {"resource": "order_events", "data": {"store_id": "{{ steps.order.output.store_id }}", "order_id": "{{ steps.order.output.id }}",
        "user_id": "{{ steps.order.output.user_id }}", "kind": "paid", "message": "Payment received ({{ steps.payment.output.provider }})", "is_customer_visible": True}}),
    ("sale", "resource.create", {"resource": "ledger_entries", "data": {"store_id": "{{ steps.order.output.store_id }}", "kind": "sale",
        "amount_minor": "{{ steps.payment.output.amount_minor }}", "currency": "{{ steps.order.output.currency }}", "order_id": "{{ steps.order.output.id }}",
        "payment_id": "{{ steps.payment.output.id }}", "description": "Order #{{ steps.order.output.number }}", "occurred_at": "{{ steps.now.output }}"}}),
    ("neg_platform", "math.calculate", {"operation": "multiply", "values": ["{{ steps.platform_fee.output }}", -1], "digits": 0}),
    ("platform_entry", "resource.create", {"resource": "ledger_entries", "data": {"store_id": "{{ steps.order.output.store_id }}", "kind": "platform_fee",
        "amount_minor": "{{ steps.neg_platform.output | int }}", "currency": "{{ steps.order.output.currency }}", "order_id": "{{ steps.order.output.id }}",
        "payment_id": "{{ steps.payment.output.id }}", "description": "Platform fee", "occurred_at": "{{ steps.now.output }}"}}),
    ("has_fee", "control.if", {"condition": {"gt": [f"${P}.fee_minor", 0]}}),
    ("neg_provider", "math.calculate", {"operation": "multiply", "values": [f"{{{{ {P}.fee_minor }}}}", -1], "digits": 0}),
    ("provider_entry", "resource.create", {"resource": "ledger_entries", "data": {"store_id": "{{ steps.order.output.store_id }}", "kind": "provider_fee",
        "amount_minor": "{{ steps.neg_provider.output | int }}", "currency": "{{ steps.order.output.currency }}", "order_id": "{{ steps.order.output.id }}",
        "payment_id": "{{ steps.payment.output.id }}", "description": "Payment provider fee", "occurred_at": "{{ steps.now.output }}"}}),
    ("announce", "event.emit", {"event": "order.paid", "payload": {"order_id": "{{ steps.order.output.id }}", "store_id": "{{ steps.order.output.store_id }}"}}),
], [("captured", "payment"), ("payment", "order"), ("order", "already"), ("already", "skip", "true"), ("already", "store", "false"),
    ("store", "fee_raw"), ("fee_raw", "platform_fee"), ("platform_fee", "now"), ("now", "settle"), ("settle", "paid"), ("paid", "event"),
    ("event", "sale"), ("sale", "neg_platform"), ("neg_platform", "platform_entry"), ("platform_entry", "has_fee"),
    ("has_fee", "neg_provider", "true"), ("neg_provider", "provider_entry"), ("provider_entry", "announce"), ("has_fee", "announce", "false")]))

ORDER_ITEMS_SQL = """SELECT oi.id, oi.variant_id, oi.product_id, oi.quantity, oi.title, oi.variant_title, oi.unit_price_minor, oi.total_minor,
       v.stock, v.reserved, v.track_inventory
FROM order_items oi LEFT JOIN product_variants v ON v.id = oi.variant_id WHERE oi.order_id = ? ORDER BY oi.id"""

O = "steps.order.output"
FLOWS.append(flow("order_paid", "On order.paid: commit stock (reserved → sold, with movements), update customer lifetime value, discount usage and campaign attribution, email the receipt, notify the store live.", [
    ("paid", "trigger.event", {"event": "order.paid"}),
    ("order", "resource.get", {"resource": "orders", "id": f"{{{{ {P}.order_id }}}}"}),
    ("items", "db.query", {"sql": ORDER_ITEMS_SQL, "params": [f"{{{{ {O}.id }}}}"]}),
    ("each", "control.foreach", {"items": "{{ steps.items.output }}"}),
    ("tracked", "control.if", {"condition": {"truthy": "$item.track_inventory"}}),
    ("stock", "math.calculate", {"operation": "subtract", "values": ["{{ item.stock }}", "{{ item.quantity }}"], "digits": 0}),
    ("reserved_raw", "math.calculate", {"operation": "subtract", "values": ["{{ item.reserved }}", "{{ item.quantity }}"], "digits": 0}),
    ("reserved", "math.calculate", {"operation": "max", "values": ["{{ steps.reserved_raw.output }}", 0], "digits": 0}),
    ("commit", "resource.update", {"resource": "product_variants", "id": "{{ item.variant_id }}", "data": {
        "stock": "{{ steps.stock.output | int }}", "reserved": "{{ steps.reserved.output | int }}"}}),
    ("neg", "math.calculate", {"operation": "multiply", "values": ["{{ item.quantity }}", -1], "digits": 0}),
    ("movement", "resource.create", {"resource": "inventory_movements", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "variant_id": "{{ item.variant_id }}",
        "delta": "{{ steps.neg.output | int }}", "balance_after": "{{ steps.stock.output | int }}", "reason": "sale",
        "reference_type": "order", "reference_id": f"{{{{ {O}.id }}}}"}}),
    ("product", "resource.get", {"resource": "products", "id": "{{ item.product_id }}"}),
    ("bought", "math.calculate", {"operation": "add", "values": ["{{ steps.product.output.purchase_count | default:0 }}", "{{ item.quantity }}"], "digits": 0}),
    ("popularity", "resource.update", {"resource": "products", "id": "{{ item.product_id }}", "data": {"purchase_count": "{{ steps.bought.output | int }}"}}),
    ("lifetime", "db.query", {"sql": """SELECT COUNT(*) AS n, COALESCE(SUM(total_minor - refunded_minor), 0) AS spent, MIN(placed_at) AS first_at, MAX(placed_at) AS last_at
FROM orders WHERE customer_id = ? AND payment_status IN ('paid', 'partially_refunded')""", "params": [f"{{{{ {O}.customer_id }}}}"]}),
    ("customer", "resource.update", {"resource": "customers", "id": f"{{{{ {O}.customer_id }}}}", "data": {
        "orders_count": "{{ steps.lifetime.output.0.n | int }}", "total_spent_minor": "{{ steps.lifetime.output.0.spent | int }}",
        "first_order_at": "{{ steps.lifetime.output.0.first_at }}", "last_order_at": "{{ steps.lifetime.output.0.last_at }}"}}),
    ("used_discount", "control.if", {"condition": {"exists": f"${O}.discount_id"}}),
    ("usage", "resource.create", {"resource": "discount_usages", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "discount_id": f"{{{{ {O}.discount_id }}}}",
        "order_id": f"{{{{ {O}.id }}}}", "customer_id": f"{{{{ {O}.customer_id }}}}", "amount_minor": f"{{{{ {O}.discount_minor }}}}"}}),
    ("discount", "resource.get", {"resource": "discounts", "id": f"{{{{ {O}.discount_id }}}}"}),
    ("count", "math.calculate", {"operation": "add", "values": ["{{ steps.discount.output.usage_count | default:0 }}", 1], "digits": 0}),
    ("count_save", "resource.update", {"resource": "discounts", "id": f"{{{{ {O}.discount_id }}}}", "data": {"usage_count": "{{ steps.count.output | int }}"}}),
    ("attributed", "control.if", {"condition": {"exists": f"${O}.campaign_id"}}),
    ("campaign", "resource.get", {"resource": "campaigns", "id": f"{{{{ {O}.campaign_id }}}}"}),
    ("c_orders", "math.calculate", {"operation": "add", "values": ["{{ steps.campaign.output.orders_count | default:0 }}", 1], "digits": 0}),
    ("c_revenue", "math.calculate", {"operation": "add", "values": ["{{ steps.campaign.output.revenue_minor | default:0 }}", f"{{{{ {O}.total_minor }}}}"], "digits": 0}),
    ("c_save", "resource.update", {"resource": "campaigns", "id": f"{{{{ {O}.campaign_id }}}}", "data": {
        "orders_count": "{{ steps.c_orders.output | int }}", "revenue_minor": "{{ steps.c_revenue.output | int }}"}}),
    ("receipt", "mail.send", {"to": f"{{{{ {O}.email }}}}", "template": "order_confirmation", "data": {
        "order": f"{{{{ {O} }}}}", "items": "{{ steps.items.output }}", "store": f"{{{{ {O}.store_id }}}}"}}),
    ("notify", "resource.create", {"resource": "notifications", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "kind": "order_paid",
        "title": "New order #{{ steps.order.output.number }}", "body": "{{ steps.order.output.email }} paid {{ steps.order.output.currency }} {{ steps.order.output.total_minor }}",
        "url": "/orders/{{ steps.order.output.id }}", "level": "success"}}),
    ("live", "realtime.publish", {"channel": f"store:{{{{ {O}.store_id }}}}", "event": "order.paid", "payload": {
        "order_id": f"{{{{ {O}.id }}}}", "number": f"{{{{ {O}.number }}}}", "total_minor": f"{{{{ {O}.total_minor }}}}", "currency": f"{{{{ {O}.currency }}}}"}}),
], [("paid", "order"), ("order", "items"), ("items", "each"), ("each", "tracked", "each"), ("tracked", "stock", "true"), ("stock", "reserved_raw"),
    ("reserved_raw", "reserved"), ("reserved", "commit"), ("commit", "neg"), ("neg", "movement"), ("movement", "product"),
    ("tracked", "product", "false"), ("product", "bought"), ("bought", "popularity"),
    ("each", "lifetime", "done"), ("lifetime", "customer"), ("customer", "used_discount"),
    ("used_discount", "usage", "true"), ("usage", "discount"), ("discount", "count"), ("count", "count_save"), ("count_save", "attributed"),
    ("used_discount", "attributed", "false"), ("attributed", "campaign", "true"), ("campaign", "c_orders"), ("c_orders", "c_revenue"),
    ("c_revenue", "c_save"), ("c_save", "receipt"), ("attributed", "receipt", "false"), ("receipt", "notify"), ("notify", "live")], timeout=120))

# Fulfilment, cancellation, refunds ------------------------------------------
FLOWS.append(flow("fulfil_order", "POST /orders/{id}/fulfil: ship a paid order, record tracking, email the customer.", [
    ("http", "trigger.http", {}),
    ("order", "resource.get", {"resource": "orders", "id": "{{ input.params.id }}"}),
    err("missing", 404, "order_not_found", "No such order"),
    own_store("foreign", "order.output"),
    err("not_yours", 404, "order_not_found", "No such order"),
    ("cancelled", "control.if", {"condition": {"eq": [f"${O}.status", "cancelled"]}}),
    err("closed", 409, "order_cancelled", "This order was cancelled"),
    ("unpaid", "control.if", {"condition": {"not": {"in": [f"${O}.payment_status", ["paid", "partially_refunded"]]}}}),
    err("not_paid", 409, "not_paid", "Only paid orders can be fulfilled"),
    ("done", "control.if", {"condition": {"eq": [f"${O}.fulfilment_status", "fulfilled"]}}),
    err("already", 409, "already_fulfilled", "This order is already fulfilled"),
    ("now", "time.now", {}),
    ("ship", "resource.update", {"resource": "orders", "id": f"{{{{ {O}.id }}}}", "data": {
        "fulfilment_status": "fulfilled", "fulfilled_at": "{{ steps.now.output }}", "carrier": body("carrier"),
        "tracking_number": body("tracking_number"), "tracking_url": body("tracking_url")}}),
    ("lines", "resource.list", {"resource": "order_items", "filters": {"order_id": f"{{{{ {O}.id }}}}"}, "limit": 200}),
    ("each", "control.foreach", {"items": "{{ steps.lines.output.data }}"}),
    ("line", "resource.update", {"resource": "order_items", "id": "{{ item.id }}", "data": {"quantity_fulfilled": "{{ item.quantity }}"}}),
    ("event", "resource.create", {"resource": "order_events", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "order_id": f"{{{{ {O}.id }}}}",
        "user_id": f"{{{{ {O}.user_id }}}}", "kind": "fulfilled", "message": "Order shipped", "data": {"carrier": body("carrier"), "tracking_number": body("tracking_number"), "tracking_url": body("tracking_url")},
        "actor_id": "{{ auth.user_id }}", "is_customer_visible": True}}),
    ("tell", "control.if", {"condition": {"not": {"eq": ["$input.body.notify_customer", False]}}}),
    ("mail", "mail.send", {"to": f"{{{{ {O}.email }}}}", "template": "shipping_update", "data": {"order": "{{ steps.ship.output }}"}}),
    ("announce", "event.emit", {"event": "order.fulfilled", "payload": {"order_id": f"{{{{ {O}.id }}}}", "store_id": f"{{{{ {O}.store_id }}}}"}}),
    ("reply", "response.return", {"body": "{{ steps.ship.output }}"}),
], [("http", "order"), ("order", "missing", "missing"), ("order", "foreign"), ("foreign", "not_yours", "true"), ("foreign", "cancelled", "false"),
    ("cancelled", "closed", "true"), ("cancelled", "unpaid", "false"), ("unpaid", "not_paid", "true"), ("unpaid", "done", "false"),
    ("done", "already", "true"), ("done", "now", "false"), ("now", "ship"), ("ship", "lines"), ("lines", "each"), ("each", "line", "each"),
    ("each", "event", "done"), ("event", "tell"), ("tell", "mail", "true"), ("mail", "announce"), ("tell", "announce", "false"), ("announce", "reply")]))

FLOWS.append(flow("cancel_order", "POST /orders/{id}/cancel: cancel an unpaid order and release its reserved stock. Paid orders are refunded instead.", [
    ("http", "trigger.http", {}),
    ("order", "resource.get", {"resource": "orders", "id": "{{ input.params.id }}"}),
    err("missing", 404, "order_not_found", "No such order"),
    own_store("foreign", "order.output"),
    err("not_yours", 404, "order_not_found", "No such order"),
    ("cancelled", "control.if", {"condition": {"eq": [f"${O}.status", "cancelled"]}}),
    err("already", 409, "already_cancelled", "This order is already cancelled"),
    ("paid", "control.if", {"condition": {"ne": [f"${O}.payment_status", "pending"]}}),
    err("refund_first", 409, "refund_instead", "This order was paid; refund it instead of cancelling"),
    ("items", "db.query", {"sql": ORDER_ITEMS_SQL, "params": [f"{{{{ {O}.id }}}}"]}),
    ("each", "control.foreach", {"items": "{{ steps.items.output }}"}),
    ("tracked", "control.if", {"condition": {"truthy": "$item.track_inventory"}}),
    ("less", "math.calculate", {"operation": "subtract", "values": ["{{ item.reserved }}", "{{ item.quantity }}"], "digits": 0}),
    ("floor", "math.calculate", {"operation": "max", "values": ["{{ steps.less.output }}", 0], "digits": 0}),
    ("release", "resource.update", {"resource": "product_variants", "id": "{{ item.variant_id }}", "data": {"reserved": "{{ steps.floor.output | int }}"}}),
    ("now", "time.now", {}),
    ("cancel", "resource.update", {"resource": "orders", "id": f"{{{{ {O}.id }}}}", "data": {
        "status": "cancelled", "payment_status": "voided", "cancelled_at": "{{ steps.now.output }}", "cancel_reason": body("reason")}}),
    ("event", "resource.create", {"resource": "order_events", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "order_id": f"{{{{ {O}.id }}}}",
        "user_id": f"{{{{ {O}.user_id }}}}", "kind": "cancelled", "message": "Order cancelled ({{ input.body.reason }})",
        "data": {"note": body("note")}, "actor_id": "{{ auth.user_id }}", "is_customer_visible": True}}),
    ("mail", "mail.send", {"to": f"{{{{ {O}.email }}}}", "template": "order_cancelled", "data": {"order": "{{ steps.cancel.output }}"}}),
    ("announce", "event.emit", {"event": "order.cancelled", "payload": {"order_id": f"{{{{ {O}.id }}}}", "store_id": f"{{{{ {O}.store_id }}}}", "reason": body("reason")}}),
    ("reply", "response.return", {"body": "{{ steps.cancel.output }}"}),
], [("http", "order"), ("order", "missing", "missing"), ("order", "foreign"), ("foreign", "not_yours", "true"), ("foreign", "cancelled", "false"),
    ("cancelled", "already", "true"), ("cancelled", "paid", "false"), ("paid", "refund_first", "true"), ("paid", "items", "false"),
    ("items", "each"), ("each", "tracked", "each"), ("tracked", "less", "true"), ("less", "floor"), ("floor", "release"),
    ("each", "now", "done"), ("now", "cancel"), ("cancel", "event"), ("event", "mail"), ("mail", "announce"), ("announce", "reply")]))

FLOWS.append(flow("refund_order", "POST /orders/{id}/refunds: refund part or all of a paid order; full refunds can restock. Writes the refund, the ledger and the timeline, and emails the customer.", [
    ("http", "trigger.http", {}),
    ("order", "resource.get", {"resource": "orders", "id": "{{ input.params.id }}"}),
    err("missing", 404, "order_not_found", "No such order"),
    own_store("foreign", "order.output"),
    err("not_yours", 404, "order_not_found", "No such order"),
    ("refundable", "control.if", {"condition": {"not": {"in": [f"${O}.payment_status", ["paid", "partially_refunded"]]}}}),
    err("not_paid", 409, "not_refundable", "Only paid orders can be refunded"),
    ("left", "math.calculate", {"operation": "subtract", "values": [f"{{{{ {O}.total_minor }}}}", f"{{{{ {O}.refunded_minor | default:0 }}}}"], "digits": 0}),
    ("too_much", "control.if", {"condition": {"gt": ["$input.body.amount_minor", "$steps.left.output"]}}),
    err("exceeds", 422, "exceeds_refundable", "The amount is more than what's left to refund"),
    ("ref", "util.id", {"kind": "token", "length": 12}),
    ("now", "time.now", {}),
    ("refund", "resource.create", {"resource": "refunds", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "order_id": f"{{{{ {O}.id }}}}",
        "reference": "REF-{{ steps.ref.output }}", "amount_minor": body("amount_minor"), "currency": f"{{{{ {O}.currency }}}}",
        "reason": body("reason"), "note": body("note"), "restock": body("restock"), "status": "succeeded", "actor_id": "{{ auth.user_id }}"}}),
    ("total_refunded", "math.calculate", {"operation": "add", "values": [f"{{{{ {O}.refunded_minor | default:0 }}}}", body("amount_minor")], "digits": 0}),
    ("full", "control.if", {"condition": {"gte": ["$steps.total_refunded.output", f"${O}.total_minor"]}}),
    ("status_full", "control.set", {"values": {"payment_status": "refunded"}}),
    ("status_part", "control.set", {"values": {"payment_status": "partially_refunded"}}),
    ("save", "resource.update", {"resource": "orders", "id": f"{{{{ {O}.id }}}}", "data": {
        "refunded_minor": "{{ steps.total_refunded.output | int }}", "payment_status": "{{ vars.payment_status }}"}}),
    ("neg", "math.calculate", {"operation": "multiply", "values": [body("amount_minor"), -1], "digits": 0}),
    ("ledger", "resource.create", {"resource": "ledger_entries", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "kind": "refund",
        "amount_minor": "{{ steps.neg.output | int }}", "currency": f"{{{{ {O}.currency }}}}", "order_id": f"{{{{ {O}.id }}}}",
        "refund_id": "{{ steps.refund.output.id }}", "description": "Refund on order #{{ steps.order.output.number }}", "occurred_at": "{{ steps.now.output }}"}}),
    ("event", "resource.create", {"resource": "order_events", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "order_id": f"{{{{ {O}.id }}}}",
        "user_id": f"{{{{ {O}.user_id }}}}", "kind": "refunded", "message": "Refunded {{ steps.order.output.currency }} {{ input.body.amount_minor }} ({{ input.body.reason }})",
        "actor_id": "{{ auth.user_id }}", "is_customer_visible": True}}),
    ("restock", "control.if", {"condition": {"all": [{"eq": ["$input.body.restock", True]}, {"eq": ["$vars.payment_status", "refunded"]}]}}),
    ("items", "db.query", {"sql": ORDER_ITEMS_SQL, "params": [f"{{{{ {O}.id }}}}"]}),
    ("each", "control.foreach", {"items": "{{ steps.items.output }}"}),
    ("tracked", "control.if", {"condition": {"truthy": "$item.track_inventory"}}),
    ("back", "math.calculate", {"operation": "add", "values": ["{{ item.stock }}", "{{ item.quantity }}"], "digits": 0}),
    ("put_back", "resource.update", {"resource": "product_variants", "id": "{{ item.variant_id }}", "data": {"stock": "{{ steps.back.output | int }}"}}),
    ("movement", "resource.create", {"resource": "inventory_movements", "data": {"store_id": f"{{{{ {O}.store_id }}}}", "variant_id": "{{ item.variant_id }}",
        "delta": "{{ item.quantity }}", "balance_after": "{{ steps.back.output | int }}", "reason": "refund_restock",
        "reference_type": "refund", "reference_id": "{{ steps.refund.output.id }}"}}),
    ("mail", "mail.send", {"to": f"{{{{ {O}.email }}}}", "template": "refund_issued", "data": {"order": "{{ steps.save.output }}", "refund": "{{ steps.refund.output }}"}}),
    ("announce", "event.emit", {"event": "order.refunded", "payload": {"order_id": f"{{{{ {O}.id }}}}", "store_id": f"{{{{ {O}.store_id }}}}",
        "amount_minor": body("amount_minor"), "refund_id": "{{ steps.refund.output.id }}"}}),
    ("reply", "response.return", {"status": 201, "body": {"refund": "{{ steps.refund.output }}", "order": "{{ steps.save.output }}"}}),
], [("http", "order"), ("order", "missing", "missing"), ("order", "foreign"), ("foreign", "not_yours", "true"), ("foreign", "refundable", "false"),
    ("refundable", "not_paid", "true"), ("refundable", "left", "false"), ("left", "too_much"), ("too_much", "exceeds", "true"),
    ("too_much", "ref", "false"), ("ref", "now"), ("now", "refund"), ("refund", "total_refunded"), ("total_refunded", "full"),
    ("full", "status_full", "true"), ("full", "status_part", "false"), ("status_full", "save"), ("status_part", "save"),
    ("save", "neg"), ("neg", "ledger"), ("ledger", "event"), ("event", "restock"), ("restock", "items", "true"), ("items", "each"),
    ("each", "tracked", "each"), ("tracked", "back", "true"), ("back", "put_back"), ("put_back", "movement"),
    ("each", "mail", "done"), ("restock", "mail", "false"), ("mail", "announce"), ("announce", "reply")], timeout=120))

# Inventory -------------------------------------------------------------------
FLOWS.append(flow("adjust_inventory", "POST /inventory/adjust: change stock with a reason, recording a movement.", [
    ("http", "trigger.http", {}),
    ("variant", "resource.get", {"resource": "product_variants", "id": body("variant_id")}),
    err("missing", 404, "variant_not_found", "No such variant"),
    own_store("foreign", "variant.output"),
    err("not_yours", 404, "variant_not_found", "No such variant"),
    ("new", "math.calculate", {"operation": "add", "values": ["{{ steps.variant.output.stock }}", body("delta")], "digits": 0}),
    ("negative", "control.if", {"condition": {"all": [{"lt": ["$steps.new.output", 0]}, {"not": {"truthy": "$steps.variant.output.allow_backorder"}}]}}),
    err("below_zero", 422, "negative_stock", "Stock can't go below zero for this variant"),
    ("save", "resource.update", {"resource": "product_variants", "id": body("variant_id"), "data": {"stock": "{{ steps.new.output | int }}"}}),
    ("movement", "resource.create", {"resource": "inventory_movements", "data": {"store_id": "{{ steps.variant.output.store_id }}",
        "variant_id": body("variant_id"), "delta": body("delta"), "balance_after": "{{ steps.new.output | int }}", "reason": body("reason"),
        "note": body("note"), "reference_type": "adjustment", "actor_id": "{{ auth.user_id }}"}}),
    ("reply", "response.return", {"body": {"variant": "{{ steps.save.output }}", "movement": "{{ steps.movement.output }}"}}),
], [("http", "variant"), ("variant", "missing", "missing"), ("variant", "foreign"), ("foreign", "not_yours", "true"), ("foreign", "new", "false"),
    ("new", "negative"), ("negative", "below_zero", "true"), ("negative", "save", "false"), ("save", "movement"), ("movement", "reply")]))

R = "input.event.payload.record"
FLOWS.append(flow("low_stock_alert", "When a tracked variant falls to its low-stock threshold, alert the store (notification, live, email).", [
    ("changed", "trigger.resource", {"resource": "product_variants", "operations": ["updated"]}),
    ("low", "control.if", {"condition": {"all": [{"truthy": f"${R}.track_inventory"}, {"lte": [f"${R}.stock", f"${R}.low_stock_threshold"]}]}}),
    ("product", "resource.get", {"resource": "products", "id": f"{{{{ {R}.product_id }}}}"}),
    ("notify", "resource.create", {"resource": "notifications", "data": {"store_id": f"{{{{ {R}.store_id }}}}", "kind": "low_stock",
        "title": "Low stock: {{ steps.product.output.title }} ({{ input.event.payload.record.title }})",
        "body": "{{ input.event.payload.record.stock }} left", "url": "/products/{{ steps.product.output.id }}", "level": "warning"}}),
    ("live", "realtime.publish", {"channel": f"store:{{{{ {R}.store_id }}}}", "event": "inventory.low", "payload": {
        "variant_id": f"{{{{ {R}.id }}}}", "stock": f"{{{{ {R}.stock }}}}", "product": "{{ steps.product.output.title }}"}}),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": f"{{{{ {R}.store_id }}}}"}, "limit": 1}),
    ("mail", "mail.send", {"to": "{{ steps.store.output.data.0.email }}", "template": "low_stock", "data": {
        "product": "{{ steps.product.output.title }}", "variant": f"{{{{ {R}.title }}}}", "stock": f"{{{{ {R}.stock }}}}", "sku": f"{{{{ {R}.sku }}}}"}}),
], [("changed", "low"), ("low", "product", "true"), ("product", "notify"), ("notify", "live"), ("live", "store"), ("store", "mail")]))

# Abandoned carts -------------------------------------------------------------
FLOWS.append(flow("abandoned_cart_sweep", "Every 15 minutes: carts idle for an hour with an email become abandoned carts and get a recovery email.", [
    ("tick", "trigger.schedule", {"every": 900}),
    ("manual", "trigger.manual", {}),
    ("cutoff", "time.now", {"offset_seconds": -3600}),
    ("idle", "db.query", {"sql": """SELECT id, store_id, email, currency, subtotal_minor, discount_minor, item_count, token
FROM carts WHERE status = 'open' AND item_count > 0 AND email IS NOT NULL AND last_activity_at < ? ORDER BY id LIMIT 200""",
        "params": ["{{ steps.cutoff.output }}"]}),
    ("each", "control.foreach", {"items": "{{ steps.idle.output }}"}),
    ("token", "util.id", {"kind": "token", "length": 32}),
    ("now", "time.now", {}),
    ("record", "resource.create", {"resource": "abandoned_carts", "data": {"store_id": "{{ item.store_id }}", "cart_id": "{{ item.id }}",
        "email": "{{ item.email }}", "value_minor": "{{ item.subtotal_minor }}", "currency": "{{ item.currency }}", "item_count": "{{ item.item_count }}",
        "recovery_status": "emailed", "recovery_token": "{{ steps.token.output }}", "notified_at": "{{ steps.now.output }}"}}),
    ("mark", "resource.update", {"resource": "carts", "id": "{{ item.id }}", "data": {"status": "abandoned"}}),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": "{{ item.store_id }}"}, "limit": 1}),
    ("mail", "mail.send", {"to": "{{ item.email }}", "template": "cart_recovery", "data": {
        "store": "{{ steps.store.output.data.0 }}", "token": "{{ steps.token.output }}", "value_minor": "{{ item.subtotal_minor }}",
        "currency": "{{ item.currency }}", "items": "{{ item.item_count }}"}}),
    ("count", "metric.increment", {"name": "commerce.abandoned_carts", "value": "{{ steps.idle.output | length }}"}),
], [("tick", "cutoff"), ("manual", "cutoff"), ("cutoff", "idle"), ("idle", "each"), ("each", "token", "each"), ("token", "now"), ("now", "record"),
    ("record", "mark"), ("mark", "store"), ("store", "mail"), ("each", "count", "done")]))

FLOWS.append(flow("recover_cart", "GET /storefront/recover/{token}: reopen an abandoned cart from its recovery link.", [
    ("http", "trigger.http", {}),
    ("find", "resource.list", {"resource": "abandoned_carts", "filters": {"recovery_token": "{{ input.params.token }}"}, "limit": 1}),
    ("none", "control.if", {"condition": {"not": {"truthy": "$steps.find.output.total"}}}),
    err("missing", 404, "link_not_found", "This link has expired"),
    ("cart", "resource.get", {"resource": "carts", "id": "{{ steps.find.output.data.0.cart_id }}"}),
    err("gone", 404, "cart_not_found", "This cart no longer exists"),
    ("open", "control.if", {"condition": {"eq": ["$steps.cart.output.status", "abandoned"]}}),
    ("reopen", "resource.update", {"resource": "carts", "id": "{{ steps.cart.output.id }}", "data": {"status": "open"}}),
    ("reply", "response.return", {"body": {"cart_token": "{{ steps.cart.output.token }}", "store_id": "{{ steps.cart.output.store_id }}"}}),
], [("http", "find"), ("find", "none"), ("none", "missing", "true"), ("none", "cart", "false"), ("cart", "gone", "missing"), ("cart", "open"),
    ("open", "reopen", "true"), ("reopen", "reply"), ("open", "reply", "false")]))

FLOWS.append(flow("cart_recovered", "On order.placed: if the cart had been abandoned, mark it recovered and credit the order.", [
    ("placed", "trigger.event", {"event": "order.placed"}),
    ("find", "resource.list", {"resource": "abandoned_carts", "filters": {"cart_id": f"{{{{ {P}.cart_id }}}}", "recovery_status": "emailed"}, "limit": 1}),
    ("was", "control.if", {"condition": {"truthy": "$steps.find.output.total"}}),
    ("now", "time.now", {}),
    ("mark", "resource.update", {"resource": "abandoned_carts", "id": "{{ steps.find.output.data.0.id }}", "data": {
        "recovery_status": "recovered", "recovered_at": "{{ steps.now.output }}", "recovered_order_id": f"{{{{ {P}.order_id }}}}"}}),
    ("count", "metric.increment", {"name": "commerce.carts_recovered"}),
], [("placed", "find"), ("find", "was"), ("was", "now", "true"), ("now", "mark"), ("mark", "count")]))

# Merchant onboarding and dashboard -------------------------------------------
FLOWS.append(flow("register_store", "POST /merchant/stores: register the store for the active organization, with a starter theme, shipping, tax and segments.", [
    ("http", "trigger.http", {}),
    ("exists", "resource.list", {"resource": "stores", "filters": {"slug": "{{ auth.org }}"}, "limit": 1}),
    ("taken", "control.if", {"condition": {"truthy": "$steps.exists.output.total"}}),
    err("already", 409, "store_exists", "This organization already has a store"),
    ("store", "resource.create", {"resource": "stores", "data": {
        "slug": "{{ auth.org }}", "name": body("name"), "email": body("email"), "currency": body("currency"),
        "country": body("country"), "timezone": body("timezone"), "status": "active", "plan": "starter", "owner_user_id": "{{ auth.user_id }}"}}),
    ("theme", "resource.create", {"resource": "themes", "data": {"store_id": "{{ auth.org }}", "name": "Default", "is_active": True,
        "colors": {"primary": "#1f1b2e", "accent": "#6d5bd0", "background": "#ffffff"}, "fonts": {"heading": "Plus Jakarta Sans", "body": "Inter"},
        "sections": [{"type": "hero"}, {"type": "featured_collection"}, {"type": "newsletter"}]}}),
    ("zone", "resource.create", {"resource": "shipping_zones", "data": {"store_id": "{{ auth.org }}", "name": "Domestic", "countries": ["{{ input.body.country }}"]}}),
    ("rate", "resource.create", {"resource": "shipping_rates", "data": {"store_id": "{{ auth.org }}", "zone_id": "{{ steps.zone.output.id }}",
        "name": "Standard delivery", "kind": "flat", "price_minor": 0, "delivery_estimate": "2–5 days"}}),
    ("vip", "resource.create", {"resource": "segments", "data": {"store_id": "{{ auth.org }}", "name": "VIP", "is_system": True,
        "rules": {"all": [{"field": "orders_count", "op": "gte", "value": 5}]}}}),
    ("new_buyers", "resource.create", {"resource": "segments", "data": {"store_id": "{{ auth.org }}", "name": "First-time buyers", "is_system": True,
        "rules": {"all": [{"field": "orders_count", "op": "eq", "value": 1}]}}}),
    ("about", "resource.create", {"resource": "pages", "data": {"store_id": "{{ auth.org }}", "title": "About us", "handle": "about",
        "body": "Tell your customers who you are.", "is_published": False}}),
    ("welcome", "mail.send", {"to": body("email"), "template": "store_welcome", "data": {"store": "{{ steps.store.output }}"}}),
    ("announce", "event.emit", {"event": "store.created", "payload": {"store_id": "{{ auth.org }}", "name": body("name"), "owner_user_id": "{{ auth.user_id }}"}}),
    ("reply", "response.return", {"status": 201, "body": {"store": "{{ steps.store.output }}", "theme": "{{ steps.theme.output }}"}}),
], [("http", "exists"), ("exists", "taken"), ("taken", "already", "true"), ("taken", "store", "false"), ("store", "theme"), ("theme", "zone"),
    ("zone", "rate"), ("rate", "vip"), ("vip", "new_buyers"), ("new_buyers", "about"), ("about", "welcome"), ("welcome", "announce"), ("announce", "reply")]))

FLOWS.append(flow("store_dashboard", "GET /merchant/dashboard: the store's headline numbers (cached 30 s).", [
    ("http", "trigger.http", {}),
    ("today", "time.now", {"format": "date"}),
    ("month_ago", "time.now", {"format": "date", "offset_seconds": -2592000}),
    ("sales", "db.query", {"sql": """SELECT COUNT(*) AS orders, COALESCE(SUM(total_minor - refunded_minor), 0) AS net_minor,
       COALESCE(SUM(total_minor), 0) AS gross_minor, COALESCE(SUM(discount_minor), 0) AS discounts_minor,
       CASE WHEN COUNT(*) > 0 THEN SUM(total_minor) / COUNT(*) ELSE 0 END AS average_order_minor
FROM orders WHERE store_id = ? AND payment_status IN ('paid', 'partially_refunded') AND CAST(placed_at AS TEXT) >= ?""",
        "params": ["{{ auth.org }}", "{{ steps.month_ago.output }}"]}),
    ("today_sales", "db.query", {"sql": """SELECT COUNT(*) AS orders, COALESCE(SUM(total_minor), 0) AS gross_minor
FROM orders WHERE store_id = ? AND payment_status IN ('paid', 'partially_refunded') AND CAST(placed_at AS TEXT) LIKE ?""",
        "params": ["{{ auth.org }}", "{{ steps.today.output }}%"]}),
    ("pipeline", "db.query", {"sql": """SELECT
  SUM(CASE WHEN payment_status = 'pending' AND status = 'open' THEN 1 ELSE 0 END) AS awaiting_payment,
  SUM(CASE WHEN payment_status = 'paid' AND fulfilment_status = 'unfulfilled' AND status = 'open' THEN 1 ELSE 0 END) AS to_fulfil,
  SUM(CASE WHEN fulfilment_status = 'fulfilled' THEN 1 ELSE 0 END) AS fulfilled
FROM orders WHERE store_id = ?""", "params": ["{{ auth.org }}"]}),
    ("top", "db.query", {"sql": """SELECT oi.product_id, oi.title, SUM(oi.quantity) AS units, SUM(oi.total_minor) AS revenue_minor
FROM order_items oi JOIN orders o ON o.id = oi.order_id
WHERE o.store_id = ? AND o.payment_status IN ('paid', 'partially_refunded') AND CAST(o.placed_at AS TEXT) >= ?
GROUP BY oi.product_id, oi.title ORDER BY revenue_minor DESC LIMIT 5""", "params": ["{{ auth.org }}", "{{ steps.month_ago.output }}"]}),
    ("stock", "db.query", {"sql": """SELECT COUNT(*) AS low FROM product_variants
WHERE store_id = ? AND track_inventory = ? AND stock <= low_stock_threshold""", "params": ["{{ auth.org }}", True]}),
    ("abandoned", "db.query", {"sql": """SELECT COUNT(*) AS carts, COALESCE(SUM(value_minor), 0) AS value_minor,
  SUM(CASE WHEN recovery_status = 'recovered' THEN 1 ELSE 0 END) AS recovered
FROM abandoned_carts WHERE store_id = ?""", "params": ["{{ auth.org }}"]}),
    ("balance", "db.query", {"sql": "SELECT COALESCE(SUM(amount_minor), 0) AS available_minor FROM ledger_entries WHERE store_id = ?", "params": ["{{ auth.org }}"]}),
    ("customers", "db.query", {"sql": """SELECT COUNT(*) AS total, SUM(CASE WHEN orders_count > 1 THEN 1 ELSE 0 END) AS returning_customers
FROM customers WHERE store_id = ?""", "params": ["{{ auth.org }}"]}),
    ("reply", "response.return", {"body": {
        "store_id": "{{ auth.org }}", "date": "{{ steps.today.output }}",
        "last_30_days": "{{ steps.sales.output.0 }}", "today": "{{ steps.today_sales.output.0 }}", "orders": "{{ steps.pipeline.output.0 }}",
        "top_products": "{{ steps.top.output }}", "low_stock_variants": "{{ steps.stock.output.0.low }}",
        "abandoned_carts": "{{ steps.abandoned.output.0 }}", "balance": "{{ steps.balance.output.0 }}", "customers": "{{ steps.customers.output.0 }}"}}),
], [("http", "today"), ("today", "month_ago"), ("month_ago", "sales"), ("sales", "today_sales"), ("today_sales", "pipeline"), ("pipeline", "top"),
    ("top", "stock"), ("stock", "abandoned"), ("abandoned", "balance"), ("balance", "customers"), ("customers", "reply")]))

# Point of sale -----------------------------------------------------------------
FLOWS.append(flow("pos_sale", "POST /pos/sales: ring up an in-person sale on an open till session; paid immediately.", [
    ("http", "trigger.http", {}),
    ("session", "resource.get", {"resource": "pos_sessions", "id": body("session_id")}),
    err("no_session", 404, "session_not_found", "No such till session"),
    own_store("foreign", "session.output"),
    err("not_yours", 404, "session_not_found", "No such till session"),
    ("closed", "control.if", {"condition": {"ne": ["$steps.session.output.status", "open"]}}),
    err("not_open", 409, "session_closed", "This till session is closed"),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": "{{ auth.org }}"}, "limit": 1}),
    ("number", "db.query", {"sql": "SELECT COALESCE(MAX(number), 1000) + 1 AS n FROM orders WHERE store_id = ?", "params": ["{{ auth.org }}"]}),
    ("now", "time.now", {}),
    ("start", "control.set", {"values": {"subtotal": 0}}),
    ("order", "resource.create", {"resource": "orders", "data": {"store_id": "{{ auth.org }}", "number": "{{ steps.number.output.0.n | int }}",
        "email": "{{ input.body.email | default:\"pos@store.local\" }}", "currency": "{{ steps.store.output.data.0.currency }}", "status": "open",
        "payment_status": "pending", "fulfilment_status": "fulfilled", "source": "pos", "placed_at": "{{ steps.now.output }}", "fulfilled_at": "{{ steps.now.output }}"}}),
    ("each", "control.foreach", {"items": "{{ input.body.lines }}"}),
    ("variant", "resource.get", {"resource": "product_variants", "id": "{{ item.variant_id }}"}),
    ("product", "resource.get", {"resource": "products", "id": "{{ steps.variant.output.product_id }}"}),
    ("line_total", "math.calculate", {"operation": "multiply", "values": ["{{ steps.variant.output.price_minor }}", "{{ item.quantity }}"], "digits": 0}),
    ("line", "resource.create", {"resource": "order_items", "data": {"store_id": "{{ auth.org }}", "order_id": "{{ steps.order.output.id }}",
        "variant_id": "{{ item.variant_id }}", "product_id": "{{ steps.variant.output.product_id }}", "title": "{{ steps.product.output.title }}",
        "variant_title": "{{ steps.variant.output.title }}", "sku": "{{ steps.variant.output.sku }}", "quantity": "{{ item.quantity }}",
        "unit_price_minor": "{{ steps.variant.output.price_minor }}", "total_minor": "{{ steps.line_total.output | int }}",
        "quantity_fulfilled": "{{ item.quantity }}", "cost_minor": "{{ steps.variant.output.cost_minor }}"}}),
    ("add", "math.calculate", {"operation": "add", "values": ["{{ vars.subtotal }}", "{{ steps.line_total.output }}"], "digits": 0}),
    ("keep", "control.set", {"values": {"subtotal": "{{ steps.add.output | int }}"}}),
    ("tax", "db.query", {"sql": "SELECT COALESCE(MAX(rate_bps), 0) AS bps FROM tax_rates WHERE store_id = ? AND country = ?",
        "params": ["{{ auth.org }}", "{{ steps.store.output.data.0.country }}"]}),
    ("tax_raw", "math.calculate", {"operation": "multiply", "values": ["{{ vars.subtotal }}", "{{ steps.tax.output.0.bps }}"], "digits": 0}),
    ("tax_amount", "math.calculate", {"operation": "divide", "values": ["{{ steps.tax_raw.output }}", 10000], "digits": 0}),
    ("total", "math.calculate", {"operation": "add", "values": ["{{ vars.subtotal }}", "{{ steps.tax_amount.output }}"], "digits": 0}),
    ("price", "resource.update", {"resource": "orders", "id": "{{ steps.order.output.id }}", "data": {
        "subtotal_minor": "{{ vars.subtotal }}", "tax_minor": "{{ steps.tax_amount.output | int }}", "total_minor": "{{ steps.total.output | int }}"}}),
    ("ref", "util.id", {"kind": "token", "length": 12}),
    ("payment", "resource.create", {"resource": "payments", "data": {"store_id": "{{ auth.org }}", "order_id": "{{ steps.order.output.id }}",
        "provider": body("method"), "method": body("method"), "reference": "POS-{{ steps.ref.output }}",
        "currency": "{{ steps.store.output.data.0.currency }}", "amount_minor": "{{ steps.total.output | int }}", "status": "pending"}}),
    ("till_total", "math.calculate", {"operation": "add", "values": ["{{ steps.session.output.total_sales_minor | default:0 }}", "{{ steps.total.output }}"], "digits": 0}),
    ("till_count", "math.calculate", {"operation": "add", "values": ["{{ steps.session.output.order_count | default:0 }}", 1], "digits": 0}),
    ("till", "resource.update", {"resource": "pos_sessions", "id": body("session_id"), "data": {
        "total_sales_minor": "{{ steps.till_total.output | int }}", "order_count": "{{ steps.till_count.output | int }}"}}),
    ("captured", "event.emit", {"event": "payment.captured", "payload": {"payment_id": "{{ steps.payment.output.id }}", "order_id": "{{ steps.order.output.id }}", "fee_minor": 0}}),
    ("reply", "response.return", {"status": 201, "body": {"order": "{{ steps.price.output }}", "payment": "{{ steps.payment.output }}"}}),
], [("http", "session"), ("session", "no_session", "missing"), ("session", "foreign"), ("foreign", "not_yours", "true"), ("foreign", "closed", "false"),
    ("closed", "not_open", "true"), ("closed", "store", "false"), ("store", "number"), ("number", "now"), ("now", "start"), ("start", "order"),
    ("order", "each"), ("each", "variant", "each"), ("variant", "product"), ("product", "line_total"), ("line_total", "line"), ("line", "add"),
    ("add", "keep"), ("each", "tax", "done"), ("tax", "tax_raw"), ("tax_raw", "tax_amount"), ("tax_amount", "total"), ("total", "price"),
    ("price", "ref"), ("ref", "payment"), ("payment", "till_total"), ("till_total", "till_count"), ("till_count", "till"), ("till", "captured"),
    ("captured", "reply")], timeout=120))

# Help desk -----------------------------------------------------------------
FLOWS.append(flow("open_ticket", "POST /storefront/support: a shopper contacts a store.", [
    ("http", "trigger.http", {}),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": body("store_id")}, "limit": 1}),
    ("none", "control.if", {"condition": {"not": {"truthy": "$steps.store.output.total"}}}),
    err("missing", 404, "store_not_found", "No such store"),
    ("token", "util.id", {"kind": "token", "length": 32}),
    ("now", "time.now", {}),
    ("ticket", "resource.create", {"resource": "tickets", "data": {"store_id": body("store_id"), "token": "{{ steps.token.output }}",
        "user_id": "{{ auth.user_id }}", "customer_name": body("name"), "customer_email": "{{ input.body.email | lower }}",
        "subject": body("subject"), "order_number": body("order_number"), "status": "open", "last_message_at": "{{ steps.now.output }}"}}),
    ("message", "resource.create", {"resource": "ticket_messages", "data": {"store_id": body("store_id"), "ticket_id": "{{ steps.ticket.output.id }}",
        "user_id": "{{ auth.user_id }}", "from_staff": False, "author_name": body("name"), "body": body("message")}}),
    ("ack", "mail.send", {"to": body("email"), "template": "ticket_received", "data": {"ticket": "{{ steps.ticket.output }}", "store": "{{ steps.store.output.data.0 }}"}}),
    ("notify", "resource.create", {"resource": "notifications", "data": {"store_id": body("store_id"), "kind": "ticket",
        "title": "New message: {{ input.body.subject }}", "body": "From {{ input.body.name }}", "url": "/help-desk/{{ steps.ticket.output.id }}", "level": "info"}}),
    ("live", "realtime.publish", {"channel": "store:{{ input.body.store_id }}", "event": "ticket.opened", "payload": {"ticket_id": "{{ steps.ticket.output.id }}", "subject": body("subject")}}),
    ("reply", "response.return", {"status": 201, "body": {"ticket": "{{ steps.ticket.output.id }}", "token": "{{ steps.token.output }}"}}),
], [("http", "store"), ("store", "none"), ("none", "missing", "true"), ("none", "token", "false"), ("token", "now"), ("now", "ticket"),
    ("ticket", "message"), ("message", "ack"), ("ack", "notify"), ("notify", "live"), ("live", "reply")]))

FLOWS.append(flow("customer_ticket_reply", "POST /storefront/support/{token}/messages: the shopper replies using their ticket link.", [
    ("http", "trigger.http", {}),
    ("find", "resource.list", {"resource": "tickets", "filters": {"token": "{{ input.params.token }}"}, "limit": 1}),
    ("none", "control.if", {"condition": {"not": {"truthy": "$steps.find.output.total"}}}),
    err("missing", 404, "ticket_not_found", "No such conversation"),
    ("now", "time.now", {}),
    ("message", "resource.create", {"resource": "ticket_messages", "data": {"store_id": "{{ steps.find.output.data.0.store_id }}",
        "ticket_id": "{{ steps.find.output.data.0.id }}", "from_staff": False, "author_name": "{{ steps.find.output.data.0.customer_name }}", "body": body("message")}}),
    ("reopen", "resource.update", {"resource": "tickets", "id": "{{ steps.find.output.data.0.id }}", "data": {"status": "open", "last_message_at": "{{ steps.now.output }}"}}),
    ("live", "realtime.publish", {"channel": "store:{{ steps.find.output.data.0.store_id }}", "event": "ticket.message", "payload": {"ticket_id": "{{ steps.find.output.data.0.id }}"}}),
    ("reply", "response.return", {"status": 201, "body": "{{ steps.message.output }}"}),
], [("http", "find"), ("find", "none"), ("none", "missing", "true"), ("none", "now", "false"), ("now", "message"), ("message", "reopen"), ("reopen", "live"), ("live", "reply")]))

FLOWS.append(flow("staff_reply", "POST /support/tickets/{id}/reply: staff answer a ticket (optionally closing it) and email the shopper.", [
    ("http", "trigger.http", {}),
    ("ticket", "resource.get", {"resource": "tickets", "id": "{{ input.params.id }}"}),
    err("missing", 404, "ticket_not_found", "No such ticket"),
    own_store("foreign", "ticket.output"),
    err("not_yours", 404, "ticket_not_found", "No such ticket"),
    ("now", "time.now", {}),
    ("message", "resource.create", {"resource": "ticket_messages", "data": {"store_id": "{{ steps.ticket.output.store_id }}",
        "ticket_id": "{{ steps.ticket.output.id }}", "user_id": "{{ auth.user_id }}", "from_staff": True, "author_name": "{{ auth.email }}", "body": body("message")}}),
    ("closing", "control.if", {"condition": {"eq": ["$input.body.close", True]}}),
    ("close", "resource.update", {"resource": "tickets", "id": "{{ steps.ticket.output.id }}", "data": {"status": "closed", "closed_at": "{{ steps.now.output }}", "last_message_at": "{{ steps.now.output }}"}}),
    ("pending", "resource.update", {"resource": "tickets", "id": "{{ steps.ticket.output.id }}", "data": {"status": "pending", "last_message_at": "{{ steps.now.output }}"}}),
    ("mail", "mail.send", {"to": "{{ steps.ticket.output.customer_email }}", "template": "support_reply", "data": {
        "ticket": "{{ steps.ticket.output }}", "message": body("message")}}),
    ("reply", "response.return", {"status": 201, "body": "{{ steps.message.output }}"}),
], [("http", "ticket"), ("ticket", "missing", "missing"), ("ticket", "foreign"), ("foreign", "not_yours", "true"), ("foreign", "now", "false"),
    ("now", "message"), ("message", "closing"), ("closing", "close", "true"), ("closing", "pending", "false"), ("close", "mail"), ("pending", "mail"), ("mail", "reply")]))

# Reviews ---------------------------------------------------------------------
FLOWS.append(flow("submit_review", "POST /storefront/reviews: a signed-in shopper reviews a product once; verified if they bought it.", [
    ("http", "trigger.http", {}),
    ("product", "resource.get", {"resource": "products", "id": body("product_id")}),
    err("missing", 404, "product_not_found", "No such product"),
    ("dupe", "resource.list", {"resource": "reviews", "filters": {"product_id": body("product_id"), "user_id": "{{ auth.user_id }}"}, "limit": 1}),
    ("reviewed", "control.if", {"condition": {"truthy": "$steps.dupe.output.total"}}),
    err("once", 409, "already_reviewed", "You've already reviewed this product"),
    ("bought", "db.query", {"sql": """SELECT COUNT(*) AS n FROM order_items oi JOIN orders o ON o.id = oi.order_id
WHERE o.user_id = ? AND oi.product_id = ? AND o.payment_status IN ('paid', 'partially_refunded')""", "params": ["{{ auth.user_id }}", body("product_id")]}),
    ("verified", "control.if", {"condition": {"gt": ["$steps.bought.output.0.n", 0]}}),
    ("yes", "control.set", {"values": {"verified": True}}),
    ("no", "control.set", {"values": {"verified": False}}),
    ("review", "resource.create", {"resource": "reviews", "data": {"store_id": "{{ steps.product.output.store_id }}", "product_id": body("product_id"),
        "user_id": "{{ auth.user_id }}", "author_name": "{{ auth.email }}", "rating": body("rating"), "title": body("title"), "body": body("body"),
        "status": "pending", "verified_purchase": "{{ vars.verified }}"}}),
    ("notify", "resource.create", {"resource": "notifications", "data": {"store_id": "{{ steps.product.output.store_id }}", "kind": "review",
        "title": "New {{ input.body.rating }}★ review for {{ steps.product.output.title }}", "url": "/reviews/{{ steps.review.output.id }}", "level": "info"}}),
    ("reply", "response.return", {"status": 201, "body": "{{ steps.review.output }}"}),
], [("http", "product"), ("product", "missing", "missing"), ("product", "dupe"), ("dupe", "reviewed"), ("reviewed", "once", "true"),
    ("reviewed", "bought", "false"), ("bought", "verified"), ("verified", "yes", "true"), ("verified", "no", "false"), ("yes", "review"), ("no", "review"),
    ("review", "notify"), ("notify", "reply")]))

FLOWS.append(flow("review_rollup", "When a review is moderated, recompute the product's average rating and count.", [
    ("changed", "trigger.resource", {"resource": "reviews", "operations": ["updated", "deleted"]}),
    ("stats", "db.query", {"sql": """SELECT COALESCE(AVG(rating), 0) AS avg, COUNT(*) AS n FROM reviews WHERE product_id = ? AND status = 'approved'""",
        "params": [f"{{{{ {R}.product_id }}}}"]}),
    ("round", "math.calculate", {"operation": "round", "values": ["{{ steps.stats.output.0.avg }}"], "digits": 1}),
    ("save", "resource.update", {"resource": "products", "id": f"{{{{ {R}.product_id }}}}", "data": {
        "rating_avg": "{{ steps.round.output }}", "rating_count": "{{ steps.stats.output.0.n | int }}"}}),
], [("changed", "stats"), ("stats", "round"), ("round", "save")]))

# Guest order lookup ------------------------------------------------------------
FLOWS.append(flow("order_status", "GET /storefront/orders/{reference}?email=: a guest checks an order with its payment reference and email.", [
    ("http", "trigger.http", {}),
    ("find", "resource.list", {"resource": "orders", "filters": {"payment_reference": "{{ input.params.reference }}"}, "limit": 1}),
    ("match", "control.if", {"condition": {"all": [{"truthy": "$steps.find.output.total"},
        {"eq": ["$steps.find.output.data.0.email", "$input.query.email"]}]}}),
    err("missing", 404, "order_not_found", "No order matches that reference and email"),
    ("items", "resource.list", {"resource": "order_items", "filters": {"order_id": "{{ steps.find.output.data.0.id }}"}, "limit": 200}),
    ("events", "resource.list", {"resource": "order_events", "filters": {"order_id": "{{ steps.find.output.data.0.id }}", "is_customer_visible": True}, "sort": "created_at", "limit": 100}),
    ("reply", "response.return", {"body": {"order": "{{ steps.find.output.data.0 }}", "items": "{{ steps.items.output.data }}", "timeline": "{{ steps.events.output.data }}"}}),
], [("http", "find"), ("find", "match"), ("match", "missing", "false"), ("match", "items", "true"), ("items", "events"), ("events", "reply")]))

# Analytics and payouts -------------------------------------------------------
FLOWS.append(flow("daily_rollup", "Nightly at 00:10 UTC: yesterday's sales per store into daily_stats (idempotent).", [
    ("nightly", "trigger.schedule", {"cron": "10 0 * * *"}),
    ("manual", "trigger.manual", {}),
    ("day", "time.now", {"format": "date", "offset_seconds": -86400}),
    ("per_store", "db.query", {"sql": """SELECT o.store_id, COUNT(*) AS orders, COALESCE(SUM(o.total_minor), 0) AS gross, COALESCE(SUM(o.discount_minor), 0) AS discounts,
       COALESCE(SUM(o.refunded_minor), 0) AS refunds, COALESCE(SUM(o.total_minor - o.refunded_minor), 0) AS net,
       COALESCE((SELECT SUM(oi.quantity) FROM order_items oi JOIN orders o2 ON o2.id = oi.order_id
                 WHERE o2.store_id = o.store_id AND o2.payment_status IN ('paid', 'partially_refunded', 'refunded') AND CAST(o2.placed_at AS TEXT) LIKE ?), 0) AS items
FROM orders o WHERE o.payment_status IN ('paid', 'partially_refunded', 'refunded') AND CAST(o.placed_at AS TEXT) LIKE ?
GROUP BY o.store_id""", "params": ["{{ steps.day.output }}%", "{{ steps.day.output }}%"]}),
    ("each", "control.foreach", {"items": "{{ steps.per_store.output }}"}),
    ("exists", "resource.list", {"resource": "daily_stats", "filters": {"store_id": "{{ item.store_id }}", "day": "{{ steps.day.output }}"}, "limit": 1}),
    ("fresh", "control.if", {"condition": {"not": {"truthy": "$steps.exists.output.total"}}}),
    ("newcomers", "db.query", {"sql": "SELECT COUNT(*) AS n FROM customers WHERE store_id = ? AND CAST(first_order_at AS TEXT) LIKE ?",
        "params": ["{{ item.store_id }}", "{{ steps.day.output }}%"]}),
    ("avg", "math.calculate", {"operation": "divide", "values": ["{{ item.gross }}", "{{ item.orders }}"], "digits": 0}),
    ("save", "resource.create", {"resource": "daily_stats", "data": {"store_id": "{{ item.store_id }}", "day": "{{ steps.day.output }}",
        "orders": "{{ item.orders }}", "gross_minor": "{{ item.gross | int }}", "discounts_minor": "{{ item.discounts | int }}",
        "refunds_minor": "{{ item.refunds | int }}", "net_minor": "{{ item.net | int }}", "items_sold": "{{ item.items | int }}",
        "new_customers": "{{ steps.newcomers.output.0.n | int }}", "average_order_minor": "{{ steps.avg.output | int }}"}}),
], [("nightly", "day"), ("manual", "day"), ("day", "per_store"), ("per_store", "each"), ("each", "exists", "each"), ("exists", "fresh"),
    ("fresh", "newcomers", "true"), ("newcomers", "avg"), ("avg", "save")]))

FLOWS.append(flow("payout_run", "Daily at 06:00 UTC: move each store's positive balance into a pending payout.", [
    ("daily", "trigger.schedule", {"cron": "0 6 * * *"}),
    ("manual", "trigger.manual", {}),
    ("today", "time.now", {"format": "date"}),
    ("balances", "db.query", {"sql": """SELECT store_id, currency, SUM(amount_minor) AS balance FROM ledger_entries
GROUP BY store_id, currency HAVING SUM(amount_minor) > 0""", "params": []}),
    ("each", "control.foreach", {"items": "{{ steps.balances.output }}"}),
    ("payout", "resource.create", {"resource": "payouts", "data": {"store_id": "{{ item.store_id }}", "amount_minor": "{{ item.balance | int }}",
        "currency": "{{ item.currency }}", "status": "pending", "period_end": "{{ steps.today.output }}"}}),
    ("neg", "math.calculate", {"operation": "multiply", "values": ["{{ item.balance }}", -1], "digits": 0}),
    ("ledger", "resource.create", {"resource": "ledger_entries", "data": {"store_id": "{{ item.store_id }}", "kind": "payout",
        "amount_minor": "{{ steps.neg.output | int }}", "currency": "{{ item.currency }}", "payout_id": "{{ steps.payout.output.id }}",
        "description": "Payout {{ steps.payout.output.id }}"}}),
    ("store", "resource.list", {"resource": "stores", "filters": {"slug": "{{ item.store_id }}"}, "limit": 1}),
    ("mail", "mail.send", {"to": "{{ steps.store.output.data.0.email }}", "template": "payout_initiated", "data": {"payout": "{{ steps.payout.output }}", "store": "{{ steps.store.output.data.0 }}"}}),
    ("notify", "resource.create", {"resource": "notifications", "data": {"store_id": "{{ item.store_id }}", "kind": "payout",
        "title": "Payout of {{ item.currency }} {{ item.balance }} on its way", "url": "/finance/payouts", "level": "success"}}),
], [("daily", "today"), ("manual", "today"), ("today", "balances"), ("balances", "each"), ("each", "payout", "each"), ("payout", "neg"),
    ("neg", "ledger"), ("ledger", "store"), ("store", "mail"), ("mail", "notify")]))

SCHEMAS.append({"name": "PosSale", "description": "An in-person sale on an open till.", "fields": [
    F("session_id", "integer", required=True), F("method", required=True, enum=["cash", "pos_card", "mobile_money", "bank_transfer"]),
    F("email", "email"), F("lines", "array", required=True, items={"name": "line", "type": "object", "fields": [
        F("variant_id", "integer", required=True), F("quantity", "integer", required=True, minimum=1, maximum=999)]})]})


FLOWS.append(flow("merchant_inventory", "GET /merchant/inventory: every variant of the active store with cost, stock, reservations, availability and margin. ?low=1 for low stock only.", [
    ("http", "trigger.http", {}),
    ("rows", "db.query", {"sql": """SELECT v.id AS variant_id, v.product_id, p.title AS product, p.status AS product_status, v.title AS variant, v.sku, v.barcode,
       v.price_minor, v.cost_minor, v.price_minor - COALESCE(v.cost_minor, 0) AS margin_minor,
       v.stock, v.reserved, v.stock - v.reserved AS available, v.low_stock_threshold, v.track_inventory, v.allow_backorder,
       CASE WHEN v.track_inventory = ? AND v.stock <= v.low_stock_threshold THEN 1 ELSE 0 END AS is_low
FROM product_variants v JOIN products p ON p.id = v.product_id
WHERE v.store_id = ? AND (? = '' OR (v.track_inventory = ? AND v.stock <= v.low_stock_threshold))
ORDER BY p.title, v.position""", "params": [True, "{{ auth.org }}", "{{ input.query.low | default:\"\" }}", True]}),
    ("reply", "response.return", {"body": {"store_id": "{{ auth.org }}", "variants": "{{ steps.rows.output }}", "count": "{{ steps.rows.output | length }}"}}),
], [("http", "rows"), ("rows", "reply")]))

FLOWS.append(flow("merchant_order", "GET /merchant/orders/{id}: the full order for the back office: lines, timeline, payments, refunds, customer, risk.", [
    ("http", "trigger.http", {}),
    ("order", "db.query", {"sql": "SELECT * FROM orders WHERE id = ? AND store_id = ?", "params": ["{{ input.params.id }}", "{{ auth.org }}"]}),
    ("none", "control.if", {"condition": {"empty": "$steps.order.output"}}),
    err("missing", 404, "order_not_found", "No such order"),
    ("items", "db.query", {"sql": "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", "params": ["{{ input.params.id }}"]}),
    ("events", "db.query", {"sql": "SELECT * FROM order_events WHERE order_id = ? ORDER BY id", "params": ["{{ input.params.id }}"]}),
    ("payments", "db.query", {"sql": "SELECT * FROM payments WHERE order_id = ? ORDER BY id", "params": ["{{ input.params.id }}"]}),
    ("refunds", "db.query", {"sql": "SELECT * FROM refunds WHERE order_id = ? ORDER BY id", "params": ["{{ input.params.id }}"]}),
    ("customer", "db.query", {"sql": "SELECT * FROM customers WHERE id = ?", "params": ["{{ steps.order.output.0.customer_id | default:0 }}"]}),
    ("reply", "response.return", {"body": {"order": "{{ steps.order.output.0 }}", "items": "{{ steps.items.output }}", "timeline": "{{ steps.events.output }}",
        "payments": "{{ steps.payments.output }}", "refunds": "{{ steps.refunds.output }}", "customer": "{{ steps.customer.output.0 }}"}}),
], [("http", "order"), ("order", "none"), ("none", "missing", "true"), ("none", "items", "false"), ("items", "events"), ("events", "payments"),
    ("payments", "refunds"), ("refunds", "customer"), ("customer", "reply")]))


# ── routes ───────────────────────────────────────────────────────────────────

SHOP, MERCHANT, FIN = ["Storefront"], ["Merchant"], ["Finance"]
ROUTES = [
    route("POST", "/storefront/cart/items", "cart_add", "Add a variant to a cart (creates the cart when cart_token is omitted).", "public", "cart_add",
          tags=SHOP, input_fields=[*SCHEMAS[1]["fields"], F("email", "email")], rate={"limit": 120, "window": 60}),
    route("POST", "/storefront/cart/update", "cart_update", "Change a line's quantity; 0 removes it.", "public", "cart_update", tags=SHOP, input_schema="CartQuantity"),
    route("GET", "/storefront/cart/{token}", "cart_view", "A cart with its lines.", "public", "cart_view", tags=SHOP),
    route("POST", "/storefront/cart/discount", "apply_discount", "Apply a discount code.", "public", "apply_discount", tags=SHOP, input_schema="DiscountApply", rate={"limit": 30, "window": 60}),
    route("DELETE", "/storefront/cart/{token}/discount", "remove_discount", "Remove the discount code.", "public", "remove_discount", tags=SHOP),
    route("POST", "/storefront/checkout", "checkout", "Place an order from a cart.", "public", "checkout", tags=SHOP, input_schema="CheckoutInput", rate={"limit": 20, "window": 60}),
    route("GET", "/storefront/orders/{reference}", "order_status", "Look up an order by payment reference and ?email=.", "public", "order_status", tags=SHOP, rate={"limit": 30, "window": 60}),
    route("GET", "/storefront/recover/{token}", "recover_cart", "Reopen an abandoned cart.", "public", "recover_cart", tags=SHOP),
    route("POST", "/storefront/support", "open_ticket", "Contact a store.", "public", "open_ticket", tags=SHOP, input_schema="TicketInput", rate={"limit": 10, "window": 60}),
    route("POST", "/storefront/support/{token}/messages", "customer_ticket_reply", "Reply in a conversation.", "public", "customer_ticket_reply", tags=SHOP,
          input_fields=[F("message", "text", required=True, max_length=5000)], rate={"limit": 20, "window": 60}),
    route("POST", "/storefront/reviews", "submit_review", "Review a product.", "shopper", "submit_review", tags=SHOP, input_schema="ReviewInput", rate={"limit": 10, "window": 60}),
    route("POST", "/merchant/stores", "register_store", "Register the store for your organization.", "store_owner_setup", "register_store", tags=MERCHANT, input_schema="StoreSetup"),
    route("GET", "/merchant/dashboard", "store_dashboard", "Headline numbers for the active store.", "store_member", "store_dashboard", tags=MERCHANT, cache=30),
    route("GET", "/merchant/inventory", "merchant_inventory", "Full stock view for the active store (?low=1 for low stock).", "store_member", "merchant_inventory", tags=MERCHANT),
    route("GET", "/merchant/orders/{id}", "merchant_order", "The full order record for staff.", "store_member", "merchant_order", tags=MERCHANT),
    route("POST", "/orders/{id}/mark-paid", "mark_paid", "Record an offline payment.", "store_operator", "mark_paid", tags=MERCHANT, input_schema="MarkPaid"),
    route("POST", "/orders/{id}/fulfil", "fulfil_order", "Ship an order.", "store_operator", "fulfil_order", tags=MERCHANT, input_schema="FulfilInput"),
    route("POST", "/orders/{id}/cancel", "cancel_order", "Cancel an unpaid order.", "store_manager_route", "cancel_order", tags=MERCHANT, input_schema="CancelInput"),
    route("POST", "/orders/{id}/refunds", "refund_order", "Refund a paid order.", ["mfa", "store_manager_route"], "refund_order", tags=FIN, input_schema="RefundInput"),
    route("POST", "/inventory/adjust", "adjust_inventory", "Adjust stock with a reason.", "store_operator", "adjust_inventory", tags=MERCHANT, input_schema="InventoryAdjust"),
    route("POST", "/support/tickets/{id}/reply", "staff_reply", "Answer a ticket.", "store_operator", "staff_reply", tags=MERCHANT, input_schema="TicketReply"),
    route("POST", "/pos/sales", "pos_sale", "Ring up an in-person sale.", "store_operator", "pos_sale", tags=["POS"], input_schema="PosSale"),
]
POLICIES.append({"name": "store_manager_route", "description": "Owners and admins of the active organization (for routes).",
                 "condition": {"all": [{"exists": "$auth.org"}, {"in": ["$auth.org_role", MANAGER_ROLES]}]}})


# ── mail ─────────────────────────────────────────────────────────────────────

def mail(name, subject, html):
    wrapped = ('<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1f1b2e">'
               '<div style="padding:18px 22px;border-radius:14px 14px 0 0;background:#cfc4fa"><b>{{ store.name | default(store) }}</b></div>'
               f'<div style="padding:22px;border:1px solid #e7e3ef;border-top:0;border-radius:0 0 14px 14px">{html}</div></div>')
    text = html.replace("<p>", "").replace("</p>", "\n").replace("<b>", "").replace("</b>", "")
    return {"name": name, "description": "", "subject": subject, "html": wrapped, "text": text}


MAIL = [
    mail("order_confirmation", "Order #{{ order.number }} confirmed",
         "<p>Thanks for your order!</p><p>Order <b>#{{ order.number }}</b> · {{ order.currency }} {{ order.total_minor }}</p>"
         "{% for item in items %}<p>{{ item.quantity }} × {{ item.title }} {{ item.variant_title or '' }}</p>{% endfor %}"),
    mail("shipping_update", "Your order #{{ order.number }} is on its way",
         "<p>Good news: order <b>#{{ order.number }}</b> has shipped{% if order.carrier %} with {{ order.carrier }}{% endif %}.</p>"
         "{% if order.tracking_url %}<p>Track it: {{ order.tracking_url }}</p>{% endif %}"),
    mail("order_cancelled", "Order #{{ order.number }} was cancelled", "<p>Your order <b>#{{ order.number }}</b> has been cancelled.</p>"),
    mail("refund_issued", "Refund for order #{{ order.number }}",
         "<p>We've refunded <b>{{ refund.currency }} {{ refund.amount_minor }}</b> for order #{{ order.number }}.</p><p>It can take a few days to reach you.</p>"),
    mail("cart_recovery", "You left something in your cart",
         "<p>Your cart ({{ items }} item(s), {{ currency }} {{ value_minor }}) is waiting.</p><p>Pick up where you left off: /recover/{{ token }}</p>"),
    mail("low_stock", "Low stock: {{ product }}", "<p><b>{{ product }}</b> ({{ variant }}, SKU {{ sku }}) is down to <b>{{ stock }}</b>.</p>"),
    mail("ticket_received", "We got your message: {{ ticket.subject }}", "<p>Hi {{ ticket.customer_name }}, thanks for reaching out. We'll reply soon.</p>"),
    mail("support_reply", "Re: {{ ticket.subject }}", "<p>{{ message }}</p>"),
    mail("store_welcome", "Welcome to Sell4Me, {{ store.name }}", "<p>Your store <b>{{ store.name }}</b> is ready. Add products, set shipping, and share your link.</p>"),
    mail("payout_initiated", "Payout on its way", "<p>A payout of <b>{{ payout.currency }} {{ payout.amount_minor }}</b> has been initiated.</p>"),
]


# ── the rest of the system ───────────────────────────────────────────────────

BUCKETS = [
    {"name": "product-media", "description": "Product photos and video. Upload under <store>/…", "public": True, "read_policy": None,
     "write_policy": {"all": [{"exists": "$auth.org"}, {"in": ["$auth.org_role", EDITOR_ROLES]}, {"eq": ["$object.segments.0", "$auth.org"]}]},
     "accepts": ["image/*", "video/mp4"], "max_bytes": 15 * 1024 * 1024, "signed_uploads": True},
    {"name": "store-assets", "description": "Logos, banners and downloadable files. Upload under <store>/…", "public": True, "read_policy": None,
     "write_policy": {"all": [{"exists": "$auth.org"}, {"in": ["$auth.org_role", MANAGER_ROLES]}, {"eq": ["$object.segments.0", "$auth.org"]}]},
     "accepts": ["image/*", "application/pdf"], "max_bytes": 10 * 1024 * 1024, "signed_uploads": True},
    {"name": "exports", "description": "CSV exports, readable by the store's staff under <store>/…", "public": False,
     "read_policy": {"all": [{"exists": "$auth.org"}, {"eq": ["$object.segments.0", "$auth.org"]}]}, "write_policy": "service",
     "accepts": ["text/csv", "application/json"], "max_bytes": 50 * 1024 * 1024, "signed_uploads": True},
]

SUBSCRIPTIONS = [
    {"name": "platform_new_stores", "description": "Live feed of new stores for platform operators.", "event": "store.created",
     "target_type": "realtime", "target": "platform:stores", "condition": None, "enabled": True},
]

WEBHOOKS = [
    {"name": "erp_sync", "description": "Send paid, refunded and cancelled orders to your ERP.", "url": "https://erp.example.com/hooks/sell4me",
     "events": ["order.paid", "order.refunded", "order.cancelled", "order.fulfilled"], "headers": {}, "enabled": False, "max_attempts": 8},
    {"name": "analytics_warehouse", "description": "Stream order and customer changes to your warehouse.", "url": "https://warehouse.example.com/ingest",
     "events": ["orders.*", "customers.*", "payments.*"], "headers": {"X-Source": "sell4me"}, "enabled": False, "max_attempts": 5},
]

INBOUND = [
    {"slug": "payment-gateway", "name": "Payment gateway", "description": "Charge confirmations from your payment provider.",
     "verification": "hmac-sha256", "signature_header": "x-signature", "target_type": "flow", "target": "payment_gateway", "enabled": True},
]

SCHEDULES = [
    {"name": "weekly_merchant_digest", "description": "Monday 08:00 UTC: an event your own digest flow or webhook can consume.",
     "cron": "0 8 * * 1", "interval_seconds": None, "target_type": "event", "target": "merchant.weekly_digest", "payload": {"window": "7d"}, "enabled": True},
]

SETTINGS = {
    "public_docs": True,
    "realtime": {
        "allow_client_publish": False,
        "channels": [
            {"pattern": "store:{{ auth.org }}", "subscribe": {"all": [{"exists": "$auth.org"}, {"in": ["$auth.org_role", STAFF_ROLES]}]},
             "publish": "deny", "presence": True, "history": 100},
            {"pattern": "platform:*", "subscribe": "platform_admin", "publish": "deny", "presence": False, "history": 50},
            {"pattern": "resource:*", "subscribe": "platform_admin", "publish": "deny", "presence": False, "history": 0},
        ],
    },
}

AUTH = {
    "signup_enabled": True, "require_email_verification": False, "password_policy": "basic", "password_min_length": 8,
    "access_ttl": 900, "refresh_ttl": 2592000, "magic_link_enabled": True, "mfa_enabled": True, "default_roles": ["customer"],
}


# ── sample data: one demo store ──────────────────────────────────────────────

DEMO = "sell4me-demo"
IMG = "https://images.unsplash.com/photo-{}?w=1200&q=80"
PRODUCTS = [
    # id, title, handle, type, price (kobo), compare, image, options {name: [values]}, tags
    (1, "Ankara Wrap Dress", "ankara-wrap-dress", "Dresses", 3_500_000, 4_200_000, "1515886657613-9f3515b0c78f", {"Size": ["S", "M", "L"]}, ["ankara", "new"]),
    (2, "Adire Silk Scarf", "adire-silk-scarf", "Accessories", 1_200_000, None, "1601924994987-69e26d50dc26", {"Colour": ["Indigo", "Rust"]}, ["adire", "gift"]),
    (3, "Leather Tote Bag", "leather-tote-bag", "Bags", 4_800_000, 5_500_000, "1548036328-c9fa89d128fa", {"Colour": ["Tan", "Black"]}, ["leather", "bestseller"]),
    (4, "Kaftan Shirt", "kaftan-shirt", "Menswear", 2_600_000, None, "1521572163474-6864f9cf17ab", {"Size": ["M", "L", "XL"]}, ["menswear"]),
    (5, "Beaded Bracelet Set", "beaded-bracelet-set", "Jewellery", 650_000, 800_000, "1611591437281-460bfbe1220a", {}, ["jewellery", "gift"]),
    (6, "Shea Body Butter", "shea-body-butter", "Beauty", 450_000, None, "1608248597279-f99d160bfcbc", {"Size": ["200ml", "400ml"]}, ["beauty", "organic"]),
    (7, "Aso Oke Cap", "aso-oke-cap", "Menswear", 900_000, None, "1521369909029-2afed882baee", {}, ["menswear", "traditional"]),
    (8, "Woven Raffia Hat", "woven-raffia-hat", "Accessories", 1_500_000, 1_800_000, "1514327605112-b887c0e61c0a", {}, ["summer", "new"]),
]


def sample_data():
    now = "2026-09-01T09:00:00+00:00"
    data = defaultdict(list)
    data["stores"].append({"id": 1, "slug": DEMO, "name": "Naija Threads", "email": "hello@naijathreads.example", "phone": "+234 800 555 0100",
                           "currency": "NGN", "country": "NG", "timezone": "Africa/Lagos", "status": "active", "plan": "growth",
                           "description": "Contemporary African fashion, made in Lagos.", "order_prefix": "#", "platform_fee_bps": 150})
    data["themes"].append({"id": 1, "store_id": DEMO, "name": "Lagos Sunset", "is_active": True,
                           "colors": {"primary": "#1f1b2e", "accent": "#e07a3f", "background": "#fffaf5"},
                           "fonts": {"heading": "Playfair Display", "body": "Inter"}, "announcement": "Free delivery in Lagos on orders over ₦50,000",
                           "sections": [{"type": "hero", "title": "New season, new colour"}, {"type": "featured_collection", "collection": "new-arrivals"},
                                        {"type": "product_grid", "collection": "bestsellers"}, {"type": "newsletter"}],
                           "header_links": [{"label": "Shop", "url": "/collections/all"}, {"label": "About", "url": "/pages/about"}],
                           "footer_links": [{"label": "Shipping", "url": "/pages/shipping"}, {"label": "Returns", "url": "/pages/returns"}]})
    for i, (title, handle, body_text) in enumerate([
        ("About us", "about", "Naija Threads designs contemporary African fashion in Lagos."),
        ("Shipping", "shipping", "Lagos: 1–2 days. Nationwide: 2–5 days. West Africa: 5–10 days."),
        ("Returns", "returns", "Returns within 14 days of delivery, unworn with tags.")], start=1):
        data["pages"].append({"id": i, "store_id": DEMO, "title": title, "handle": handle, "body": body_text, "is_published": True})

    variant_id = 0
    image_id = 0
    for pid, title, handle, ptype, price, compare, photo, options, tags in PRODUCTS:
        data["products"].append({"id": pid, "store_id": DEMO, "title": title, "handle": handle, "product_type": ptype, "vendor": "Naija Threads",
                                 "summary": f"{title}, handmade in Lagos.", "description": f"<p>{title}. Ethically made, in small batches.</p>",
                                 "status": "active", "tags": tags, "price_from_minor": price, "image_url": IMG.format(photo),
                                 "rating_count": 0, "view_count": 0, "cart_count": 0, "purchase_count": 0, "published_at": now})
        image_id += 1
        data["product_images"].append({"id": image_id, "store_id": DEMO, "product_id": pid, "url": IMG.format(photo), "alt": title, "position": 0})
        combos = [({}, "Default")] if not options else [({name: value}, value) for name, values in options.items() for value in values]
        for position, (combo, vtitle) in enumerate(combos):
            variant_id += 1
            data["product_variants"].append({
                "id": variant_id, "store_id": DEMO, "product_id": pid, "title": vtitle, "options": combo or None,
                "sku": f"NT-{pid:02d}-{position + 1:02d}", "price_minor": price + (position * 50_000 if ptype == "Beauty" else 0),
                "compare_at_minor": compare, "cost_minor": int(price * 0.45), "stock": [24, 12, 6, 3][position % 4] + pid,
                "reserved": 0, "track_inventory": True, "allow_backorder": False, "low_stock_threshold": 5,
                "weight_grams": 300 if ptype != "Bags" else 900, "position": position, "is_default": position == 0})
        for position, (name, values) in enumerate(options.items()):
            data["product_options"].append({"id": len(data["product_options"]) + 1, "store_id": DEMO, "product_id": pid,
                                            "name": name, "values": values, "position": position})

    for cid, title, handle, members in [(1, "New arrivals", "new-arrivals", [1, 8, 2]), (2, "Bestsellers", "bestsellers", [3, 1, 4, 5]),
                                        (3, "Gifts under ₦15,000", "gifts", [2, 5, 6, 7])]:
        data["collections"].append({"id": cid, "store_id": DEMO, "title": title, "handle": handle, "kind": "manual", "is_published": True, "position": cid})
        for position, pid in enumerate(members):
            data["collection_products"].append({"id": len(data["collection_products"]) + 1, "store_id": DEMO, "collection_id": cid, "product_id": pid, "position": position})

    data["shipping_zones"] += [
        {"id": 1, "store_id": DEMO, "name": "Nigeria", "countries": ["NG"], "position": 0},
        {"id": 2, "store_id": DEMO, "name": "West Africa", "countries": ["GH", "BJ", "TG", "SN", "CI"], "position": 1},
        {"id": 3, "store_id": DEMO, "name": "International", "countries": ["GB", "US", "CA", "DE", "FR", "NL"], "position": 2}]
    data["shipping_rates"] += [
        {"id": 1, "store_id": DEMO, "zone_id": 1, "name": "Standard (2–5 days)", "kind": "price", "price_minor": 250_000, "max_subtotal_minor": 4_999_999, "delivery_estimate": "2–5 days", "is_active": True},
        {"id": 2, "store_id": DEMO, "zone_id": 1, "name": "Free delivery", "kind": "price", "price_minor": 0, "min_subtotal_minor": 5_000_000, "delivery_estimate": "2–5 days", "is_active": True},
        {"id": 3, "store_id": DEMO, "zone_id": 2, "name": "Regional courier", "kind": "flat", "price_minor": 1_500_000, "delivery_estimate": "5–10 days", "is_active": True},
        {"id": 4, "store_id": DEMO, "zone_id": 3, "name": "DHL Express (up to 2kg)", "kind": "weight", "price_minor": 4_500_000, "max_weight_grams": 2000, "delivery_estimate": "3–6 days", "is_active": True},
        {"id": 5, "store_id": DEMO, "zone_id": 3, "name": "DHL Express (heavy)", "kind": "weight", "price_minor": 7_500_000, "min_weight_grams": 2001, "delivery_estimate": "3–6 days", "is_active": True}]
    data["tax_rates"].append({"id": 1, "store_id": DEMO, "country": "NG", "name": "VAT", "rate_bps": 750, "applies_to_shipping": False})
    data["discounts"] += [
        {"id": 1, "store_id": DEMO, "code": "WELCOME15", "title": "15% off your first order", "kind": "percentage", "value": 1500,
         "maximum_discount_minor": 1_000_000, "usage_limit": 500, "per_customer_limit": 1, "usage_count": 0, "is_active": True},
        {"id": 2, "store_id": DEMO, "code": "NAIRA5000", "title": "₦5,000 off orders over ₦40,000", "kind": "fixed_amount", "value": 500_000,
         "minimum_order_minor": 4_000_000, "usage_count": 0, "is_active": True},
        {"id": 3, "store_id": DEMO, "code": "SHIPFREE", "title": "Free shipping", "kind": "free_shipping", "value": 0, "usage_count": 0, "is_active": True}]
    data["segments"] += [
        {"id": 1, "store_id": DEMO, "name": "VIP", "is_system": True, "rules": {"all": [{"field": "orders_count", "op": "gte", "value": 5}]}},
        {"id": 2, "store_id": DEMO, "name": "First-time buyers", "is_system": True, "rules": {"all": [{"field": "orders_count", "op": "eq", "value": 1}]}}]
    data["campaigns"].append({"id": 1, "store_id": DEMO, "name": "Detty December", "kind": "social", "status": "active", "discount_id": 1,
                              "budget_minor": 50_000_000, "spend_minor": 0, "orders_count": 0, "revenue_minor": 0, "recipients_count": 0})
    data["gift_cards"].append({"id": 1, "store_id": DEMO, "code": "GIFT-NT-2026-0001", "initial_minor": 2_000_000, "balance_minor": 2_000_000,
                               "currency": "NGN", "is_active": True, "note": "Launch giveaway"})
    data["pos_devices"].append({"id": 1, "store_id": DEMO, "label": "Lekki showroom till", "receipt_header": "Naija Threads · Lekki",
                                "receipt_footer": "Thank you! Returns within 14 days.", "print_receipt_auto": True, "is_active": True})
    return dict(data)


# ── assemble ─────────────────────────────────────────────────────────────────


def build():
    return {
        "format": "pawabase.blueprint",
        "version": 1,
        "name": "Sell4Me Commerce",
        "description": ("A multi-store commerce backend: catalog with variants and inventory, carts, discounts, checkout with shipping "
                        "zones and tax, payments with a ledger, fulfilment, cancellations, refunds with restock, abandoned-cart recovery, "
                        "point of sale, help desk, reviews, analytics and payouts. Stores are organizations; staff use org-scoped tokens."),
        "source": {"project": "sell4me", "env": "blueprint", "generator": "examples/blueprints/commerce/build.py"},
        "definitions": {
            "schemas": SCHEMAS, "transformers": TRANSFORMERS, "policies": POLICIES, "resources": RESOURCES,
            "mail-templates": MAIL, "flows": FLOWS, "routes": ROUTES, "buckets": BUCKETS, "subscriptions": SUBSCRIPTIONS,
            "webhooks": WEBHOOKS, "inbound-hooks": INBOUND, "schedules": SCHEDULES,
        },
        "roles": ROLES,
        "auth": AUTH,
        "settings": SETTINGS,
        "data": sample_data(),
    }


if __name__ == "__main__":
    blueprint = build()
    out = HERE / "commerce.blueprint.json"
    out.write_text(json.dumps(blueprint, indent=2, ensure_ascii=False) + "\n")
    counts = {k: len(v) for k, v in blueprint["definitions"].items()}
    rows = sum(len(v) for v in blueprint["data"].values())
    print(f"wrote {out.name}: {sum(counts.values())} definitions {counts}, {len(ROLES)} roles, {rows} sample rows")
