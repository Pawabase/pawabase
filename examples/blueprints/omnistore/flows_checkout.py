"""Checkout, payment, fulfilment, cancellation and refunds.

These are the flows where money and stock change hands, so they are written
against three rules:

* the order is created **once** — an idempotency key on the request short-circuits
  a second submit instead of double-charging a shopper;
* stock is **reserved** at checkout and only **sold** when payment is captured;
  a reservation that is never paid for is released by a schedule, so the
  storefront stops selling units that are sitting in someone's abandoned order;
* every stock movement writes its ledger row in the same ``db.transaction`` as
  the ``stock_levels`` write, so the journal can always explain the balance.

Money is recomputed here from the cart, the coupon and the shipping rate: the
client's numbers are a suggestion.
"""

from __future__ import annotations

from helpers import (
    CALC, CHECK, CREATE, DONE, EMIT, FAIL, FOREACH, GET, HTTP, ID_TOKEN, IF, LEDGER, LIST, LOG, MAIL,
    METRIC, N, NOW, NOTIFY, OP_CREATE, OP_UPDATE, ORDER_EVENT, PUBLISH, QUERY, REPLY, SECRET, SET,
    TRIGGER_EVENT, TRIGGER_HTTP, TRIGGER_WEBHOOK, TX, UPDATE, WEBHOOK, body, flow, out, param, query, vref,
)
from flows_util import CART_LINES_SQL, CART_TOTALS_SQL, DEFAULT_WAREHOUSE_SQL, ORDER_STOCK_SQL, store_guard

FLOWS = []

# ── checkout ─────────────────────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/storefront/checkout", "public")], []
_n += [
    SET("vars", store=query("store"), token=body("cart_token"), email=body("email"),
        rate_id=body("rate_id"), idempotency=body("idempotency_key")),
    CHECK("check", "{{ input.body }}", [
        {"name": "cart_token", "required": True, "max_length": 64},
        {"name": "email", "type": "email", "required": True},
        {"name": "rate_id", "type": "integer", "required": True, "minimum": 1},
        {"name": "idempotency_key", "max_length": 64},
    ]),
    FAIL("bad_request", 422, "bad_request", "Send the basket, an email and a shipping option"),
    LIST("replay", "orders", {"store_id": "{{ vars.store }}", "idempotency_key": "{{ vars.idempotency }}"}, limit=1),
    IF("already", {"truthy": "$steps.replay.output.total"}),
    REPLY("placed_before", {"order": out("replay", "output", "data", "0"), "duplicate": True}),
    LIST("find", "carts", {"token": "{{ vars.token }}", "store_id": "{{ vars.store }}", "status": "open"}, limit=1),
    IF("found", {"truthy": "$steps.find.output.total"}),
    FAIL("no_cart", 404, "no_cart", "That basket is not open"),
    SET("ctx", cart_id=out("find", "output", "data", "0", "id"),
        subtotal=out("find", "output", "data", "0", "subtotal_minor"),
        discount=out("find", "output", "data", "0", "discount_minor"),
        currency=out("find", "output", "data", "0", "currency")),
    IF("empty", {"lte": [vref("subtotal"), 0]}),
    FAIL("blank", 409, "empty_cart", "Add something to the basket first"),
]
_e += [
    ("in", "vars"), ("vars", "check"), ("check", "bad_request", "invalid"), ("check", "replay"),
    ("replay", "already"), ("already", "placed_before", "true"), ("already", "find", "false"),
    ("find", "found"), ("found", "no_cart", "false"), ("found", "ctx", "true"),
    ("ctx", "empty"), ("empty", "blank", "true"), ("empty", "sg_store", "false"),
    ("sg_store_ok", "reference", "true"),
]
_sg_nodes, _sg_edges = store_guard("sg")
_n += _sg_nodes
_e += _sg_edges
_n += [
    ID_TOKEN("reference", kind="token", length=14),
    ID_TOKEN("pay_ref", kind="token", length=16),
    LIST("rate", "shipping_rates", {"store_id": "{{ vars.store }}", "id": "{{ vars.rate_id }}", "is_active": True}, limit=1),
    IF("rate_ok", {"truthy": "$steps.rate.output.total"}),
    FAIL("no_rate", 422, "unknown_shipping", "That shipping option is not available here"),
    IF("free", {"all": [
        {"gt": ["$steps.rate.output.data.0.free_over_minor", 0]},
        {"gte": [vref("subtotal"), "$steps.rate.output.data.0.free_over_minor"]},
    ]}),
    SET("ship_free", shipping_minor=0),
    SET("ship_paid", shipping_minor="{{ steps.rate.output.data.0.price_minor }}"),
    QUERY("warehouse", DEFAULT_WAREHOUSE_SQL, ["{{ vars.store }}"]),
    QUERY("priced", CART_TOTALS_SQL, ["{{ vars.cart_id }}"]),
    QUERY("tax", "SELECT rate_bps, applies_to_shipping FROM tax_rates WHERE store_id = ? AND is_active = TRUE LIMIT 1",
          ["{{ vars.store }}"]),
]
_e += [
    ("reference", "pay_ref"),
    ("pay_ref", "rate"),
    ("rate", "rate_ok"), ("rate_ok", "no_rate", "false"), ("rate_ok", "free", "true"),
    ("free", "ship_free", "true"), ("free", "ship_paid", "false"),
    ("ship_free", "warehouse"), ("ship_paid", "warehouse"),
    ("warehouse", "priced"), ("priced", "tax"),
]
_n += [
    CALC("net", "subtract", ["{{ vars.subtotal }}", "{{ vars.discount }}"], digits=0),
    CALC("ship", "add", ["{{ vars.shipping_minor }}", 0], digits=0),
    IF("taxed", {"truthy": "$steps.tax.output.0.rate_bps"}),
    CALC("tax_raw", "multiply", ["{{ steps.net.output }}", "{{ steps.tax.output.0.rate_bps }}"], digits=0),
    CALC("tax_minor", "divide", [out("tax_raw", "output"), 10000], digits=0),
    SET("no_tax", tax_minor=0),
    CALC("total", "add", ["{{ steps.net.output }}", "{{ steps.ship.output }}"], digits=0),
    CALC("grand", "add", ["{{ steps.total.output }}", "{{ steps.tax_minor.output }}"], digits=0),
    QUERY("next_number", "SELECT COALESCE(MAX(number), 1000) + 1 AS number FROM orders WHERE store_id = ?",
          ["{{ vars.store }}"]),
    TX("place", [OP_CREATE("orders", {
        "store_id": "{{ vars.store }}",
        "number": "{{ steps.next_number.output.0.number }}",
        "reference": "{{ steps.reference.output }}",
        "email": "{{ vars.email }}",
        "user_id": "{{ auth.user_id }}",
        "cart_id": "{{ vars.cart_id }}",
        "status": "pending",
        "payment_status": "pending",
        "fulfillment_status": "unfulfilled",
        "currency": "{{ vars.currency }}",
        "subtotal_minor": "{{ vars.subtotal }}",
        "discount_minor": "{{ vars.discount }}",
        "shipping_minor": "{{ steps.ship.output }}",
        "tax_minor": "{{ steps.tax_minor.output }}",
        "total_minor": "{{ steps.grand.output }}",
        "coupon_code": "{{ steps.find.output.data.0.coupon_code }}",
        "shipping_method": "{{ steps.rate.output.data.0.name }}",
        "shipping_address": "{{ input.body.shipping_address }}",
        "billing_address": "{{ input.body.billing_address }}",
        "customer_note": "{{ input.body.customer_note }}",
        "utm_source": "{{ input.body.utm_source }}",
        "utm_campaign": "{{ input.body.utm_campaign }}",
        "warehouse_id": "{{ steps.warehouse.output.0.id }}",
        "shipping_weight_grams": "{{ steps.priced.output.0.weight_grams }}",
        "idempotency_key": "{{ vars.idempotency }}",
        "placed_at": "{{ steps.now.output }}",
    })]),
    IF("placed", {"truthy": "$steps.place.output.0.id"}),
    FAIL("not_placed", 500, "checkout_failed", "We could not open that order — nothing was charged"),
    SET("order", order_id=out("place", "output", "0", "id")),
    QUERY("lines", CART_LINES_SQL, ["{{ vars.cart_id }}"]),
    FOREACH("each", "{{ steps.lines.output }}"),
    LIST("level", "stock_levels", {"store_id": "{{ vars.store }}", "variant_id": "{{ item.variant_id }}",
                                   "warehouse_id": "{{ steps.place.output.0.warehouse_id }}"}, limit=1),
    IF("tracked", {"truthy": "$item.track_inventory"}),
    CALC("hold_for", "add", ["{{ steps.level.output.data.0.reserved }}", "{{ item.quantity }}"], digits=0),
    IF("fits", {"lte": [out("hold_for", "output"), "$steps.level.output.data.0.on_hand"]}),
    FAIL("no_stock", 409, "insufficient_stock", "One of those lines sold out while you were checking out"),
    TX("hold", [
        OP_UPDATE("stock_levels", "{{ steps.level.output.data.0.id }}", {"reserved": "{{ steps.hold_for.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ vars.store }}",
            "warehouse_id": "{{ steps.place.output.0.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "delta": 0,
            "reason": "reservation",
            "reference_type": "order",
            "reference_id": "{{ vars.order_id }}",
            "on_hand_after": "{{ steps.level.output.data.0.on_hand }}",
            "reserved_after": "{{ steps.hold_for.output }}",
            "note": "Held at checkout for {{ steps.reference.output }}",
            "actor_kind": "system",
        }),
        OP_CREATE("order_items", {
            "store_id": "{{ vars.store }}",
            "order_id": "{{ vars.order_id }}",
            "variant_id": "{{ item.variant_id }}",
            "product_id": "{{ item.product_id }}",
            "title": "{{ item.product_title }}",
            "variant_title": "{{ item.variant_title }}",
            "sku": "{{ item.sku }}",
            "quantity": "{{ item.quantity }}",
            "unit_price_minor": "{{ item.unit_price_minor }}",
            "total_minor": "{{ item.total_minor }}",
            "warehouse_id": "{{ steps.place.output.0.warehouse_id }}",
        }),
    ]),
    CREATE("loose_item", "order_items", {
        "store_id": "{{ vars.store }}",
        "order_id": "{{ vars.order_id }}",
        "variant_id": "{{ item.variant_id }}",
        "product_id": "{{ item.product_id }}",
        "title": "{{ item.product_title }}",
        "variant_title": "{{ item.variant_title }}",
        "sku": "{{ item.sku }}",
        "quantity": "{{ item.quantity }}",
        "unit_price_minor": "{{ item.unit_price_minor }}",
        "total_minor": "{{ item.total_minor }}",
    }),
]
_e += [
    ("tax", "taxed"),
    ("taxed", "tax_raw", "true"), ("tax_raw", "tax_minor"), ("tax_minor", "total"),
    ("taxed", "no_tax", "false"), ("no_tax", "total"),
    ("total", "grand"), ("grand", "next_number"), ("next_number", "place"),
    ("place", "placed"), ("placed", "not_placed", "false"), ("placed", "order", "true"),
    ("order", "lines"), ("lines", "each"),
    ("each", "level", "each"),
    ("level", "tracked"),
    ("tracked", "hold_for", "true"), ("hold_for", "fits"), ("fits", "no_stock", "false"),
    ("fits", "hold", "true"),
    ("tracked", "loose_item", "false"),
]
_n += [
    SECRET("gateway_key", "GATEWAY_API_KEY", as_="api_key"),
    HTTP("intent", "{{ steps.sg_store.output.data.0.gateway_url }}", method="post",
         headers={"Authorization": "Bearer {{ steps.gateway_key.output }}", "Content-Type": "application/json"},
         body={"amount": "{{ steps.grand.output }}", "currency": "{{ vars.currency }}",
               "reference": "{{ steps.reference.output }}", "email": "{{ vars.email }}"},
         timeout=10, retries=2),
    CREATE("payment", "payments", {
        "store_id": "{{ vars.store }}",
        "order_id": "{{ vars.order_id }}",
        "provider": "example",
        "kind": "charge",
        "status": "pending",
        "amount_minor": "{{ steps.grand.output }}",
        "currency": "{{ vars.currency }}",
        "reference": "{{ steps.pay_ref.output }}",
        "gateway_reference": "{{ steps.intent.output.body.id }}",
        "capture_url": "{{ steps.intent.output.body.checkout_url }}",
        "idempotency_key": "{{ vars.idempotency }}",
    }),
    CREATE("offline_payment", "payments", {
        "store_id": "{{ vars.store }}",
        "order_id": "{{ vars.order_id }}",
        "provider": "offline",
        "kind": "offline",
        "status": "pending",
        "amount_minor": "{{ steps.grand.output }}",
        "currency": "{{ vars.currency }}",
        "reference": "{{ steps.pay_ref.output }}",
        "failure_message": "The gateway did not answer — pay by transfer or on collection",
    }),
    UPDATE("cart_done", "carts", "{{ vars.cart_id }}", {"status": "converted", "converted_order_id": "{{ vars.order_id }}"}),
    ORDER_EVENT("timeline", "{{ vars.order_id }}", "{{ vars.store }}", "placed",
                "Order {{ steps.reference.output }} opened for {{ vars.email }}",
                actor_id="{{ auth.user_id }}", actor_kind="shopper",
                data={"total_minor": "{{ steps.grand.output }}", "currency": "{{ vars.currency }}"}),
    EMIT("announce", "order.placed", {
        "order_id": "{{ vars.order_id }}", "store_id": "{{ vars.store }}",
        "reference": "{{ steps.reference.output }}", "email": "{{ vars.email }}",
        "total_minor": "{{ steps.grand.output }}", "currency": "{{ vars.currency }}",
    }),
    PUBLISH("live", "store:{{ vars.store }}", "order.placed",
            {"order_id": "{{ vars.order_id }}", "reference": "{{ steps.reference.output }}",
             "total_minor": "{{ steps.grand.output }}"}),
    METRIC("counter", "omnistore.checkouts"),
    REPLY("reply", {
        "order": out("place", "output", "0"),
        "payment": out("payment", "output"),
        "pay_url": "{{ steps.intent.output.body.checkout_url }}",
    }),
    REPLY("offline_reply", {"order": out("place", "output", "0"), "payment": out("offline_payment", "output"), "pay_url": None}),
]
_e += [
    ("each", "gateway_key", "done"),
    ("gateway_key", "intent"),
    ("intent", "payment", "next"),
    ("intent", "offline_payment", "failed"),
    ("payment", "cart_done"), ("offline_payment", "cart_done"),
    ("cart_done", "timeline"), ("timeline", "announce"), ("announce", "live"),
    ("live", "counter"), ("counter", "reply"),
    ("offline_payment", "offline_reply"),
]
FLOWS.append(flow(
    "checkout",
    "POST /storefront/checkout: refuse a replayed submit, reprice the basket from the store's own coupon, shipping rate and tax rate, "
    "hold the stock line by line (each hold journals a reservation in the ledger), open the order, ask the gateway for a checkout URL, "
    "and close the basket. If the gateway is down the order still stands with an offline payment.",
    _n, _e, timeout=45,
))


# ── the gateway calls us back ────────────────────────────────────────────────

_n, _e = [TRIGGER_WEBHOOK("hook", "payment-gateway")], []
_n += [
    LIST("payment", "payments", {"gateway_reference": "{{ input.event.payload.reference }}"}, limit=1),
    IF("known", {"truthy": "$steps.payment.output.total"}),
    FAIL("unknown_payment", 404, "unknown_payment", "No payment with that reference"),
    IF("settled", {"eq": [out("payment", "output", "data", "0", "status"), "pending"]}),
    DONE("already_seen", {"ignored": True, "reason": "already settled"}),
    N("verdict", "control.switch", value="{{ input.event.payload.status }}",
      cases={"succeeded": "capture", "failed": "decline", "expired": "decline"}),
    UPDATE("capture", "payments", out("payment", "output", "data", "0", "id"), {
        "status": "captured",
        "captured_at": "{{ input.event.payload.paid_at }}",
        "fee_minor": "{{ input.event.payload.fee_minor }}",
        "instrument": "{{ input.event.payload.instrument }}",
        "last4": "{{ input.event.payload.last4 }}",
        "raw": "{{ input.event.payload }}",
    }),
    EMIT("captured", "payment.captured", {
        "payment_id": out("payment", "output", "data", "0", "id"),
        "order_id": out("payment", "output", "data", "0", "order_id"),
        "store_id": out("payment", "output", "data", "0", "store_id"),
        "amount_minor": out("payment", "output", "data", "0", "amount_minor"),
        "fee_minor": "{{ input.event.payload.fee_minor }}",
        "captured_at": "{{ input.event.payload.paid_at }}",
    }),
    UPDATE("decline", "payments", out("payment", "output", "data", "0", "id"), {
        "status": "failed",
        "failure_code": "{{ input.event.payload.code }}",
        "failure_message": "{{ input.event.payload.message }}",
        "raw": "{{ input.event.payload }}",
    }),
    ORDER_EVENT("declined_event", out("payment", "output", "data", "0", "order_id"),
                out("payment", "output", "data", "0", "store_id"), "payment_failed",
                "The gateway declined the charge: {{ input.event.payload.message }}"),
    EMIT("declined", "payment.failed", {
        "order_id": out("payment", "output", "data", "0", "order_id"),
        "store_id": out("payment", "output", "data", "0", "store_id"),
        "reason": "{{ input.event.payload.message }}",
    }),
    LOG("seen", "payment webhook handled", data={"reference": "{{ input.event.payload.reference }}"}),
    DONE("handled"),
]
_e += [
    ("hook", "payment"), ("payment", "known"), ("known", "unknown_payment", "false"),
    ("known", "settled", "true"), ("settled", "already_seen", "false"), ("settled", "verdict", "true"),
    ("verdict", "capture", "succeeded"), ("verdict", "decline", "failed"), ("verdict", "decline", "expired"),
    ("capture", "captured"), ("captured", "seen"), ("seen", "handled"),
    ("decline", "declined_event"), ("declined_event", "declined"), ("declined", "seen"),
]
FLOWS.append(flow(
    "payment_hook",
    "Inbound hook payment-gateway: a charge is confirmed or refused exactly once — a payment that has already settled ignores the retry.",
    _n, _e, timeout=30,
))

# ── payment captured: the order is paid ──────────────────────────────────────

_n, _e = [TRIGGER_EVENT("paid_in", "payment.captured")], []
_n += [
    GET("order", "orders", "{{ input.event.payload.order_id }}"),
    IF("unpaid", {"not_in": ["$steps.order.output.payment_status", ["paid", "refunded"]]}),
    DONE("already_paid", {"ignored": True}),
    UPDATE("settle", "orders", "{{ steps.order.output.id }}", {
        "payment_status": "paid",
        "paid_minor": "{{ input.event.payload.amount_minor }}",
        "paid_at": "{{ input.event.payload.captured_at }}",
        "status": "paid",
    }),
    ORDER_EVENT("timeline", "{{ steps.order.output.id }}", "{{ steps.order.output.store_id }}", "paid",
                "Paid {{ input.event.payload.amount_minor }} {{ steps.order.output.currency }}"),
    PUBLISH("live", "store:{{ steps.order.output.store_id }}", "order.paid",
            {"order_id": "{{ steps.order.output.id }}", "number": "{{ steps.order.output.number }}",
             "total_minor": "{{ steps.order.output.total_minor }}"}),
    EMIT("announce", "order.paid", {
        "order_id": "{{ steps.order.output.id }}", "store_id": "{{ steps.order.output.store_id }}",
        "payment_id": "{{ input.event.payload.payment_id }}",
    }),
    METRIC("counter", "omnistore.orders_paid"),
    DONE("ok"),
]
_e += [
    ("paid_in", "order"), ("order", "unpaid"), ("unpaid", "already_paid", "false"),
    ("unpaid", "settle", "true"), ("settle", "timeline"), ("timeline", "live"),
    ("live", "announce"), ("announce", "counter"), ("counter", "ok"),
]
FLOWS.append(flow(
    "payment_captured",
    "On payment.captured: settle the order once, write the timeline, tell the store live and hand off to order.paid.",
    _n, _e, timeout=30,
))

# ── paid: sell the stock, tell everyone ──────────────────────────────────────

_n, _e = [TRIGGER_EVENT("paid", "order.paid")], []
_n += [
    GET("order", "orders", "{{ input.event.payload.order_id }}"),
    QUERY("lines", ORDER_STOCK_SQL, ["{{ steps.order.output.id }}"]),
    FOREACH("each", "{{ steps.lines.output }}"),
    IF("on_book", {"exists": "$item.level_id"}),
    CALC("on_hand", "subtract", ["{{ item.on_hand }}", "{{ item.quantity }}"], digits=0),
    CALC("held_raw", "subtract", ["{{ item.reserved }}", "{{ item.quantity }}"], digits=0),
    CALC("held", "max", [out("held_raw", "output"), 0], digits=0),
    CALC("delta", "multiply", ["{{ item.quantity }}", -1], digits=0),
    TX("sell", [
        OP_UPDATE("stock_levels", "{{ item.level_id }}", {
            "on_hand": "{{ steps.on_hand.output }}", "reserved": "{{ steps.held.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ steps.order.output.store_id }}",
            "warehouse_id": "{{ item.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "delta": "{{ steps.delta.output }}",
            "reason": "sale",
            "reference_type": "order",
            "reference_id": "{{ steps.order.output.id }}",
            "on_hand_after": "{{ steps.on_hand.output }}",
            "reserved_after": "{{ steps.held.output }}",
            "actor_kind": "system",
        }),
    ]),
    CALC("available", "subtract", [out("on_hand", "output"), out("held", "output")], digits=0),
    CALC("margin", "subtract", [out("available", "output"), "{{ item.reorder_point }}"], digits=0),
    IF("low", {"lte": [out("margin", "output"), 0]}),
    EMIT("low_stock", "stock.low", {
        "store_id": "{{ steps.order.output.store_id }}", "variant_id": "{{ item.variant_id }}",
        "product_id": "{{ item.product_id }}", "available": "{{ steps.available.output }}",
    }),
    SET("untracked", moved="not stock tracked"),
]
_e += [
    ("paid", "order"), ("order", "lines"), ("lines", "each"),
    ("each", "on_book", "each"),
    ("on_book", "on_hand", "true"), ("on_hand", "held_raw"), ("held_raw", "held"), ("held", "delta"),
    ("delta", "sell"), ("sell", "available"), ("available", "margin"), ("margin", "low"),
    ("low", "low_stock", "true"),
    ("on_book", "untracked", "false"),
]
_n += [
    QUERY("lifetime", "SELECT COUNT(*) AS orders_count, COALESCE(SUM(total_minor - refunded_minor), 0) AS spent, "
                      "MAX(placed_at) AS last_at FROM orders WHERE customer_id = ? AND payment_status IN ('paid', 'partially_refunded')",
          ["{{ steps.order.output.customer_id }}"]),
    IF("a_customer", {"exists": "$steps.order.output.customer_id"}),
    UPDATE("customer", "customers", "{{ steps.order.output.customer_id }}", {
        "orders_count": "{{ steps.lifetime.output.0.orders_count }}",
        "lifetime_value_minor": "{{ steps.lifetime.output.0.spent }}",
        "last_order_at": "{{ steps.lifetime.output.0.last_at }}",
        "is_guest": False,
    }),
    IF("a_coupon", {"truthy": "$steps.order.output.coupon_code"}),
    LIST("coupon", "coupons", {"store_id": "{{ steps.order.output.store_id }}",
                               "code": "{{ steps.order.output.coupon_code }}"}, limit=1),
    CALC("uses", "add", ["{{ steps.coupon.output.data.0.usage_count }}", 1], digits=0),
    UPDATE("coupon_used", "coupons", "{{ steps.coupon.output.data.0.id }}", {"usage_count": "{{ steps.uses.output }}"}),
    CREATE("redemption", "coupon_redemptions", {
        "store_id": "{{ steps.order.output.store_id }}",
        "coupon_id": "{{ steps.coupon.output.data.0.id }}",
        "order_id": "{{ steps.order.output.id }}",
        "customer_id": "{{ steps.order.output.customer_id }}",
        "user_id": "{{ steps.order.output.user_id }}",
        "discount_minor": "{{ steps.order.output.discount_minor }}",
        "status": "consumed",
    }),
    LIST("store", "stores", {"slug": "{{ steps.order.output.store_id }}"}, limit=1),
    IF("loyalty", {"truthy": "$steps.store.output.data.0.loyalty_enabled"}),
    CALC("points_raw", "multiply", ["{{ steps.order.output.total_minor }}",
                                    "{{ steps.store.output.data.0.loyalty_earn_bps }}"], digits=0),
    CALC("points", "divide", [out("points_raw", "output"), 10000], digits=0),
    LIST("member", "customers", {"id": "{{ steps.order.output.customer_id }}"}, limit=1),
    CALC("balance", "add", ["{{ steps.member.output.data.0.loyalty_balance }}", "{{ steps.points.output }}"], digits=0),
    TX("loyal", [
        OP_CREATE("loyalty_entries", {
            "store_id": "{{ steps.order.output.store_id }}",
            "customer_id": "{{ steps.order.output.customer_id }}",
            "user_id": "{{ steps.order.output.user_id }}",
            "points": "{{ steps.points.output }}",
            "balance_after": "{{ steps.balance.output }}",
            "kind": "earn",
            "order_id": "{{ steps.order.output.id }}",
            "note": "Earned on order {{ steps.order.output.reference }}",
            "actor_id": "flow:order_paid",
        }),
        OP_UPDATE("customers", "{{ steps.order.output.customer_id }}",
                  {"loyalty_balance": "{{ steps.balance.output }}"}),
    ]),
    MAIL("receipt", "{{ steps.order.output.email }}", "order_receipt", {
        "order": "{{ steps.order.output }}",
        "store": "{{ steps.store.output.data.0.name }}",
        "lines": "{{ steps.lines.output }}",
    }),
    NOTIFY("notify", "{{ steps.order.output.store_id }}", "{{ steps.order.output.user_id }}", "order_paid",
           "Paid order {{ steps.order.output.reference }}",
           "{{ steps.order.output.email }} paid {{ steps.order.output.currency }} {{ steps.order.output.total_minor }}",
           link="/orders/{{ steps.order.output.id }}", severity="success"),
    WEBHOOK("hook", "order.paid", {
        "order_id": "{{ steps.order.output.id }}", "reference": "{{ steps.order.output.reference }}",
        "store_id": "{{ steps.order.output.store_id }}", "total_minor": "{{ steps.order.output.total_minor }}",
        "currency": "{{ steps.order.output.currency }}",
    }),
    DONE("done"),
]
_e += [
    ("each", "lifetime", "done"),
    ("lifetime", "a_customer"), ("a_customer", "customer", "true"), ("a_customer", "a_coupon", "false"),
    ("customer", "a_coupon"),
    ("a_coupon", "coupon", "true"), ("coupon", "uses"), ("uses", "coupon_used"),
    ("coupon_used", "redemption"), ("redemption", "store"), ("a_coupon", "store", "false"),
    ("store", "loyalty"), ("loyalty", "points_raw", "true"), ("points_raw", "points"),
    ("points", "member"), ("member", "balance"), ("balance", "loyal"), ("loyal", "receipt"),
    ("loyalty", "receipt", "false"),
    ("receipt", "notify"), ("notify", "hook"), ("hook", "done"),
]
FLOWS.append(flow(
    "order_paid",
    "On order.paid: sell each reserved line (on_hand down, reservation released, the ledger journalled in the same transaction, the reorder point "
    "re-checked), update lifetime value and loyalty, consume the coupon, email the receipt, notify the store and fire the outbound webhook.",
    _n, _e, timeout=90,
))


# ── an offline payment, recorded by staff ────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/orders/{id}/mark-paid", "fulfilment_staff")], []
_n += [
    GET("order", "orders", param("id")),
    FAIL("no_order", 404, "no_order", "No such order"),
    IF("unpaid", {"eq": [out("order", "output", "payment_status"), "pending"]}),
    FAIL("already_paid", 409, "already_paid", "That order is already paid — issue a refund instead"),
    NOW("now"),
    ID_TOKEN("ref", kind="token", length=12),
    CREATE("payment", "payments", {
        "store_id": "{{ steps.order.output.store_id }}",
        "order_id": "{{ steps.order.output.id }}",
        "customer_id": "{{ steps.order.output.customer_id }}",
        "provider": "offline",
        "kind": "offline",
        "status": "captured",
        "amount_minor": "{{ steps.order.output.total_minor }}",
        "currency": "{{ steps.order.output.currency }}",
        "reference": "{{ steps.ref.output }}",
        "instrument": "{{ input.body.instrument }}",
        "captured_at": "{{ steps.now.output }}",
    }),
    ORDER_EVENT("timeline", "{{ steps.order.output.id }}", "{{ steps.order.output.store_id }}", "payment_recorded",
                "Recorded by {{ auth.user_id }}: {{ input.body.instrument }} {{ input.body.note }}",
                actor_id="{{ auth.user_id }}", actor_kind="staff"),
    EMIT("captured", "payment.captured", {
        "payment_id": out("payment", "output", "id"),
        "order_id": "{{ steps.order.output.id }}",
        "store_id": "{{ steps.order.output.store_id }}",
        "amount_minor": "{{ steps.order.output.total_minor }}",
        "fee_minor": 0,
        "captured_at": "{{ steps.now.output }}",
    }),
    REPLY("reply", out("payment", "output")),
]
_e += [
    ("in", "order"), ("order", "unpaid"), ("unpaid", "already_paid", "false"),
    ("unpaid", "now", "true"), ("now", "ref"), ("ref", "payment"), ("payment", "timeline"),
    ("timeline", "captured"), ("captured", "reply"),
]
FLOWS.append(flow(
    "mark_paid",
    "POST /orders/{id}/mark-paid: staff record cash, transfer or card-on-collection, and the order goes through the same paid path as a gateway capture.",
    _n, _e, timeout=30,
))

# ── cancel, and give the stock back ─────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/orders/{id}/cancel", "store_manager")], []
_n += [
    GET("order", "orders", param("id")),
    FAIL("no_order", 404, "no_order", "No such order"),
    IF("cancellable", {"in": [out("order", "output", "status"), ["pending", "paid", "processing"]]}),
    FAIL("too_late", 409, "cannot_cancel", "This order has shipped or closed — refund or return it instead"),
    IF("paid_already", {"eq": [out("order", "output", "payment_status"), "pending"]}),
    FAIL("refund_instead", 409, "refund_required", "This order is paid: refund it, which restocks it too"),
    QUERY("lines", ORDER_STOCK_SQL, ["{{ steps.order.output.id }}"]),
    FOREACH("each", "{{ steps.lines.output }}"),
    IF("on_book", {"exists": "$item.level_id"}),
    CALC("held_raw", "subtract", ["{{ item.reserved }}", "{{ item.quantity }}"], digits=0),
    CALC("held", "max", [out("held_raw", "output"), 0], digits=0),
    TX("release", [
        OP_UPDATE("stock_levels", "{{ item.level_id }}", {"reserved": "{{ steps.held.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ steps.order.output.store_id }}",
            "warehouse_id": "{{ item.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "delta": 0,
            "reason": "release",
            "reference_type": "order",
            "reference_id": "{{ steps.order.output.id }}",
            "on_hand_after": "{{ item.on_hand }}",
            "reserved_after": "{{ steps.held.output }}",
            "note": "Reservation released by cancellation",
            "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    SET("nothing_held", released="nothing on book"),
]
_e += [
    ("in", "order"), ("order", "cancellable"), ("cancellable", "too_late", "false"),
    ("cancellable", "paid_already", "true"), ("paid_already", "refund_instead", "false"),
    ("paid_already", "lines", "true"),
    ("lines", "each"), ("each", "on_book", "each"),
    ("on_book", "held_raw", "true"), ("held_raw", "held"), ("held", "release"),
    ("on_book", "nothing_held", "false"),
]
_n += [
    NOW("now2"),
    UPDATE("cancel", "orders", "{{ steps.order.output.id }}", {
        "status": "cancelled",
        "cancelled_at": "{{ steps.now2.output }}",
        "cancel_reason": "{{ input.body.reason }}",
    }),
    ORDER_EVENT("timeline", "{{ steps.order.output.id }}", "{{ steps.order.output.store_id }}", "cancelled",
                "Cancelled by {{ auth.user_id }}: {{ input.body.reason }}",
                actor_id="{{ auth.user_id }}", actor_kind="staff"),
    MAIL("mail", "{{ steps.order.output.email }}", "order_cancelled", {"order": "{{ steps.cancel.output }}"}),
    EMIT("announce", "order.cancelled", {
        "order_id": "{{ steps.order.output.id }}", "store_id": "{{ steps.order.output.store_id }}",
        "reason": "{{ input.body.reason }}",
    }),
    PUBLISH("live", "store:{{ steps.order.output.store_id }}", "order.cancelled",
            {"order_id": "{{ steps.order.output.id }}"}),
    REPLY("reply", out("cancel", "output")),
]
_e += [
    ("each", "now2", "done"), ("now2", "cancel"), ("cancel", "timeline"),
    ("timeline", "mail"), ("mail", "announce"), ("announce", "live"), ("live", "reply"),
]
FLOWS.append(flow(
    "cancel_order",
    "POST /orders/{id}/cancel: only an unpaid, unshipped order cancels here; every reservation it held is released and journalled, and everyone is told.",
    _n, _e, timeout=60,
))


# ── pick, pack, ship ─────────────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/orders/{id}/fulfil", "fulfilment_staff")], []
_n += [
    GET("order", "orders", param("id")),
    FAIL("no_order", 404, "no_order", "No such order"),
    IF("payable", {"in": [out("order", "output", "payment_status"), ["paid", "partially_refunded"]]}),
    FAIL("unpaid", 409, "unpaid", "Take payment before shipping this order"),
    IF("open", {"not_in": [out("order", "output", "fulfillment_status"), ["fulfilled", "returned"]]}),
    FAIL("shipped", 409, "already_shipped", "This order has already gone out"),
    NOW("now"),
    CREATE("shipment", "shipments", {
        "store_id": "{{ steps.order.output.store_id }}",
        "order_id": "{{ steps.order.output.id }}",
        "warehouse_id": "{{ steps.order.output.warehouse_id }}",
        "carrier": "{{ input.body.carrier }}",
        "service": "{{ input.body.service }}",
        "status": "label_created",
        "tracking_number": "{{ input.body.tracking_number }}",
        "tracking_url": "{{ input.body.tracking_url }}",
        "label_url": "{{ input.body.label_url }}",
        "cost_minor": "{{ input.body.cost_minor }}",
        "weight_grams": "{{ steps.order.output.shipping_weight_grams }}",
        "recipient_name": "{{ input.body.recipient_name }}",
        "recipient_phone": "{{ steps.order.output.phone }}",
        "address": "{{ steps.order.output.shipping_address }}",
        "picked_by": "{{ auth.user_id }}",
        "shipped_at": "{{ steps.now.output }}",
    }),
    TX("paperwork", [
        OP_CREATE("shipment_tracking", {
            "store_id": "{{ steps.order.output.store_id }}",
            "shipment_id": "{{ steps.shipment.output.id }}",
            "status": "label_created",
            "location": "{{ input.body.origin }}",
            "description": "Label created and picked for the van",
            "occurred_at": "{{ steps.now.output }}",
            "source": "staff",
        }),
        OP_CREATE("order_events", {
            "store_id": "{{ steps.order.output.store_id }}",
            "order_id": "{{ steps.order.output.id }}",
            "kind": "shipped",
            "message": "Shipped with {{ input.body.carrier }} — {{ input.body.tracking_number }}",
            "actor_id": "{{ auth.user_id }}",
            "actor_kind": "staff",
        }),
        OP_UPDATE("orders", "{{ steps.order.output.id }}", {
            "fulfillment_status": "fulfilled",
            "status": "fulfilled",
            "fulfilled_at": "{{ steps.now.output }}",
        }),
    ]),
    MAIL("mail", "{{ steps.order.output.email }}", "shipping_update", {
        "order": "{{ steps.order.output }}",
        "shipment": "{{ steps.ship.output.0 }}",
    }),
    EMIT("announce", "order.fulfilled", {
        "order_id": "{{ steps.order.output.id }}", "store_id": "{{ steps.order.output.store_id }}",
        "shipment_id": "{{ steps.shipment.output.id }}",
    }),
    PUBLISH("live", "store:{{ steps.order.output.store_id }}", "order.fulfilled",
            {"order_id": "{{ steps.order.output.id }}"}),
    REPLY("reply", {"shipment": out("shipment", "output"), "order": out("paperwork", "output", "2")}),
]
_e += [
    ("in", "order"), ("order", "payable"), ("payable", "unpaid", "false"),
    ("payable", "open", "true"), ("open", "shipped", "false"),
    ("open", "now", "true"), ("now", "shipment"), ("shipment", "paperwork"),
    ("paperwork", "mail"),
    ("announce", "live"), ("live", "reply"),
]
FLOWS.append(flow(
    "fulfil_order",
    "POST /orders/{id}/fulfil: open a shipment, then write its first scan, the order timeline and the order's fulfilled state in one transaction; the "
    "customer gets the tracking mail, the store gets it live, and webhooks get order.fulfilled.",
    _n, _e, timeout=60,
))


# ── money back, stock back ───────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/orders/{id}/refunds", "store_manager")], []
_n += [
    GET("order", "orders", param("id")),
    FAIL("no_order", 404, "no_order", "No such order"),
    LIST("payment", "payments", {"store_id": "{{ steps.order.output.store_id }}",
                                 "order_id": "{{ steps.order.output.id }}", "status": "captured"}, limit=1),
    IF("payable", {"truthy": "$steps.payment.output.total"}),
    FAIL("nothing_to_refund", 409, "nothing_to_refund", "Nothing was captured on this order"),
    CALC("left", "subtract", [out("order", "output", "total_minor"), out("order", "output", "refunded_minor")], digits=0),
    IF("fits", {"lte": ["$input.body.amount_minor", "$steps.left.output"]}),
    FAIL("too_much", 422, "over_refund", "That is more than is left to refund on this order"),
    LIST("store", "stores", {"slug": "{{ steps.order.output.store_id }}"}, limit=1),
    SECRET("gateway_key", "GATEWAY_API_KEY", as_="api_key"),
    ID_TOKEN("ref_ref", kind="token", length=12),
    HTTP("gateway_refund", "{{ steps.store.output.data.0.gateway_url }}/refunds", method="post",
         headers={"Authorization": "Bearer {{ steps.gateway_key.output }}", "Content-Type": "application/json"},
         body={"charge": "{{ steps.payment.output.data.0.gateway_reference }}",
               "amount": "{{ input.body.amount_minor }}", "reason": "{{ input.body.reason }}"},
         timeout=15, retries=3),
    FAIL("gateway_no", 502, "refund_failed", "The gateway refused the refund — nothing was changed"),
    NOW("now"),
    TX("refund", [
        OP_CREATE("refunds", {
            "store_id": "{{ steps.order.output.store_id }}",
            "order_id": "{{ steps.order.output.id }}",
            "payment_id": "{{ steps.payment.output.data.0.id }}",
            "amount_minor": "{{ input.body.amount_minor }}",
            "currency": "{{ steps.order.output.currency }}",
            "status": "succeeded",
            "reason": "{{ input.body.reason }}",
            "restock": "{{ input.body.restock }}",
            "reference": "{{ steps.ref_ref.output }}",
            "gateway_reference": "{{ steps.gateway_refund.output.body.id }}",
            "created_by": "{{ auth.user_id }}",
            "processed_at": "{{ steps.now.output }}",
        }),
        OP_CREATE("order_events", {
            "store_id": "{{ steps.order.output.store_id }}",
            "order_id": "{{ steps.order.output.id }}",
            "kind": "refunded",
            "message": "Refunded {{ input.body.amount_minor }} — {{ input.body.reason }}",
            "actor_id": "{{ auth.user_id }}",
            "actor_kind": "staff",
        }),
    ]),
]
_e += [
    ("in", "order"), ("order", "payment"), ("payment", "payable"),
    ("payable", "nothing_to_refund", "false"), ("payable", "left", "true"),
    ("left", "fits"), ("fits", "too_much", "false"), ("fits", "store", "true"),
    ("store", "gateway_key"), ("gateway_key", "ref_ref"), ("ref_ref", "gateway_refund"),
    ("gateway_refund", "gateway_no", "failed"), ("gateway_refund", "now", "next"),
    ("now", "refund"),
]
_n += [
    CALC("refunded", "add", [out("order", "output", "refunded_minor"), "{{ input.body.amount_minor }}"], digits=0),
    IF("full", {"truthy": "$input.body.full"}),
    UPDATE("order_state", "orders", "{{ steps.order.output.id }}", {
        "refunded_minor": "{{ steps.refunded.output }}", "payment_status": "refunded", "status": "refunded",
    }),
    UPDATE("order_partial", "orders", "{{ steps.order.output.id }}", {
        "refunded_minor": "{{ steps.refunded.output }}", "payment_status": "partially_refunded",
    }),
    QUERY("lines", ORDER_STOCK_SQL, ["{{ steps.order.output.id }}"]),
    IF("restock", {"truthy": "$input.body.restock"}),
    FOREACH("each", "{{ steps.lines.output }}"),
    IF("on_book", {"exists": "$item.level_id"}),
    CALC("back", "add", ["{{ item.on_hand }}", "{{ item.quantity }}"], digits=0),
    TX("restocked", [
        OP_UPDATE("stock_levels", "{{ item.level_id }}", {"on_hand": "{{ steps.back.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ steps.order.output.store_id }}",
            "warehouse_id": "{{ item.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "delta": "{{ item.quantity }}",
            "reason": "return_restock",
            "reference_type": "order",
            "reference_id": "{{ steps.order.output.id }}",
            "on_hand_after": "{{ steps.back.output }}",
            "note": "Refund with restock",
            "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    MAIL("mail", "{{ steps.order.output.email }}", "refund_issued", {
        "order": "{{ steps.order.output }}",
        "refund": "{{ steps.refund.output.0 }}",
    }),
    EMIT("announce", "order.refunded", {
        "order_id": "{{ steps.order.output.id }}", "store_id": "{{ steps.order.output.store_id }}",
        "amount_minor": "{{ input.body.amount_minor }}",
    }),
    WEBHOOK("hook", "order.refunded", {
        "order_id": "{{ steps.order.output.id }}", "reference": "{{ steps.order.output.reference }}",
        "amount_minor": "{{ input.body.amount_minor }}", "currency": "{{ steps.order.output.currency }}",
    }),
    REPLY("reply", {"refund": out("refund", "output", "0"),
                    "refunded_minor": "{{ steps.refunded.output }}"}),
]
_e += [
    ("refund", "refunded"), ("refunded", "full"),
    ("full", "order_state", "true"), ("full", "order_partial", "false"),
    ("order_state", "lines"), ("order_partial", "lines"), ("lines", "restock"),
    ("restock", "each", "true"), ("each", "on_book", "each"),
    ("on_book", "back", "true"), ("back", "restocked"),
    ("on_book", "mail", "false"), ("restocked", "mail"),
    ("restock", "mail", "false"),
    ("mail", "announce"), ("announce", "hook"), ("hook", "reply"),
]
FLOWS.append(flow(
    "refund_order",
    "POST /orders/{id}/refunds: prove the amount still fits, ask the gateway for the money back, journal the refund and the timeline in one "
    "transaction, put the stock back when the goods are coming, then tell the customer and the store's webhook.",
    _n, _e, timeout=60,
))


# ── the carrier tells us where the parcel is ─────────────────────────────────

_n, _e = [TRIGGER_WEBHOOK("hook", "carrier-tracking")], []
_n += [
    LIST("shipment", "shipments", {"tracking_number": "{{ input.event.payload.tracking_number }}"}, limit=1),
    IF("known", {"truthy": "$steps.shipment.output.total"}),
    FAIL("unknown_shipment", 404, "unknown_shipment", "We don't ship that number"),
    GET("order", "orders", "{{ steps.shipment.output.data.0.order_id }}"),
    NOW("now"),
    TX("scan", [
        OP_CREATE("shipment_tracking", {
            "store_id": "{{ steps.shipment.output.data.0.store_id }}",
            "shipment_id": "{{ steps.shipment.output.data.0.id }}",
            "status": "{{ input.event.payload.status }}",
            "location": "{{ input.event.payload.location }}",
            "description": "{{ input.event.payload.description }}",
            "occurred_at": "{{ steps.now.output }}",
            "source": "carrier",
        }),
        OP_UPDATE("shipments", "{{ steps.shipment.output.data.0.id }}", {
            "status": "{{ input.event.payload.status }}",
            "delivered_at": "{{ steps.now.output }}",
            "signature_name": "{{ input.event.payload.signature }}",
        }),
        OP_CREATE("order_events", {
            "store_id": "{{ steps.shipment.output.data.0.store_id }}",
            "order_id": "{{ steps.shipment.output.data.0.order_id }}",
            "kind": "tracking",
            "message": "{{ input.event.payload.status }} — {{ input.event.payload.location }}",
        }),
    ]),
    IF("delivered", {"eq": ["$input.event.payload.status", "delivered"]}),
    UPDATE("order_delivered", "orders", "{{ steps.shipment.output.data.0.order_id }}",
           {"fulfillment_status": "fulfilled"}),
    MAIL("mail", "{{ steps.order.output.email }}", "delivery_done", {
        "order": "{{ steps.order.output }}", "shipment": "{{ steps.scan.output.1 }}",
    }),
    EMIT("announce", "shipment.updated", {
        "shipment_id": "{{ steps.shipment.output.data.0.id }}",
        "order_id": "{{ steps.shipment.output.data.0.order_id }}",
        "store_id": "{{ steps.shipment.output.data.0.store_id }}",
        "status": "{{ input.event.payload.status }}",
    }),
    PUBLISH("live", "store:{{ steps.shipment.output.data.0.store_id }}", "shipment.updated",
            {"shipment_id": "{{ steps.shipment.output.data.0.id }}",
             "status": "{{ input.event.payload.status }}"}),
    DONE("seen"),
]
_e += [
    ("hook", "shipment"), ("shipment", "known"), ("known", "unknown_shipment", "false"),
    ("known", "order", "true"), ("order", "now"), ("now", "scan"), ("scan", "delivered"),
    ("delivered", "order_delivered", "true"), ("order_delivered", "mail"),
    ("delivered", "announce", "false"), ("mail", "announce"),
    ("announce", "live"), ("live", "seen"),
]
FLOWS.append(flow(
    "shipment_scan",
    "Inbound hook carrier-tracking: append the scan, move the shipment, note it on the order, and only say 'it has arrived' when it really has.",
    _n, _e, timeout=30,
))

# __APPEND__
