"""Marketing, content and support resources.

Grouped here because they all share one trait: they exist to nudge a shopper or
answer them, and they are read far more often than they are written — which is
why most of them carry a ``cache_ttl`` and a public read policy.
"""

from __future__ import annotations

from helpers import CODE, CURRENCY, HANDLE, SLUG, BT, F, HM, money, ops, resource

from model import REVIEW_STATUSES, TICKET_STATUSES

S = F("store_id", required=True, pattern=SLUG, description="The store (organization slug)")

RESOURCES = []

RESOURCES.append(
    resource(
        "coupons",
        "A code a shopper types at checkout, and the rules for pricing it.",
        [
            S,
            F("code", required=True, pattern=CODE, unique=True),
            F("title", required=True, max_length=120),
            F("kind", enum=["percentage", "fixed_amount", "free_shipping", "buy_x_get_y"], default="percentage"),
            F("value", "integer", required=True, minimum=0, description="Basis points for percentage, minor units otherwise"),
            money("minimum_order_minor", default=0),
            money("maximum_discount_minor", default=0),
            F("usage_limit", "integer", minimum=0, default=0, description="0 means unlimited"),
            F("per_customer_limit", "integer", minimum=0, default=0),
            F("usage_count", "integer", minimum=0, default=0),
            F("applies_to", enum=["whole_order", "collection", "product", "shipping"], default="whole_order"),
            F("collection_id", "integer", nullable=True),
            F("product_ids", "array", items={"type": "integer"}),
            F("customer_tiers", "array", items={"type": "string"}),
            F("first_order_only", "boolean", default=False),
            F("starts_at", "datetime", nullable=True),
            F("ends_at", "datetime", nullable=True),
            F("is_active", "boolean", default=True),
            F("stackable", "boolean", default=False),
        ],
        ops(
            list_="store_editor",
            get="store_editor",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        relations=[HM("redemptions", "coupon_redemptions", "coupon_id")],
        tags=["Promotions"],
    )
)

RESOURCES.append(
    resource(
        "coupon_redemptions",
        "One use of a coupon: who, on which order, for how much.",
        [
            S,
            F("coupon_id", "integer", required=True),
            F("order_id", "integer", required=True),
            F("customer_id", "integer", nullable=True),
            F("user_id", max_length=64),
            F("cart_id", "integer", nullable=True),
            money("discount_minor", required=True),
            F("status", enum=["reserved", "consumed", "released"], default="consumed"),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="service_only",
            update="service_only",
            delete=False,
        ),
        relations=[
            BT("coupon", "coupons", "coupon_id"),
            BT("order", "orders", "order_id"),
        ],
        tags=["Promotions"],
    )
)

RESOURCES.append(
    resource(
        "promotions",
        "Automatic discounts: no code, applied while a rule holds (spend X, get Y).",
        [
            S,
            F("name", required=True, max_length=120),
            F("kind", enum=["spend_get", "quantity_tier", "bundle", "bogo"], default="spend_get"),
            money("threshold_minor", default=0),
            F("threshold_quantity", "integer", minimum=0, default=0),
            F("benefit_kind", enum=["percentage", "fixed_amount", "free_shipping"], default="percentage"),
            F("benefit_value", "integer", minimum=0, default=0),
            F("collection_id", "integer", nullable=True),
            money("cap_minor", default=0),
            F("priority", "integer", minimum=0, default=100),
            F("starts_at", "datetime", nullable=True),
            F("ends_at", "datetime", nullable=True),
            F("is_active", "boolean", default=True),
        ],
        ops(
            list_="store_editor",
            get="store_editor",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        tags=["Promotions"],
    )
)

RESOURCES.append(
    resource(
        "gift_cards",
        "Store credit with a code, an initial value and a running balance.",
        [
            S,
            F("code", required=True, max_length=24, unique=True),
            F("customer_id", "integer", nullable=True),
            F("user_id", max_length=64),
            F("email", "email"),
            money("initial_minor", required=True),
            money("balance_minor", required=True),
            F("currency", pattern=CURRENCY, default="NGN"),
            F("status", enum=["active", "redeemed", "expired", "void"], default="active"),
            F("expires_at", "datetime", nullable=True),
            F("issued_by", max_length=64),
            F("note", "text", max_length=500),
            F("last_used_at", "datetime", nullable=True),
        ],
        ops(
            list_="store_staff",
            get="customer_or_store",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        relations=[HM("transactions", "gift_card_transactions", "gift_card_id")],
        owner="user_id",
        tags=["Promotions"],
    )
)

RESOURCES.append(
    resource(
        "gift_card_transactions",
        "The journal behind a gift card balance: issue, top-up, spend, refund.",
        [
            S,
            F("gift_card_id", "integer", required=True),
            F("delta_minor", "integer", required=True),
            money("balance_after_minor", required=True),
            F("kind", enum=["issue", "topup", "spend", "refund", "void"], required=True),
            F("order_id", "integer", nullable=True),
            F("actor_id", max_length=64),
            F("note", max_length=300),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="service_only",
            update=False,
            delete=False,
        ),
        relations=[BT("gift_card", "gift_cards", "gift_card_id")],
        tags=["Promotions"],
    )
)

RESOURCES.append(
    resource(
        "loyalty_entries",
        "One loyalty points movement: earned, redeemed, adjusted or expired.",
        [
            S,
            F("customer_id", "integer", required=True),
            F("user_id", max_length=64),
            F("points", "integer", required=True),
            F("balance_after", "integer", required=True),
            F("kind", enum=["earn", "redeem", "adjust", "expire", "bonus"], required=True),
            F("order_id", "integer", nullable=True),
            F("expires_at", "datetime", nullable=True),
            F("note", max_length=300),
            F("actor_id", max_length=64),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="service_only",
            update=False,
            delete=False,
        ),
        relations=[
            BT("customer", "customers", "customer_id"),
            BT("order", "orders", "order_id"),
        ],
        owner="user_id",
        tags=["Loyalty"],
    )
)

RESOURCES.append(
    resource(
        "reviews",
        "A shopper's rating of a product. Pending until a moderator or an auto-rule approves it.",
        [
            S,
            F("product_id", "integer", required=True),
            F("variant_id", "integer", nullable=True),
            F("user_id", max_length=64),
            F("customer_id", "integer", nullable=True),
            F("author_name", max_length=120),
            F("rating", "integer", required=True, minimum=1, maximum=5),
            F("title", max_length=120),
            F("body", "text", max_length=4000),
            F("status", enum=REVIEW_STATUSES, default="pending"),
            F("is_verified_purchase", "boolean", default=False),
            F("order_id", "integer", nullable=True),
            F("helpful_count", "integer", minimum=0, default=0),
            F("photos", "array", items={"type": "string"}),
            F("staff_reply", "text", max_length=2000),
            F("replied_at", "datetime", nullable=True),
            F("moderated_by", max_length=64),
            F("moderated_at", "datetime", nullable=True),
        ],
        ops(
            list_="approved_read",
            get="approved_read",
            create="authenticated",
            update="moderator",
            delete="moderator",
        ),
        relations=[
            BT("product", "products", "product_id"),
            BT("order", "orders", "order_id"),
            HM("votes", "review_votes", "review_id"),
        ],
        owner="user_id",
        tags=["Content"],
        transformer="public_review",
        cache_ttl=60,
    )
)

RESOURCES.append(
    resource(
        "review_votes",
        "A shopper marking a review helpful: one vote per person, per review.",
        [
            S,
            F("review_id", "integer", required=True),
            F("user_id", required=True, max_length=64),
            F("vote", enum=["helpful", "unhelpful"], default="helpful"),
        ],
        ops(
            list_="service_only",
            get="service_only",
            create="authenticated",
            update=False,
            delete="authenticated",
        ),
        relations=[BT("review", "reviews", "review_id")],
        owner="user_id",
        tags=["Content"],
    )
)

RESOURCES.append(
    resource(
        "questions",
        "A public question about a product, and the store's answer.",
        [
            S,
            F("product_id", "integer", required=True),
            F("user_id", max_length=64),
            F("asker_name", max_length=120),
            F("question", "text", required=True, max_length=2000),
            F("answer", "text", max_length=4000),
            F("status", enum=["pending", "answered", "rejected"], default="pending"),
            F("answered_by", max_length=64),
            F("answered_at", "datetime", nullable=True),
            F("helpful_count", "integer", minimum=0, default=0),
        ],
        ops(
            list_="approved_read",
            get="approved_read",
            create="authenticated",
            update="store_editor",
            delete="moderator",
        ),
        relations=[BT("product", "products", "product_id")],
        owner="user_id",
        tags=["Content"],
        cache_ttl=60,
    )
)

RESOURCES.append(
    resource(
        "tickets",
        "A support conversation, opened from the storefront or the back office.",
        [
            S,
            F("code", max_length=24, unique=True),
            F("subject", required=True, max_length=160),
            F("status", enum=TICKET_STATUSES, default="open"),
            F("priority", enum=["low", "normal", "high", "urgent"], default="normal"),
            F("channel", enum=["storefront", "email", "phone", "chat", "staff"], default="storefront"),
            F("customer_id", "integer", nullable=True),
            F("user_id", max_length=64),
            F("requester_name", max_length=120),
            F("requester_email", "email", required=True),
            F("order_id", "integer", nullable=True),
            F("assigned_to", max_length=64),
            F("category", max_length=40),
            F("last_message_at", "datetime", nullable=True),
            F("first_response_at", "datetime", nullable=True),
            F("snooze_until", "datetime", nullable=True),
            F("satisfaction_score", "integer", minimum=0, maximum=5),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="public",
            update="support_desk",
            delete="store_manager",
        ),
        relations=[
            BT("customer", "customers", "customer_id"),
            BT("order", "orders", "order_id"),
            HM("messages", "ticket_messages", "ticket_id"),
        ],
        owner="user_id",
        tags=["Support"],
        realtime=True,
    )
)

RESOURCES.append(
    resource(
        "ticket_messages",
        "One message in a ticket, from the shopper or from staff.",
        [
            S,
            F("ticket_id", "integer", required=True),
            F("author_id", max_length=64),
            F("author_kind", enum=["customer", "staff", "system"], default="customer"),
            F("author_email", "email"),
            F("body", "text", required=True, max_length=8000),
            F("attachments", "array", items={"type": "string"}),
            F("is_internal", "boolean", default=False),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="public",
            update=False,
            delete="moderator",
        ),
        relations=[BT("ticket", "tickets", "ticket_id")],
        tags=["Support"],
        realtime=True,
    )
)

RESOURCES.append(
    resource(
        "pages",
        "Storefront content: policies, landing pages, about pages.",
        [
            S,
            F("title", required=True, max_length=160),
            F("handle", pattern=HANDLE),
            F("kind", enum=["page", "policy", "landing", "faq"], default="page"),
            F("body", "text", max_length=40000),
            F("meta_title", max_length=120),
            F("meta_description", "text", max_length=400),
            F("seo_noindex", "boolean", default=False),
            F("is_published", "boolean", default=False),
            F("published_at", "datetime", nullable=True),
            F("position", "integer", minimum=0, default=0),
            F("template", max_length=40),
            F("view_count", "integer", minimum=0, default=0),
        ],
        ops(
            list_="published_read",
            get="published_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        tags=["Content"],
        transformer="public_page",
        cache_ttl=120,
    )
)

RESOURCES.append(
    resource(
        "banners",
        "A promo strip or hero slide, with an optional schedule and click target.",
        [
            S,
            F("title", required=True, max_length=120),
            F("placement", enum=["hero", "header", "body", "footer", "popup", "category"], default="hero"),
            F("image_url", "url", required=True),
            F("mobile_image_url", "url"),
            F("headline", max_length=120),
            F("subheadline", max_length=200),
            F("cta_label", max_length=40),
            F("cta_url", "url"),
            F("starts_at", "datetime", nullable=True),
            F("ends_at", "datetime", nullable=True),
            F("position", "integer", minimum=0, default=0),
            F("is_published", "boolean", default=False),
            F("impression_count", "integer", minimum=0, default=0),
            F("click_count", "integer", minimum=0, default=0),
        ],
        ops(
            list_="published_read",
            get="published_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        tags=["Content"],
        transformer="storefront_banner",
        cache_ttl=120,
    )
)

RESOURCES.append(
    resource(
        "newsletter_subscribers",
        "Mailing-list signups, with double opt-in and unsubscribe bookkeeping.",
        [
            S,
            F("email", "email", required=True),
            F("user_id", max_length=64),
            F("status", enum=["pending", "subscribed", "unsubscribed", "bounced"], default="pending"),
            F("confirm_token", max_length=64),
            F("source", max_length=40),
            F("interests", "array", items={"type": "string"}),
            F("subscribed_at", "datetime", nullable=True),
            F("unsubscribed_at", "datetime", nullable=True),
        ],
        ops(
            list_="store_editor",
            get="store_editor",
            create="public",
            update="store_editor",
            delete="store_manager",
        ),
        tags=["Marketing"],
    )
)

RESOURCES.append(
    resource(
        "notifications",
        "One row per in-app notification, so the back office has an inbox with read state.",
        [
            S,
            F("user_id", required=True, max_length=64),
            F("kind", required=True, max_length=40),
            F("title", required=True, max_length=160),
            F("body", "text", max_length=1000),
            F("link", max_length=300),
            F("resource_type", max_length=40),
            F("resource_id", "integer", nullable=True),
            F("severity", enum=["info", "success", "warning", "error"], default="info"),
            F("is_read", "boolean", default=False),
            F("read_at", "datetime", nullable=True),
        ],
        ops(
            list_="customer_self",
            get="customer_self",
            create="service_only",
            update="customer_self",
            delete="customer_self",
        ),
        owner="user_id",
        tags=["Platform"],
        realtime=True,
    )
)

RESOURCES.append(
    resource(
        "daily_stats",
        "One row per store per day: the numbers the dashboard reads without scanning orders.",
        [
            S,
            F("day", required=True, max_length=10, description="ISO date, e.g. 2026-09-30"),
            F("orders_count", "integer", minimum=0, default=0),
            money("gross_minor", default=0),
            money("net_minor", default=0),
            money("refunds_minor", default=0),
            money("shipping_minor", default=0),
            money("tax_minor", default=0),
            money("discount_minor", default=0),
            F("units_sold", "integer", minimum=0, default=0),
            F("new_customers", "integer", minimum=0, default=0),
            F("cancellations", "integer", minimum=0, default=0),
            F("returns_count", "integer", minimum=0, default=0),
            F("page_views", "integer", minimum=0, default=0),
            F("carts_created", "integer", minimum=0, default=0),
            F("checkouts_started", "integer", minimum=0, default=0),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="service_only",
            update="service_only",
            delete=False,
        ),
        tags=["Analytics"],
        cache_ttl=60,
    )
)

# __APPEND__
