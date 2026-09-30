"""Reusable request schemas: the validated shapes clients post to routes.

A route that names a schema gets Sillo validation for free, and the OpenAPI
document Studio renders is generated from the same fields, so the contract
cannot drift from what is enforced.
"""

from __future__ import annotations

from helpers import CODE, COUNTRY, F, SLUG, money

# ── identity and the storefront ──────────────────────────────────────────────

ADDRESS = {
    "name": "Address",
    "description": "A postal address, stored inline on the order it ships to.",
    "fields": [
        F("first_name", required=True, max_length=80),
        F("last_name", required=True, max_length=80),
        F("company", max_length=120),
        F("line1", required=True, max_length=160),
        F("line2", max_length=160),
        F("city", required=True, max_length=80),
        F("province", max_length=80),
        F("postal_code", max_length=20),
        F("country", required=True, pattern=COUNTRY, example="NG"),
        F("phone", max_length=32),
    ],
}

STORE_SETUP = {
    "name": "StoreSetup",
    "description": "Register the store for the active organization.",
    "fields": [
        F("name", required=True, max_length=120),
        F("currency", required=True, enum=["NGN", "USD", "GBP", "EUR", "KES", "GHS", "ZAR", "CAD"]),
        F("country", required=True, pattern=COUNTRY, example="NG"),
        F("email", "email", required=True),
        F("phone", max_length=32),
        F("timezone", default="Africa/Lagos", max_length=60),
        F("support_email", "email"),
    ],
}

STAFF_INVITE = {
    "name": "StaffInvite",
    "description": "Invite a person into the store with an org role.",
    "fields": [
        F("email", "email", required=True),
        F("org_role", required=True, enum=["admin", "member", "viewer"]),
        F("warehouse_id", "integer"),
        F("note", "text", max_length=500),
    ],
}

CART_ADD = {
    "name": "CartAdd",
    "description": "Add a variant to a cart, creating the cart when no token is given.",
    "fields": [
        F("cart_token", max_length=64, description="Omit to start a new cart"),
        F("store_id", required=True, pattern=SLUG),
        F("variant_id", "integer", required=True),
        F("quantity", "integer", required=True, minimum=1, maximum=999),
        F("add_on_ids", "array", items={"type": "integer"}, description="Bundle add-ons"),
    ],
}

CART_QUANTITY = {
    "name": "CartQuantity",
    "description": "Change or remove a cart line. Quantity 0 removes it.",
    "fields": [
        F("cart_token", required=True, max_length=64),
        F("item_id", "integer", required=True),
        F("quantity", "integer", required=True, minimum=0, maximum=999),
    ],
}

CART_CODE = {
    "name": "CartCode",
    "description": "Apply a coupon or gift card to a cart.",
    "fields": [
        F("cart_token", required=True, max_length=64),
        F("code", required=True, max_length=40),
    ],
}

CHECKOUT = {
    "name": "CheckoutInput",
    "description": "Turn a cart into an order: prices, stock, tax, shipping and totals.",
    "fields": [
        F("cart_token", required=True, max_length=64),
        F("email", "email", required=True),
        F("phone", max_length=32),
        F("shipping_address", "ref", schema="Address", required=True),
        F("billing_address", "ref", schema="Address", nullable=True),
        F("shipping_rate_id", "integer", nullable=True),
        F("coupon_code", max_length=40),
        F("gift_card_code", max_length=40),
        F("loyalty_points", "integer", minimum=0, maximum=1000000, default=0),
        F("note", "text", max_length=1000),
        F("accepts_marketing", "boolean", default=False),
    ],
}

WISHLIST_ITEM = {
    "name": "WishlistItem",
    "description": "Save a variant to the shopper's wishlist.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("variant_id", "integer", required=True),
        F("note", max_length=200),
    ],
}

STOCK_ALERT = {
    "name": "StockAlert",
    "description": "Email me when this variant is back in stock.",
    "fields": [
        F("variant_id", "integer", required=True),
        F("email", "email", required=True),
    ],
}

NEWSLETTER = {
    "name": "NewsletterSignup",
    "description": "Join a store's mailing list.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("email", "email", required=True),
        F("source", max_length=60, default="storefront"),
    ],
}

# ── the back office: catalog ─────────────────────────────────────────────────

CATEGORY_INPUT = {
    "name": "CategoryInput",
    "description": "Create or re-parent a catalog category.",
    "fields": [
        F("name", required=True, max_length=120),
        F("parent_id", "integer", nullable=True),
        F("position", "integer", minimum=0, default=0),
        F("is_published", "boolean", default=True),
    ],
}

BRAND_INPUT = {
    "name": "BrandInput",
    "description": "A manufacturer or label.",
    "fields": [
        F("name", required=True, max_length=120),
        F("logo_url", "url"),
        F("description", "text", max_length=2000),
        F("is_published", "boolean", default=True),
    ],
}

PRODUCT_INPUT = {
    "name": "ProductInput",
    "description": "Create a product. Variants are added separately, each with its own price.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("title", required=True, max_length=200),
        F("handle", pattern=r"^[a-z0-9][a-z0-9-]{0,120}$"),
        F("subtitle", max_length=200),
        F("description", "text", max_length=20000),
        F("category_id", "integer", nullable=True),
        F("brand_id", "integer", nullable=True),
        F("status", enum=["draft", "active", "archived"], default="draft"),
        F("is_published", "boolean", default=False),
        F("tax_class", max_length=40, default="standard"),
        F("weight_grams", "integer", minimum=0, default=0),
        F("internal_note", "text", max_length=2000),
        F("tags", "array", items={"type": "string"}),
    ],
}

PRODUCT_UPDATE = {
    "name": "ProductUpdate",
    "description": "Change a product. Only the fields you send are touched.",
    "fields": [
        F("title", max_length=200),
        F("subtitle", max_length=200),
        F("description", "text", max_length=20000),
        F("category_id", "integer", nullable=True),
        F("brand_id", "integer", nullable=True),
        F("status", enum=["draft", "active", "archived"]),
        F("is_published", "boolean"),
        F("tax_class", max_length=40),
        F("weight_grams", "integer", minimum=0),
        F("internal_note", "text", max_length=2000),
    ],
}

VARIANT_INPUT = {
    "name": "VariantInput",
    "description": "A buyable version of a product: price, SKU and inventory policy.",
    "fields": [
        F("product_id", "integer", required=True),
        F("title", required=True, max_length=120),
        F("sku", required=True, max_length=64),
        money("price_minor", required=True),
        money("compare_at_minor", description="Strike-through price"),
        money("cost_minor", description="What the store pays"),
        F("barcode", max_length=64),
        F("weight_grams", "integer", minimum=0, default=0),
        F("is_default", "boolean", default=False),
        F("track_inventory", "boolean", default=True),
        F("allow_backorder", "boolean", default=False),
        F("reorder_point", "integer", minimum=0, default=0),
        F("supplier_sku", max_length=64),
    ],
}

MEDIA_INPUT = {
    "name": "MediaInput",
    "description": "Attach an image or video to a product or category.",
    "fields": [
        F("product_id", "integer", nullable=True),
        F("variant_id", "integer", nullable=True),
        F("category_id", "integer", nullable=True),
        F("kind", enum=["image", "video"], default="image"),
        F("url", "url", required=True),
        F("alt", max_length=200),
        F("position", "integer", minimum=0, default=0),
    ],
}


# ── the back office: inventory ───────────────────────────────────────────────

STOCK_ADJUST = {
    "name": "StockAdjust",
    "description": "Move stock at one warehouse. Every change is journalled.",
    "fields": [
        F("variant_id", "integer", required=True),
        F("warehouse_id", "integer", required=True),
        F("delta", "integer", required=True, description="May be negative"),
        F("reason", required=True, enum=[
            "purchase_receipt",
            "damage",
            "shrinkage",
            "cycle_count",
            "manual",
        ]),
        F("note", "text", max_length=500),
    ],
}

STOCK_TRANSFER = {
    "name": "StockTransfer",
    "description": "Move stock between two warehouses with a two-phase ledger write.",
    "fields": [
        F("from_warehouse_id", "integer", required=True),
        F("to_warehouse_id", "integer", required=True),
        F("variant_id", "integer", required=True),
        F("quantity", "integer", required=True, minimum=1, maximum=100000),
        F("note", "text", max_length=500),
    ],
}

PURCHASE_ORDER = {
    "name": "PurchaseOrderInput",
    "description": "Order stock from a supplier into a warehouse.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("supplier_name", required=True, max_length=160),
        F("warehouse_id", "integer", required=True),
        F("currency", enum=["NGN", "USD", "GBP", "EUR", "KES", "GHS", "ZAR", "CAD"], default="NGN"),
        F("expected_at", "datetime", nullable=True),
        F("note", "text", max_length=1000),
        F("lines", "array", required=True, items={"type": "object", "fields": [
            F("variant_id", "integer", required=True),
            F("quantity", "integer", required=True, minimum=1),
            money("unit_cost_minor", required=True),
        ]}),
    ],
}

PURCHASE_RECEIPT = {
    "name": "PurchaseReceipt",
    "description": "Book received quantities against purchase-order lines.",
    "fields": [
        F("warehouse_id", "integer", required=True),
        F("note", "text", max_length=500),
        F("lines", "array", required=True, items={"type": "object", "fields": [
            F("line_id", "integer", required=True),
            F("quantity", "integer", required=True, minimum=1),
        ]}),
    ],
}

# ── the back office: orders ──────────────────────────────────────────────────

SHIPMENT_INPUT = {
    "name": "ShipmentInput",
    "description": "Ship selected order lines, reserving stock from a warehouse.",
    "fields": [
        F("warehouse_id", "integer", nullable=True),
        F("carrier", max_length=80),
        F("tracking_number", max_length=120),
        F("tracking_url", "url"),
        money("cost_minor"),
        F("notify_customer", "boolean", default=True),
        F("lines", "array", required=True, items={"type": "object", "fields": [
            F("order_item_id", "integer", required=True),
            F("quantity", "integer", required=True, minimum=1),
        ]}),
    ],
}

SHIPMENT_STATUS = {
    "name": "ShipmentStatusInput",
    "description": "Advance a shipment. Delivery also settles the order.",
    "fields": [
        F("status", required=True, enum=[
            "label_created",
            "in_transit",
            "out_for_delivery",
            "delivered",
            "failed",
            "returned",
        ]),
        F("location", max_length=160),
        F("note", "text", max_length=500),
    ],
}

RETURN_REQUEST = {
    "name": "ReturnRequest",
    "description": "A shopper asks to send items back.",
    "fields": [
        F("order_id", "integer", required=True),
        F("reason", required=True, enum=[
            "wrong_item",
            "damaged",
            "not_as_described",
            "changed_mind",
            "late_delivery",
            "other",
        ]),
        F("note", "text", max_length=2000),
        F("refund_method", enum=["original", "store_credit"], default="original"),
        F("lines", "array", required=True, items={"type": "object", "fields": [
            F("order_item_id", "integer", required=True),
            F("quantity", "integer", required=True, minimum=1),
        ]}),
    ],
}

RETURN_DECISION = {
    "name": "ReturnDecision",
    "description": "Approve, reject or receive a return.",
    "fields": [
        F("status", required=True, enum=["approved", "rejected", "received"]),
        F("note", "text", max_length=1000),
        F("restock", "boolean", default=False),
    ],
}

REFUND_INPUT = {
    "name": "RefundInput",
    "description": "Refund part or all of an order, optionally restocking the items.",
    "fields": [
        money("amount_minor", required=True),
        F("reason", required=True, enum=[
            "customer_request",
            "damaged",
            "not_received",
            "wrong_item",
            "fraud",
            "other",
        ]),
        F("note", "text", max_length=1000),
        F("restock", "boolean", default=False),
        F("return_id", "integer", nullable=True),
    ],
}

ORDER_NOTE = {
    "name": "OrderNote",
    "description": "Leave a note on an order for the back office or the buyer.",
    "fields": [
        F("note", "text", required=True, max_length=2000),
        F("is_customer_visible", "boolean", default=False),
    ],
}

ORDER_CANCEL = {
    "name": "OrderCancel",
    "description": "Cancel an unpaid or unshipped order.",
    "fields": [
        F("reason", required=True, max_length=200),
        F("restock", "boolean", default=True),
    ],
}

PAYMENT_CAPTURE = {
    "name": "PaymentCapture",
    "description": "Record a payment taken outside the gateway (transfer, cash, POS).",
    "fields": [
        F("method", required=True, enum=[
            "card",
            "bank_transfer",
            "cash",
            "pos",
            "mobile_money",
            "gift_card",
            "store_credit",
        ]),
        money("amount_minor", required=True),
        F("reference", max_length=120),
        F("note", "text", max_length=500),
    ],
}

# ── the back office: promotions, loyalty, content ────────────────────────────

COUPON_INPUT = {
    "name": "CouponInput",
    "description": "A discount code with its limits.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("code", required=True, pattern=CODE),
        F("title", max_length=160),
        F("kind", required=True, enum=["percentage", "fixed_amount", "free_shipping"]),
        F("value", "integer", minimum=0, default=0, description="Basis points for percentage"),
        money("minimum_order_minor"),
        money("maximum_discount_minor"),
        F("usage_limit", "integer", minimum=0, nullable=True),
        F("per_customer_limit", "integer", minimum=0, nullable=True),
        F("starts_at", "datetime", nullable=True),
        F("ends_at", "datetime", nullable=True),
        F("is_active", "boolean", default=True),
    ],
}

PROMOTION_INPUT = {
    "name": "PromotionInput",
    "description": "An automatic promotion evaluated at checkout without a code.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("name", required=True, max_length=160),
        F("kind", required=True, enum=[
            "automatic_discount",
            "free_shipping_over",
            "buy_x_get_y",
            "bundle_price",
        ]),
        F("config", "json", required=True, description="Kind-specific thresholds and targets"),
        F("priority", "integer", minimum=0, default=100),
        F("starts_at", "datetime", nullable=True),
        F("ends_at", "datetime", nullable=True),
        F("is_active", "boolean", default=True),
    ],
}

GIFT_CARD_INPUT = {
    "name": "GiftCardInput",
    "description": "Issue a gift card. The code is the balance's only key.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("code", required=True, pattern=CODE),
        money("initial_minor", required=True),
        F("currency", enum=["NGN", "USD", "GBP", "EUR", "KES", "GHS", "ZAR", "CAD"], default="NGN"),
        F("expires_at", "datetime", nullable=True),
        F("note", max_length=200),
    ],
}

LOYALTY_ADJUST = {
    "name": "LoyaltyAdjust",
    "description": "Add or remove loyalty points by hand.",
    "fields": [
        F("user_id", required=True, max_length=64),
        F("points", "integer", required=True, description="May be negative"),
        F("reason", required=True, max_length=200),
    ],
}

PAGE_INPUT = {
    "name": "PageInput",
    "description": "A CMS page for the storefront.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("title", required=True, max_length=200),
        F("slug", required=True, pattern=SLUG),
        F("body", "text", max_length=200000),
        F("meta_title", max_length=200),
        F("meta_description", max_length=400),
        F("is_published", "boolean", default=False),
        F("editor_note", "text", max_length=2000),
    ],
}

BANNER_INPUT = {
    "name": "BannerInput",
    "description": "A storefront banner in a named slot.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("slot", required=True, max_length=40, example="home-hero"),
        F("headline", required=True, max_length=160),
        F("subhead", max_length=240),
        F("image_url", "url", required=True),
        F("link_url", "url"),
        F("position", "integer", minimum=0, default=0),
        F("is_published", "boolean", default=True),
    ],
}

# ── community and support ────────────────────────────────────────────────────

REVIEW_INPUT = {
    "name": "ReviewInput",
    "description": "A verified-buyer review of a product.",
    "fields": [
        F("product_id", "integer", required=True),
        F("rating", "integer", required=True, minimum=1, maximum=5),
        F("title", max_length=160),
        F("body", "text", max_length=8000),
    ],
}

REVIEW_MODERATION = {
    "name": "ReviewModeration",
    "description": "Approve, reject or spam-flag a review.",
    "fields": [
        F("status", required=True, enum=["pending", "approved", "rejected", "spam"]),
        F("note", "text", max_length=1000),
    ],
}

QUESTION_INPUT = {
    "name": "QuestionInput",
    "description": "A product question from a shopper.",
    "fields": [
        F("product_id", "integer", required=True),
        F("body", "text", required=True, max_length=2000),
    ],
}

ANSWER_INPUT = {
    "name": "AnswerInput",
    "description": "A store's answer to a product question.",
    "fields": [
        F("body", "text", required=True, max_length=4000),
        F("is_public", "boolean", default=True),
    ],
}

TICKET_INPUT = {
    "name": "TicketInput",
    "description": "A support request about an order or a store.",
    "fields": [
        F("store_id", required=True, pattern=SLUG),
        F("order_id", "integer", nullable=True),
        F("email", "email", required=True),
        F("subject", required=True, max_length=200),
        F("message", "text", required=True, max_length=8000),
    ],
}

TICKET_REPLY = {
    "name": "TicketReply",
    "description": "Staff answer a ticket, optionally closing it.",
    "fields": [
        F("message", "text", required=True, max_length=8000),
        F("close", "boolean", default=False),
    ],
}

NOTIFY_INPUT = {
    "name": "NotifyInput",
    "description": "Queue a notification for a user.",
    "fields": [
        F("user_id", required=True, max_length=64),
        F("channel", enum=["inapp", "email"], default="inapp"),
        F("title", required=True, max_length=200),
        F("body", "text", max_length=4000),
        F("link", max_length=400),
    ],
}

SCHEMAS = [
    ADDRESS,
    STORE_SETUP,
    STAFF_INVITE,
    CART_ADD,
    CART_QUANTITY,
    CART_CODE,
    CHECKOUT,
    WISHLIST_ITEM,
    STOCK_ALERT,
    NEWSLETTER,
    CATEGORY_INPUT,
    BRAND_INPUT,
    PRODUCT_INPUT,
    PRODUCT_UPDATE,
    VARIANT_INPUT,
    MEDIA_INPUT,
    STOCK_ADJUST,
    STOCK_TRANSFER,
    PURCHASE_ORDER,
    PURCHASE_RECEIPT,
    SHIPMENT_INPUT,
    SHIPMENT_STATUS,
    RETURN_REQUEST,
    RETURN_DECISION,
    REFUND_INPUT,
    ORDER_NOTE,
    ORDER_CANCEL,
    PAYMENT_CAPTURE,
    COUPON_INPUT,
    PROMOTION_INPUT,
    GIFT_CARD_INPUT,
    LOYALTY_ADJUST,
    PAGE_INPUT,
    BANNER_INPUT,
    REVIEW_INPUT,
    REVIEW_MODERATION,
    QUESTION_INPUT,
    ANSWER_INPUT,
    TICKET_INPUT,
    TICKET_REPLY,
    NOTIFY_INPUT,
]

