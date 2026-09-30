"""Order resources: customers, carts, orders, money movement, shipping, returns.

The order is the hub. Everything else hangs off it and points back with an
explicit id, so a store's support agent can walk *order → payment → shipment →
return → ledger* without joining the world.
"""

from __future__ import annotations

from helpers import (
    COUNTRY,
    CURRENCY,
    SLUG,
    BT,
    F,
    HM,
    bps,
    money,
    ops,
    resource,
)

from model import (
    CURRENCIES,
    FULFILMENT_STATUSES,
    ORDER_STATUSES,
    PAYMENT_STATUSES,
    RETURN_STATUSES,
    SHIPMENT_STATUSES,
)

S = F("store_id", required=True, pattern=SLUG, description="The store (organization slug)")
O = F("order_id", "integer", required=True)

RESOURCES = []

RESOURCES.append(
    resource(
        "customers",
        "A shopper as the store sees them: contact details, tier, lifetime value, loyalty balance.",
        [
            S,
            F("user_id", max_length=64),
            F("email", "email", required=True),
            F("phone", max_length=32),
            F("first_name", max_length=80),
            F("last_name", max_length=80),
            F("tier", max_length=24, default="standard"),
            F("orders_count", "integer", minimum=0, default=0, read_only=True),
            money("lifetime_value_minor", default=0, read_only=True),
            F("loyalty_balance", "integer", default=0, read_only=True),
            F("marketing_opt_in", "boolean", default=False),
            F("email_verified", "boolean", default=False),
            F("is_guest", "boolean", default=True),
            F("accepted_marketing_at", "datetime", nullable=True),
            F("last_order_at", "datetime", nullable=True),
            F("internal_note", "text", max_length=2000),
            F("tags", "array", items={"type": "string"}),
            F("is_suppressed", "boolean", default=False),
        ],
        ops(
            list_="store_staff",
            get="customer_or_store",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        relations=[
            HM("addresses", "addresses", "customer_id"),
            HM("orders", "orders", "customer_id"),
            HM("loyalty_entries", "loyalty_entries", "customer_id"),
        ],
        owner="user_id",
        tags=["Customers"],
    )
)

RESOURCES.append(
    resource(
        "addresses",
        "A shipping or billing address, owned by a customer.",
        [
            S,
            F("customer_id", "integer", nullable=True),
            F("user_id", max_length=64),
            F("kind", enum=["shipping", "billing", "either"], default="shipping"),
            F("recipient_name", required=True, max_length=120),
            F("phone", required=True, max_length=32),
            F("line1", required=True, max_length=160),
            F("line2", max_length=160),
            F("city", required=True, max_length=80),
            F("state", max_length=80),
            F("country", pattern=COUNTRY, default="NG"),
            F("postal_code", max_length=16),
            F("is_default", "boolean", default=False),
            F("is_verified", "boolean", default=False),
            F("label", max_length=40),
        ],
        ops(
            list_="customer_self",
            get="customer_self",
            create="customer_self_create",
            update="customer_self",
            delete="customer_self",
        ),
        relations=[BT("customer", "customers", "customer_id")],
        owner="user_id",
        tags=["Customers", "Storefront"],
    )
)

RESOURCES.append(
    resource(
        "carts",
        "An open basket. Anonymous carts are addressed by their cart token.",
        [
            S,
            F("token", required=True, max_length=64, unique=True),
            F("user_id", max_length=64),
            F("customer_id", "integer", nullable=True),
            F("email", "email"),
            F("phone", max_length=32),
            F("status", enum=["open", "converted", "abandoned", "expired"], default="open"),
            F("currency", pattern=CURRENCY, default="NGN"),
            money("subtotal_minor", default=0, read_only=True),
            money("discount_minor", default=0, read_only=True),
            F("item_count", "integer", minimum=0, default=0, read_only=True),
            F("coupon_code", max_length=32),
            F("discount_kind", max_length=24),
            F("channel", enum=["web", "mobile", "pos", "api"], default="web"),
            F("utm_source", max_length=80),
            F("utm_campaign", max_length=80),
            F("recovery_status", enum=["none", "sent", "recovered"], default="none"),
            F("last_activity_at", "datetime", nullable=True),
            F("converted_order_id", "integer", nullable=True),
        ],
        ops(
            list_="store_staff",
            get="customer_or_store",
            create="service_only",
            update="customer_or_store",
            delete="service_only",
        ),
        relations=[HM("items", "cart_items", "cart_id")],
        owner="user_id",
        tags=["Storefront"],
        realtime=True,
    )
)

RESOURCES.append(
    resource(
        "cart_items",
        "One line of a cart, priced at the time it was added.",
        [
            S,
            F("cart_id", "integer", required=True),
            F("variant_id", "integer", required=True),
            F("quantity", "integer", required=True, minimum=1, default=1),
            money("unit_price_minor", required=True),
            F("added_at", "datetime", nullable=True),
        ],
        ops(
            list_="service_only",
            get="service_only",
            create="service_only",
            update="service_only",
            delete="service_only",
        ),
        relations=[
            BT("cart", "carts", "cart_id"),
            BT("variant", "product_variants", "variant_id"),
        ],
        tags=["Storefront"],
    )
)

RESOURCES.append(
    resource(
        "orders",
        "A placed order. Totals are frozen at checkout; status moves are journalled in order_events.",
        [
            S,
            F("number", "integer", required=True),
            F("reference", required=True, max_length=40, unique=True, description="Public reference, e.g. ORD-1042-7QF"),
            F("customer_id", "integer", nullable=True),
            F("user_id", max_length=64),
            F("email", "email", required=True),
            F("phone", max_length=32),
            F("cart_id", "integer", nullable=True),
            F("status", enum=ORDER_STATUSES, default="pending"),
            F("payment_status", enum=PAYMENT_STATUSES, default="pending"),
            F("fulfillment_status", enum=FULFILMENT_STATUSES, default="unfulfilled"),
            F("channel", enum=["web", "mobile", "pos", "api"], default="web"),
            F("currency", pattern=CURRENCY, default="NGN"),
            money("subtotal_minor", required=True),
            money("discount_minor", default=0),
            money("shipping_minor", default=0),
            money("tax_minor", default=0),
            money("total_minor", required=True),
            money("paid_minor", default=0, read_only=True),
            money("refunded_minor", default=0, read_only=True),
            money("gift_card_minor", default=0),
            money("loyalty_redeemed_value_minor", default=0),
            F("loyalty_redeemed_points", "integer", default=0),
            F("loyalty_earned_points", "integer", default=0, read_only=True),
            F("coupon_code", max_length=32),
            F("coupon_id", "integer", nullable=True),
            F("shipping_method", max_length=60),
            F("shipping_address", "json"),
            F("billing_address", "json"),
            F("shipping_weight_grams", "integer", minimum=0, default=0),
            F("warehouse_id", "integer", nullable=True),
            F("utm_source", max_length=80),
            F("utm_medium", max_length=80),
            F("utm_campaign", max_length=80),
            F("utm_term", max_length=120),
            F("utm_content", max_length=120),
            F("customer_note", "text", max_length=2000),
            F("internal_note", "text", max_length=2000),
            bps("risk_score", default=0),
            F("risk_flags", "array", items={"type": "string"}),
            F("is_fraud_review", "boolean", default=False),
            F("placed_at", "datetime", nullable=True),
            F("paid_at", "datetime", nullable=True),
            F("fulfilled_at", "datetime", nullable=True),
            F("cancelled_at", "datetime", nullable=True),
            F("cancel_reason", max_length=200),
            F("idempotency_key", max_length=64),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="service_only",
            update="fulfilment_staff",
            delete=False,
        ),
        relations=[
            BT("customer", "customers", "customer_id"),
            BT("warehouse", "warehouses", "warehouse_id"),
            HM("items", "order_items", "order_id"),
            HM("payments", "payments", "order_id"),
            HM("refunds", "refunds", "order_id"),
            HM("shipments", "shipments", "order_id"),
            HM("returns", "returns", "order_id"),
            HM("events", "order_events", "order_id"),
        ],
        owner="user_id",
        tags=["Orders"],
        realtime=True,
        transformer="customer_order",
    )
)

RESOURCES.append(
    resource(
        "order_items",
        "One line of an order: a frozen title, price and quantity, plus fulfilment progress.",
        [
            S,
            O,
            F("variant_id", "integer", required=True),
            F("product_id", "integer", nullable=True),
            F("title", required=True, max_length=200),
            F("variant_title", max_length=120),
            F("sku", max_length=64),
            F("quantity", "integer", required=True, minimum=1),
            money("unit_price_minor", required=True),
            money("discount_minor", default=0),
            money("tax_minor", default=0),
            money("total_minor", required=True),
            money("cost_minor", default=0),
            F("fulfilled_quantity", "integer", minimum=0, default=0),
            F("returned_quantity", "integer", minimum=0, default=0),
            F("warehouse_id", "integer", nullable=True),
            F("position", "integer", minimum=0, default=0),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="service_only",
            update="fulfilment_staff",
            delete=False,
        ),
        relations=[
            BT("order", "orders", "order_id"),
            BT("variant", "product_variants", "variant_id"),
            BT("product", "products", "product_id"),
        ],
        tags=["Orders"],
    )
)

RESOURCES.append(
    resource(
        "order_events",
        "The order's timeline: what happened, when, and who or what made it happen.",
        [
            S,
            O,
            F("kind", required=True, max_length=32),
            F("message", "text", max_length=1000),
            F("actor_id", max_length=64),
            F("actor_kind", max_length=20, default="system"),
            F("data", "json"),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="service_only",
            update=False,
            delete=False,
        ),
        relations=[BT("order", "orders", "order_id")],
        tags=["Orders"],
    )
)

RESOURCES.append(
    resource(
        "payments",
        "An attempt to collect money for an order, and what the gateway said back.",
        [
            S,
            O,
            F("customer_id", "integer", nullable=True),
            F("provider", max_length=30, default="mock"),
            F("kind", enum=["charge", "authorize", "capture", "offline", "gift_card", "loyalty"], default="charge"),
            F("status", enum=["pending", "processing", "captured", "failed", "expired"], default="pending"),
            money("amount_minor", required=True),
            money("fee_minor", default=0),
            F("currency", pattern=CURRENCY, default="NGN"),
            F("reference", max_length=64),
            F("gateway_reference", max_length=96),
            F("idempotency_key", max_length=64),
            F("instrument", max_length=40),
            F("last4", max_length=4),
            F("failure_code", max_length=40),
            F("failure_message", max_length=300),
            F("capture_url", "url"),
            F("mode", enum=["test", "live"], default="test"),
            F("captured_at", "datetime", nullable=True),
            F("raw", "json"),
        ],
        ops(
            list_="store_staff",
            get="customer_or_store",
            create="service_only",
            update="service_only",
            delete=False,
        ),
        relations=[BT("order", "orders", "order_id")],
        tags=["Payments"],
        realtime=True,
    )
)

RESOURCES.append(
    resource(
        "refunds",
        "Money going back, with the reason, whether stock was returned, and who approved it.",
        [
            S,
            O,
            F("payment_id", "integer", required=True),
            F("return_id", "integer", nullable=True),
            money("amount_minor", required=True),
            F("currency", pattern=CURRENCY, default="NGN"),
            F("kind", enum=["payment", "credit_note", "gift_card"], default="payment"),
            F("status", enum=["pending", "succeeded", "failed"], default="pending"),
            F("reason", max_length=200),
            F("restock", "boolean", default=True),
            F("reference", max_length=64),
            F("gateway_reference", max_length=96),
            F("created_by", max_length=64),
            F("processed_at", "datetime", nullable=True),
        ],
        ops(
            list_="store_staff",
            get="customer_or_store",
            create="service_only",
            update="service_only",
            delete=False,
        ),
        relations=[
            BT("order", "orders", "order_id"),
            BT("payment", "payments", "payment_id"),
        ],
        tags=["Payments"],
    )
)

RESOURCES.append(
    resource(
        "shipments",
        "A parcel leaving a warehouse for an order, with its tracking.",
        [
            S,
            O,
            F("warehouse_id", "integer", nullable=True),
            F("carrier", max_length=60),
            F("service", max_length=60),
            F("status", enum=SHIPMENT_STATUSES, default="label_created"),
            F("tracking_number", max_length=96),
            F("tracking_url", "url"),
            F("label_url", "url"),
            money("cost_minor", default=0),
            F("weight_grams", "integer", minimum=0, default=0),
            F("recipient_name", max_length=120),
            F("recipient_phone", max_length=32),
            F("address", "json"),
            F("picked_by", max_length=64),
            F("shipped_at", "datetime", nullable=True),
            F("delivered_at", "datetime", nullable=True),
            F("signature_name", max_length=120),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="fulfilment_staff",
            update="fulfilment_staff",
            delete="store_manager",
        ),
        relations=[
            BT("order", "orders", "order_id"),
            BT("warehouse", "warehouses", "warehouse_id"),
            HM("tracking", "shipment_tracking", "shipment_id"),
            HM("items", "shipment_items", "shipment_id"),
        ],
        tags=["Fulfilment"],
        realtime=True,
        transformer="customer_shipment",
    )
)

RESOURCES.append(
    resource(
        "shipment_items",
        "What went out in a shipment, and how many of each.",
        [
            S,
            O,
            F("shipment_id", "integer", required=True),
            F("order_item_id", "integer", required=True),
            F("variant_id", "integer", required=True),
            F("quantity", "integer", required=True, minimum=1),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="fulfilment_staff",
            update=False,
            delete=False,
        ),
        relations=[
            BT("shipment", "shipments", "shipment_id"),
            BT("order_item", "order_items", "order_item_id"),
        ],
        tags=["Fulfilment"],
    )
)

RESOURCES.append(
    resource(
        "shipment_tracking",
        "Carrier scan events, newest last, appended by the tracking webhook.",
        [
            S,
            F("shipment_id", "integer", required=True),
            F("status", enum=SHIPMENT_STATUSES),
            F("location", max_length=160),
            F("description", max_length=300),
            F("occurred_at", "datetime", nullable=True),
            F("source", max_length=30, default="carrier"),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="service_only",
            update=False,
            delete=False,
        ),
        relations=[BT("shipment", "shipments", "shipment_id")],
        tags=["Fulfilment"],
    )
)

RESOURCES.append(
    resource(
        "returns",
        "A customer asking to send something back, and how the store resolved it.",
        [
            S,
            O,
            F("customer_id", "integer", nullable=True),
            F("user_id", max_length=64),
            F("code", max_length=24, unique=True),
            F("status", enum=RETURN_STATUSES, default="requested"),
            F("kind", enum=["refund", "exchange", "store_credit"], default="refund"),
            F("reason", required=True, max_length=40),
            F("detail", "text", max_length=2000),
            money("requested_value_minor", default=0),
            money("approved_value_minor", default=0),
            F("dropoff_code", max_length=24),
            F("label_url", "url"),
            F("reviewed_by", max_length=64),
            F("reviewed_at", "datetime", nullable=True),
            F("received_at", "datetime", nullable=True),
            F("resolved_at", "datetime", nullable=True),
            F("staff_note", "text", max_length=2000),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="authenticated",
            update="customer_or_store",
            delete=False,
        ),
        relations=[
            BT("order", "orders", "order_id"),
            HM("items", "return_items", "return_id"),
        ],
        owner="user_id",
        tags=["Returns"],
        realtime=True,
    )
)

RESOURCES.append(
    resource(
        "return_items",
        "One line being returned, and whether it made it back to stock.",
        [
            S,
            F("return_id", "integer", required=True),
            O,
            F("order_item_id", "integer", required=True),
            F("variant_id", "integer", required=True),
            F("quantity", "integer", required=True, minimum=1),
            F("condition", enum=["unopened", "opened", "damaged", "defective"], default="unopened"),
            F("restocked", "boolean", default=False),
            money("refund_value_minor", default=0),
            F("note", max_length=500),
        ],
        ops(
            list_="customer_or_store",
            get="customer_or_store",
            create="service_only",
            update="fulfilment_staff",
            delete=False,
        ),
        relations=[
            BT("return", "returns", "return_id"),
            BT("order_item", "order_items", "order_item_id"),
            BT("variant", "product_variants", "variant_id"),
        ],
        tags=["Returns"],
    )
)

RESOURCES.append(
    resource(
        "shipping_zones",
        "A place the store delivers to, with the currency and lead time it quotes in.",
        [
            S,
            F("name", required=True, max_length=120),
            F("countries", "array", required=True, items={"type": "string"}, description="ISO country codes in this zone"),
            F("currency", pattern=CURRENCY, default="NGN"),
            F("lead_min_days", "integer", minimum=0, default=1),
            F("lead_max_days", "integer", minimum=0, default=7),
            F("is_active", "boolean", default=True),
            F("position", "integer", minimum=0, default=0),
        ],
        ops(
            list_="published_read",
            get="published_read",
            create="store_manager_create",
            update="store_manager",
            delete="store_manager",
        ),
        relations=[HM("rates", "shipping_rates", "zone_id")],
        tags=["Shipping"],
        cache_ttl=300,
    )
)

RESOURCES.append(
    resource(
        "shipping_rates",
        "One way to ship into a zone: flat, free over a threshold, or by weight band.",
        [
            S,
            F("zone_id", "integer", required=True),
            F("name", required=True, max_length=120),
            F("kind", enum=["flat", "weight", "free_over"], default="flat"),
            money("price_minor", required=True),
            F("min_weight_grams", "integer", minimum=0, default=0),
            F("max_weight_grams", "integer", minimum=0, default=0),
            money("free_over_minor", default=0, description="Free shipping when the order reaches this"),
            F("delivery_estimate", max_length=60),
            F("is_active", "boolean", default=True),
            F("position", "integer", minimum=0, default=0),
        ],
        ops(
            list_="published_read",
            get="published_read",
            create="store_manager_create",
            update="store_manager",
            delete="store_manager",
        ),
        relations=[BT("zone", "shipping_zones", "zone_id")],
        tags=["Shipping"],
        cache_ttl=300,
    )
)

RESOURCES.append(
    resource(
        "tax_rates",
        "What tax applies to an order's country and tax class, in basis points.",
        [
            S,
            F("country", pattern=COUNTRY, required=True),
            F("name", required=True, max_length=60),
            F("tax_class", max_length=40, default="standard"),
            bps("rate_bps", required=True),
            F("applies_to_shipping", "boolean", default=False),
            F("is_active", "boolean", default=True),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="store_manager_create",
            update="store_manager",
            delete="store_manager",
        ),
        tags=["Shipping"],
        cache_ttl=600,
    )
)

