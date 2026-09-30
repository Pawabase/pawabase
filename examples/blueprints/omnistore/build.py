"""Build ``omnistore.blueprint.json`` — the OmniStore commerce blueprint.

Run:  python build.py            → writes the document next to this file
Then: Studio → New project → A blueprint (JSON) → choose the file.

The document is assembled from the modules beside it:

* ``model``       — roles, schemas, transformers, policies;
* ``resources_*`` — 53 resources across catalog, inventory, orders, engagement;
* ``flows_*``     — the flows that make several things happen together;
* ``routes_*``    — what a client actually calls;
* this file       — mail templates, storage buckets, subscriptions, webhooks,
  schedules, auth, settings and a demo store.
"""

from __future__ import annotations

import json
from collections import defaultdict
import sys
from pathlib import Path

HERE = Path(__file__).parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from model import POLICIES, ROLES, TRANSFORMERS  # noqa: E402
from schemas import SCHEMAS  # noqa: E402
from resources_catalog import RESOURCES as CATALOG  # noqa: E402
from resources_inventory import RESOURCES as INVENTORY  # noqa: E402
from resources_orders import RESOURCES as ORDERS  # noqa: E402
from resources_engagement import RESOURCES as ENGAGEMENT  # noqa: E402
from flows_cart import FLOWS as CART_FLOWS  # noqa: E402
from flows_checkout import FLOWS as CHECKOUT_FLOWS  # noqa: E402
from flows_inventory import FLOWS as INVENTORY_FLOWS  # noqa: E402
from flows_admin import FLOWS as ADMIN_FLOWS  # noqa: E402
from flows_system import FLOWS as SYSTEM_FLOWS  # noqa: E402
from routes_customer import ROUTES as CUSTOMER_ROUTES  # noqa: E402
from routes_admin import ROUTES as ADMIN_ROUTES  # noqa: E402

RESOURCES = [*CATALOG, *INVENTORY, *ORDERS, *ENGAGEMENT]
FLOWS = [*CART_FLOWS, *CHECKOUT_FLOWS, *INVENTORY_FLOWS, *ADMIN_FLOWS, *SYSTEM_FLOWS]
ROUTES = [*CUSTOMER_ROUTES, *ADMIN_ROUTES]


def mail(name, subject, html, description=""):
    """A mail template, wrapped in the storefront's plain shell."""
    wrapped = (
        '<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1f1b2e">'
        '<div style="padding:18px 22px;border-radius:14px 14px 0 0;background:#cfc4fa">'
        "<b>{{ store | default('OmniStore') }}</b></div>"
        f'<div style="padding:22px;border:1px solid #e7e3ef;border-top:0;border-radius:0 0 14px 14px">{html}</div></div>'
    )
    text = (
        html.replace("<p>", "")
        .replace("</p>", "\n")
        .replace("<b>", "")
        .replace("</b>", "")
        .replace("<br>", "\n")
    )
    return {"name": name, "description": description, "subject": subject, "html": wrapped, "text": text}


MAIL = [
    mail("order_receipt", "Order {{ order.reference }} confirmed",
         "<p>Thanks — we have your order.</p>"
         "<p><b>{{ order.reference }}</b> · {{ order.currency }} {{ order.total_minor }}</p>"
         "{% for line in lines %}<p>{{ line.quantity }} × {{ line.title }} {{ line.variant_title or '' }}</p>{% endfor %}"
         "<p>Payment status: {{ order.payment_status }}.</p>"),
    mail("order_cancelled", "Order {{ order.reference }} was cancelled",
         "<p>Your order <b>{{ order.reference }}</b> has been cancelled.</p>"),
    mail("shipping_update", "Your order {{ order.reference }} has shipped",
         "<p>Good news: <b>{{ order.reference }}</b> left us{% if shipment.carrier %} with {{ shipment.carrier }}{% endif %}.</p>"
         "{% if shipment.tracking_url %}<p>Track it: {{ shipment.tracking_url }}</p>{% endif %}"),
    mail("delivery_done", "Order {{ order.reference }} was delivered",
         "<p><b>{{ order.reference }}</b> has arrived.</p>"),
    mail("refund_issued", "Refund for order {{ order.reference }}",
         "<p>We refunded <b>{{ refund.currency }} {{ refund.amount_minor }}</b> for order {{ order.reference }}.</p>"
         "<p>It can take a few days to reach you.</p>"),
    mail("return_decision", "Your return {{ return.code }}: {{ return.status }}",
         "<p>Your return <b>{{ return.code }}</b> is now <b>{{ return.status }}</b>.</p>"),
    mail("cart_recovery", "You left something in your basket",
         "<p>Your basket ({{ items }} item(s), {{ currency }} {{ value_minor }}) is still here.</p>"
         "<p>Pick up where you left off: /storefront/cart/{{ token }}</p>"),
    mail("low_stock", "Low stock: {{ product }}",
         "<p><b>{{ product }}</b> ({{ variant }}) is down to <b>{{ stock }}</b>.</p>"),
    mail("back_in_stock", "{{ product }} is back",
         "<p>Good news: <b>{{ product }}</b> is available again.</p>"),
    mail("support_reply", "Re: {{ ticket.subject }}", "<p>{{ message }}</p>"),
]


BUCKETS = [
    {"name": "product-media", "description": "Product photos and video. Upload under <store>/…",
     "public": True, "read_policy": None, "accepts": ["image/*", "video/mp4"],
     "max_bytes": 15 * 1024 * 1024, "signed_uploads": True},
    {"name": "store-assets", "description": "Logos, banners and downloadable files. Upload under <store>/…",
     "public": True, "read_policy": None, "accepts": ["image/*", "application/pdf"],
     "max_bytes": 10 * 1024 * 1024, "signed_uploads": True},
    {"name": "exports", "description": "CSV exports for the store's staff.",
     "public": False, "read_policy": "store_staff", "write_policy": "service",
     "accepts": ["text/csv", "application/json"], "max_bytes": 50 * 1024 * 1024,
     "signed_uploads": True},
    {"name": "support-attachments", "description": "Attachments a shopper or agent adds to a ticket.",
     "public": False, "read_policy": "customer_or_store", "write_policy": "authenticated",
     "accepts": ["image/*", "application/pdf", "text/plain"], "max_bytes": 20 * 1024 * 1024,
     "signed_uploads": True},
]

SUBSCRIPTIONS = [
    {"name": "store_live_orders", "description": "Order activity on the store's live channel.",
     "event": "order.*", "target_type": "realtime", "target": "store:{{ event.store_id }}",
     "condition": None, "enabled": True},
    {"name": "notification_fanout", "description": "Deliver a notification row to the person it is for.",
     "event": "notifications.created", "target_type": "realtime", "target": "user:{{ event.user_id }}",
     "condition": None, "enabled": True},
    {"name": "stock_low_to_staff", "description": "Low-stock events for the people who reorder.",
     "event": "stock.low", "target_type": "realtime", "target": "store:{{ event.store_id }}",
     "condition": None, "enabled": True},
]

WEBHOOKS = [
    {"name": "erp_sync", "description": "Send paid, fulfilled and refunded orders to the store's ERP.",
     "url": "https://erp.example.com/hooks/omnistore", "events": ["order.paid", "order.fulfilled", "order.refunded"],
     "headers": {}, "enabled": False, "max_attempts": 8},
    {"name": "warehouse_stream", "description": "Stream order and stock changes to a warehouse.",
     "url": "https://warehouse.example.com/ingest", "events": ["orders.*", "stock.*"],
     "headers": {"X-Source": "omnistore"}, "enabled": False, "max_attempts": 5},
]

INBOUND = [
    {"slug": "payment-gateway", "name": "Payment gateway",
     "description": "Charge confirmations and failures from the payment provider.",
     "verification": "hmac-sha256", "signature_header": "x-signature",
     "target_type": "flow", "target": "payment_hook", "enabled": True},
    {"slug": "carrier-tracking", "name": "Carrier tracking",
     "description": "Scan events for parcels the carrier is moving.",
     "verification": "hmac-sha256", "signature_header": "x-signature",
     "target_type": "flow", "target": "shipment_scan", "enabled": True},
]

SCHEDULES = [
    {"name": "abandoned_cart_sweep", "description": "Every 30 minutes: chase baskets that went quiet.",
     "cron": None, "interval_seconds": 1800, "target_type": "flow", "target": "abandoned_cart_sweep",
     "payload": {"source": "scheduler"}, "enabled": True},
    {"name": "daily_rollup", "description": "00:10 UTC: fold yesterday into daily_stats.",
     "cron": "10 0 * * *", "interval_seconds": None, "target_type": "flow", "target": "daily_rollup",
     "payload": {"source": "scheduler"}, "enabled": True},
]

SETTINGS = {
    "public_docs": True,
    "realtime": {
        "allow_client_publish": False,
        "channels": [
            {"pattern": "store:{{ auth.org }}", "subscribe": {"exists": "$auth.org"},
             "publish": "deny", "presence": True, "history": 100},
            {"pattern": "user:{{ auth.user_id }}", "subscribe": {"exists": "$auth.user_id"},
             "publish": "deny", "presence": False, "history": 50},
            {"pattern": "platform:*", "subscribe": "platform_admin", "publish": "deny",
             "presence": False, "history": 50},
        ],
    },
}

AUTH = {
    "signup_enabled": True,
    "require_email_verification": False,
    "password_policy": "basic",
    "password_min_length": 8,
    "access_ttl": 900,
    "refresh_ttl": 2592000,
    "magic_link_enabled": True,
    "mfa_enabled": True,
    "default_roles": ["customer"],
}



# ── a demo store ─────────────────────────────────────────────────────────────

DEMO = "omnistore-demo"
NOW = "2026-09-30T09:00:00+00:00"
IMG = "https://images.unsplash.com/photo-{}?w=1200&q=80"

#: id, title, handle, category, brand, price (kobo), compare-at, photo, stock.
PRODUCTS = [
    (1, "Ankara Wrap Dress", "ankara-wrap-dress", "dresses", "house", 3_500_000, 4_200_000,
     "1515886657613-9f3515b0c78f", 24),
    (2, "Adire Silk Scarf", "adire-silk-scarf", "accessories", "house", 1_200_000, None,
     "1601924949898-69e26d50dc26", 40),
    (3, "Leather Tote Bag", "leather-tote-bag", "accessories", "harbor", 4_800_000, 5_500_000,
     "1548036328-c9fa89d128fa", 12),
    (4, "Kaftan Shirt", "kaftan-shirt", "menswear", "house", 2_600_000, None,
     "1521572163474-6864f9cf17ab", 30),
    (5, "Beaded Bracelet Set", "beaded-bracelet-set", "accessories", "river", 650_000, 800_000,
     "1611591437281-460bfbe1220a", 60),
]

CATEGORIES = [
    {"id": 1, "store_id": DEMO, "name": "Dresses", "handle": "dresses", "position": 1, "is_published": True},
    {"id": 2, "store_id": DEMO, "name": "Menswear", "handle": "menswear", "position": 2, "is_published": True},
    {"id": 3, "store_id": DEMO, "name": "Accessories", "handle": "accessories", "position": 3, "is_published": True},
]

BRANDS = [
    {"id": 1, "store_id": DEMO, "name": "House", "slug": "house", "is_published": True},
    {"id": 2, "store_id": DEMO, "name": "Harbor Leather", "slug": "harbor", "is_published": True},
    {"id": 3, "store_id": DEMO, "name": "River Made", "slug": "river", "is_published": True},
]


def sample_data():
    """One demo store: a small catalog, its stock, its price list, one past order.

    Every id here is referenced by another row, so the demo can be walked end to
    end: the order's line, its captured payment, its delivered parcel, the
    review it earned, and the shelf count the sale left behind.
    """
    data: dict[str, list] = defaultdict(list)
    data["stores"] = [{
        "id": 1, "ref": DEMO, "name": "Omni Threads", "currency": "NGN", "country": "NG",
        "email": "hello@omnithreads.example", "support_email": "care@omnithreads.example",
        "owner_user_id": "user_owner", "timezone": "Africa/Lagos",
        "order_prefix": "ORD", "order_sequence": 1100, "low_stock_threshold": 5,
        "loyalty_enabled": True, "loyalty_earn_bps": 500, "loyalty_redeem_value_minor": 100,
        "tax_rate_bps": 750, "is_active": True, "accepts_orders": True, "gateway_mode": "test",
        "gateway_url": "https://pay.example.test",
    }]
    data["categories"] = [dict(row) for row in CATEGORIES]
    data["brands"] = [dict(row) for row in BRANDS]
    categories = {row["handle"]: row["id"] for row in data["categories"]}
    brands = {row["slug"]: row["id"] for row in data["brands"]}
    data["warehouses"] = [{
        "id": 1, "store_id": DEMO, "name": "Main store", "code": "MAIN", "kind": "physical",
        "city": "Lagos", "country": "NG", "is_default": True, "is_active": True, "priority": 100,
    }]
    data["shipping_zones"] = [{
        "id": 1, "store_id": DEMO, "name": "Nigeria", "countries": ["NG"], "currency": "NGN",
        "lead_min_days": 1, "lead_max_days": 5, "is_active": True, "position": 1,
    }]
    data["shipping_rates"] = [
        {"id": 1, "store_id": DEMO, "zone_id": 1, "name": "Standard", "kind": "flat",
         "price_minor": 250_000, "free_over_minor": 5_000_000,
         "delivery_estimate": "2–5 days", "is_active": True, "position": 1},
        {"id": 2, "store_id": DEMO, "zone_id": 1, "name": "Express", "kind": "flat",
         "price_minor": 500_000, "delivery_estimate": "next day", "is_active": True, "position": 2},
    ]
    data["tax_rates"] = [{
        "id": 1, "store_id": DEMO, "country": "NG", "name": "VAT", "rate_bps": 750,
        "applies_to_shipping": False, "is_active": True,
    }]
    data["coupons"] = [
        {"id": 1, "store_id": DEMO, "code": "WELCOME15", "title": "15% off your first order",
         "kind": "percentage", "value": 1500, "maximum_discount_minor": 1_000_000,
         "usage_limit": 500, "per_customer_limit": 1, "usage_count": 0, "is_active": True},
        {"id": 2, "store_id": DEMO, "code": "NAIRA5000", "title": "₦5,000 off over ₦40,000",
         "kind": "fixed_amount", "value": 500_000, "minimum_order_minor": 4_000_000,
         "usage_limit": 0, "usage_count": 0, "is_active": True},
    ]
    data["suppliers"] = [{
        "id": 1, "store_id": DEMO, "name": "Loom & Co", "email": "orders@loomco.example",
        "country": "NG", "lead_time_days": 14, "payment_terms_days": 30, "is_active": True,
    }]
    variant_id = 0
    for pid, title, handle, category, brand, price, compare, photo, stock in PRODUCTS:
        data["products"].append({
            "id": pid, "store_id": DEMO, "title": title, "handle": handle,
            "category_id": categories[category], "brand_id": brands[brand],
            "status": "active", "is_published": True, "tax_class": "standard", "weight_grams": 400,
            "rating_avg": 500, "rating_count": 1, "sold_count": 1, "in_stock": True,
            "published_at": NOW,
        })
        data["product_media"].append({
            "id": pid, "store_id": DEMO, "product_id": pid, "url": IMG.format(photo),
            "alt": title, "kind": "image", "position": 0,
        })
        for position, suffix in enumerate(["Default", "Gift"]):
            variant_id += 1
            data["product_variants"].append({
                "id": variant_id, "store_id": DEMO, "product_id": pid, "title": suffix,
                "sku": f"OT-{pid:02d}-{position + 1:02d}",
                "price_minor": price + (position * 50_000), "compare_at_minor": compare,
                "cost_minor": int(price * 0.45), "weight_grams": 400,
                "is_default": position == 0, "track_inventory": True, "allow_backorder": False,
                "reorder_point": 5, "available": stock, "status": "active",
            })
            data["stock_levels"].append({
                "id": variant_id, "store_id": DEMO, "warehouse_id": 1, "variant_id": variant_id,
                "on_hand": stock, "reserved": 0, "reorder_point": 5, "last_counted_at": NOW,
            })
    data["customers"] = [{
        "id": 1, "store_id": DEMO, "email": "ada@example.com", "first_name": "Ada",
        "last_name": "Obi", "tier": "standard", "orders_count": 1,
        "lifetime_value_minor": 4_012_500, "loyalty_balance": 2_006, "marketing_opt_in": True,
        "is_guest": False, "last_order_at": NOW,
    }]
    data["orders"] = [{
        "id": 1, "store_id": DEMO, "number": 1001, "reference": "ORD-1001-DEMO", "customer_id": 1,
        "email": "ada@example.com", "status": "fulfilled", "payment_status": "paid",
        "fulfillment_status": "fulfilled", "channel": "web", "currency": "NGN",
        "subtotal_minor": 3_500_000, "discount_minor": 0, "shipping_minor": 250_000,
        "tax_minor": 262_500, "total_minor": 4_012_500, "paid_minor": 4_012_500,
        "refunded_minor": 0, "warehouse_id": 1, "shipping_weight_grams": 400,
        "loyalty_earned_points": 2_006, "placed_at": NOW, "paid_at": NOW, "fulfilled_at": NOW,
    }]
    data["order_items"] = [{
        "id": 1, "store_id": DEMO, "order_id": 1, "variant_id": 1, "product_id": 1,
        "title": "Ankara Wrap Dress", "variant_title": "Default", "sku": "OT-01-01",
        "quantity": 1, "unit_price_minor": 3_500_000, "total_minor": 3_500_000,
        "fulfilled_quantity": 1, "warehouse_id": 1, "position": 0,
    }]
    data["payments"] = [{
        "id": 1, "store_id": DEMO, "order_id": 1, "customer_id": 1, "provider": "example",
        "kind": "charge", "status": "captured", "amount_minor": 4_012_500, "currency": "NGN",
        "reference": "PAY-DEMO-1", "gateway_reference": "ch_demo_1", "captured_at": NOW,
        "mode": "test",
    }]
    data["shipments"] = [{
        "id": 1, "store_id": DEMO, "order_id": 1, "warehouse_id": 1, "carrier": "GIG Logistics",
        "status": "delivered", "tracking_number": "GIG-77123", "recipient_name": "Ada Obi",
        "shipped_at": NOW, "delivered_at": NOW,
    }]
    data["order_events"] = [
        {"id": 1, "store_id": DEMO, "order_id": 1, "kind": "placed",
         "message": "Order opened for ada@example.com", "actor_kind": "shopper"},
        {"id": 2, "store_id": DEMO, "order_id": 1, "kind": "paid",
         "message": "Paid 4012500 NGN", "actor_kind": "system"},
        {"id": 3, "store_id": DEMO, "order_id": 1, "kind": "shipped",
         "message": "Shipped with GIG Logistics — GIG-77123", "actor_kind": "staff"},
    ]
    data["inventory_ledger"] = [{
        "id": 1, "store_id": DEMO, "warehouse_id": 1, "variant_id": 1, "delta": -1,
        "reason": "sale", "reference_type": "order", "reference_id": 1, "on_hand_after": 24,
        "reserved_after": 0, "actor_kind": "system", "note": "Paid order ORD-1001-DEMO",
    }]
    data["reviews"] = [{
        "id": 1, "store_id": DEMO, "product_id": 1, "user_id": "user_ada", "author_name": "Ada O.",
        "rating": 5, "title": "Wore it twice already", "body": "Beautiful cut, and the fabric holds up.",
        "status": "approved", "is_verified_purchase": True, "order_id": 1, "helpful_count": 3,
    }]
    data["pages"] = [{
        "id": 1, "store_id": DEMO, "title": "Shipping & returns", "handle": "shipping",
        "kind": "policy", "body": "Orders ship in 1–2 days. Returns accepted within 14 days.",
        "is_published": True, "published_at": NOW, "position": 1,
    }]
    data["banners"] = [{
        "id": 1, "store_id": DEMO, "title": "New season", "placement": "hero",
        "image_url": IMG.format("1483985988355-763728e1935b"), "headline": "New season",
        "cta_label": "Shop now", "cta_url": "/collections/new", "is_published": True,
        "position": 1,
    }]
    return dict(data)


def build():
    """The whole document, in the shape Studio imports."""
    return {
        "format": "pawabase.blueprint",
        "version": 1,
        "name": "OmniStore Commerce",
        "description": (
            "A multi-tenant commerce backend: a catalog with variants and media, multi-warehouse inventory with an "
            "append-only ledger, carts and coupons, a checkout that holds stock and talks to a gateway, fulfilment, "
            "cancellations, refunds and returns, loyalty and gift cards, reviews, tickets and a small storefront CMS. "
            "A store is an organization, staff power comes from the org role, and every stock movement is journalled in "
            "the same transaction that moves it."
        ),
        "source": {"project": "omnistore", "env": "blueprint",
                   "generator": "examples/blueprints/omnistore/build.py"},
        "definitions": {
            "schemas": SCHEMAS,
            "transformers": TRANSFORMERS,
            "policies": POLICIES,
            "resources": RESOURCES,
            "mail-templates": MAIL,
            "flows": FLOWS,
            "routes": ROUTES,
            "buckets": BUCKETS,
            "subscriptions": SUBSCRIPTIONS,
            "webhooks": WEBHOOKS,
            "inbound-hooks": INBOUND,
            "schedules": SCHEDULES,
        },
        "roles": ROLES,
        "auth": AUTH,
        "settings": SETTINGS,
        "data": sample_data(),
    }


if __name__ == "__main__":
    blueprint = build()
    out = HERE / "omnistore.blueprint.json"
    out.write_text(json.dumps(blueprint, indent=2, ensure_ascii=False) + "\n")
    counts = {k: len(v) for k, v in blueprint["definitions"].items()}
    rows = sum(len(v) for v in blueprint["data"].values())
    print(f"wrote {out.name}: {sum(counts.values())} definitions {counts}, "
          f"{len(ROLES)} roles, {rows} sample rows")

