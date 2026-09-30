"""Back-office flows: the numbers staff read, and the buttons they press.

Two things are worth noticing here. The dashboard reads from a 30-second cache
and only scans ``daily_stats`` when it misses, so the page never walks an order
table. ``product_publish`` refuses a product with no price before it goes live —
the kind of check that belongs in one place, next to the publish button.
"""

from __future__ import annotations

from helpers import (
    CALC, CACHE_GET, CACHE_SET, CREATE, DONE, EMIT, FAIL, GET, IF, LIST, MAIL, N, NOW, NOTIFY,
    OP_CREATE,
    OP_UPDATE, ORDER_EVENT, PUBLISH, QUERY, REPLY, SET, TRIGGER_EVENT, TRIGGER_HTTP, TX, UPDATE,
    WEBHOOK, body, flow, out, param, query,
)
from flows_util import DAILY_ROLLUP_SQL, UNITS_SQL

FLOWS = []

# ── the dashboard the store opens first ─────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "GET", "/merchant/dashboard", "store_staff")], []
_n += [
    SET("vars", store=body("store_id")),
    CACHE_GET("cached", "dash:{{ vars.store }}"),
    REPLY("from_cache", "{{ steps.cached.output.value }}"),
    QUERY("money", DAILY_ROLLUP_SQL, ["{{ vars.store }}", "1970-01-01", "9999-12-31"]),
    QUERY("units", UNITS_SQL, ["{{ vars.store }}", "1970-01-01", "9999-12-31"]),
    QUERY("open_orders", "SELECT COUNT(*) AS n FROM orders WHERE store_id = ? AND status IN ('pending', 'paid', 'processing')",
          ["{{ vars.store }}"]),
    QUERY("backlog", "SELECT COUNT(*) AS n FROM returns WHERE store_id = ? AND status IN ('requested', 'approved')",
          ["{{ vars.store }}"]),
    QUERY("open_tickets", "SELECT COUNT(*) AS n FROM tickets WHERE store_id = ? AND status IN ('open', 'pending')",
          ["{{ vars.store }}"]),
    QUERY("products_live", "SELECT COUNT(*) AS n FROM products WHERE store_id = ? AND status = 'active'",
          ["{{ vars.store }}"]),
    QUERY("shoppers", "SELECT COUNT(*) AS n FROM customers WHERE store_id = ?", ["{{ vars.store }}"]),
    QUERY("best", "SELECT p.id, p.title, SUM(oi.quantity) AS sold FROM order_items oi "
                  "JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id "
                  "WHERE o.store_id = ? GROUP BY p.id, p.title ORDER BY sold DESC LIMIT 5",
          ["{{ vars.store }}"]),
    N("bundle", "control.merge", objects=[{
        "orders": out("money", "output", "0"), "units": out("units", "output", "0"),
        "open_orders": out("open_orders", "output", "0", "n"),
        "returns_pending": out("backlog", "output", "0", "n"),
        "tickets_open": out("open_tickets", "output", "0", "n"),
        "products_live": out("products_live", "output", "0", "n"),
        "customers": out("shoppers", "output", "0", "n"),
        "best_sellers": out("best", "output"),
    }]),
    CACHE_SET("store_cache", "dash:{{ vars.store }}", "{{ steps.bundle.output }}", ttl=30,
              tags=["dashboard", "orders", "customers"]),
    REPLY("reply", "{{ steps.bundle.output }}"),
]
_e += [
    ("in", "vars"), ("vars", "cached"), ("cached", "from_cache", "hit"),
    ("cached", "money", "miss"), ("money", "units"), ("units", "open_orders"),
    ("open_orders", "backlog"), ("backlog", "open_tickets"), ("open_tickets", "products_live"),
    ("products_live", "shoppers"), ("shoppers", "best"), ("best", "bundle"),
    ("bundle", "store_cache"), ("store_cache", "reply"),
]
FLOWS.append(flow(
    "store_dashboard",
    "GET /merchant/dashboard: headline numbers from one cached read; a miss rebuilds them with eight targeted queries and a top-sellers rollup.",
    _n, _e, timeout=20,
))


# ── one order, for the back office ──────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "GET", "/merchant/orders/{id}", "store_staff")], []
_n += [
    GET("order", "orders", param("id")),
    FAIL("no_order", 404, "no_order", "No such order in this store"),
    LIST("items", "order_items", {"store_id": "{{ steps.order.output.store_id }}",
                                  "order_id": "{{ steps.order.output.id }}"}, limit=100),
    LIST("pays", "payments", {"store_id": "{{ steps.order.output.store_id }}",
                              "order_id": "{{ steps.order.output.id }}"}, limit=50),
    LIST("ships", "shipments", {"store_id": "{{ steps.order.output.store_id }}",
                                "order_id": "{{ steps.order.output.id }}"}, limit=25),
    LIST("refs", "refunds", {"store_id": "{{ steps.order.output.store_id }}",
                             "order_id": "{{ steps.order.output.id }}"}, limit=25),
    LIST("evs", "order_events", {"store_id": "{{ steps.order.output.store_id }}",
                                 "order_id": "{{ steps.order.output.id }}"}, limit=100),
    LIST("rets", "returns", {"store_id": "{{ steps.order.output.store_id }}",
                             "order_id": "{{ steps.order.output.id }}"}, limit=25),
    N("customer", "control.if", condition={"exists": "$steps.order.output.customer_id"}),
    LIST("shopper", "customers", {"id": "{{ steps.order.output.customer_id }}"}, limit=1),
    N("assemble", "control.merge", objects=[{
        "order": "{{ steps.order.output }}",
        "items": out("items", "output", "data"),
        "payments": out("pays", "output", "data"),
        "shipments": out("ships", "output", "data"),
        "refunds": out("refs", "output", "data"),
        "returns": out("rets", "output", "data"),
        "timeline": out("evs", "output", "data"),
        "customer": out("shopper", "output", "data", "0"),
    }]),
    REPLY("reply", "{{ steps.assemble.output }}"),
]
_e += [
    ("in", "order"), ("order", "items"), ("items", "pays"), ("pays", "ships"),
    ("ships", "refs"), ("refs", "rets"), ("rets", "evs"),
    ("evs", "customer"), ("customer", "shopper", "true"), ("customer", "assemble", "false"),
    ("shopper", "assemble"), ("assemble", "reply"),
]
FLOWS.append(flow(
    "order_detail",
    "GET /merchant/orders/{id}: everything staff need about one order — lines, money, parcels, refunds, returns, customer and timeline — "
    "assembled from seven targeted reads.",
    _n, _e, timeout=20,
))

# ── publish a product, or refuse to ─────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/catalog/products/{id}/publish", "store_editor")], []
_n += [
    GET("product", "products", param("id")),
    FAIL("no_product", 404, "no_such_product", "No such product"),
    LIST("variants", "product_variants", {"store_id": "{{ steps.product.output.store_id }}",
                                          "product_id": "{{ steps.product.output.id }}", "status": "active"}, limit=100),
    IF("has_variants", {"gt": ["$steps.variants.output.total", 0]}),
    FAIL("no_variants", 422, "unpublishable", "Give it at least one active variant before it goes live"),
    LIST("priced", "product_variants", {"store_id": "{{ steps.product.output.store_id }}",
                                        "product_id": "{{ steps.product.output.id }}"}, limit=100),
    N("priced_ok", "control.if", condition={"gt": ["$steps.priced.output.total", 0]}),
    FAIL("no_price", 422, "unpriceable", "Every live product needs a price"),
    NOW("now"),
    UPDATE("publish", "products", "{{ steps.product.output.id }}", {
        "status": "active", "is_published": True, "published_at": "{{ steps.now.output }}",
        "published_by": "{{ auth.user_id }}",
    }),
    N("tags", "cache.invalidate", tags=["products", "catalog", "product:{{ steps.product.output.id }}"]),
    EMIT("announce", "product.published", {
        "product_id": "{{ steps.product.output.id }}", "store_id": "{{ steps.product.output.store_id }}",
        "handle": "{{ steps.product.output.handle }}",
    }),
    PUBLISH("live", "store:{{ steps.product.output.store_id }}", "product.published",
            {"product_id": "{{ steps.product.output.id }}", "title": "{{ steps.product.output.title }}"}),
    REPLY("reply", out("publish", "output")),
]
_e += [
    ("in", "product"), ("product", "variants"), ("variants", "has_variants"),
    ("has_variants", "no_variants", "false"), ("has_variants", "priced", "true"),
    ("priced", "priced_ok"), ("priced_ok", "no_price", "false"), ("priced_ok", "now", "true"),
    ("now", "publish"), ("publish", "tags"),
    ("tags", "announce"), ("announce", "live"), ("live", "reply"),
]
FLOWS.append(flow(
    "product_publish",
    "POST /catalog/products/{id}/publish: go live only when there is an active variant with a price; then publish, drop the cache tags and say so.",
    _n, _e, timeout=20,
))


# ── moderate a review, then re-score the product ─────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/content/reviews/{id}/moderate", "moderator")], []
_n += [
    GET("review", "reviews", param("id")),
    FAIL("no_review", 404, "no_such_review", "No such review"),
    NOW("now"),
    UPDATE("decision", "reviews", "{{ steps.review.output.id }}", {
        "status": "{{ input.body.status }}",
        "staff_reply": "{{ input.body.reply }}",
        "moderated_by": "{{ auth.user_id }}",
        "moderated_at": "{{ steps.now.output }}",
    }),
    QUERY("scores", "SELECT COUNT(*) AS rating_count, "
                    "COALESCE(CAST(AVG(rating) * 100 AS INTEGER), 0) AS rating_avg "
                    "FROM reviews WHERE product_id = ? AND status = 'approved'",
          ["{{ steps.review.output.product_id }}"]),
    UPDATE("rescore", "products", "{{ steps.review.output.product_id }}", {
        "rating_count": "{{ steps.scores.output.0.rating_count }}",
        "rating_avg": "{{ steps.scores.output.0.rating_avg }}",
    }),
    IF("not_ok", {"ne": ["$input.body.status", "approved"]}),
    UPDATE("moderated", "reviews", "{{ steps.review.output.id }}",
           {"status": "{{ input.body.status }}", "moderated_by": "{{ auth.user_id }}"}),
    EMIT("announce", "review.moderated", {
        "review_id": "{{ steps.review.output.id }}", "product_id": "{{ steps.review.output.product_id }}",
        "status": "{{ input.body.status }}", "store_id": "{{ steps.review.output.store_id }}",
    }),
    REPLY("reply", {"review": out("decision", "output"),
                    "product": out("rescore", "output")}),
]
_e += [
    ("in", "review"), ("review", "now"), ("now", "decision"), ("decision", "scores"),
    ("scores", "rescore"), ("rescore", "not_ok"),
    ("not_ok", "moderated", "true"), ("moderated", "announce"),
    ("not_ok", "announce", "false"), ("announce", "reply"),
]
FLOWS.append(flow(
    "moderate_review",
    "POST /content/reviews/{id}/moderate: record the decision and the reply, then recompute the product's average and count from approved reviews only.",
    _n, _e, timeout=20,
))

# ── answer a shopper ─────────────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/support/tickets/{id}/reply", "support_desk")], []
_n += [
    GET("ticket", "tickets", param("id")),
    FAIL("no_ticket", 404, "no_such_ticket", "No such ticket"),
    IF("live", {"not_in": ["$steps.ticket.output.status", ["closed"]]}),
    FAIL("gone", 409, "ticket_closed", "That conversation is closed"),
    NOW("now"),
    TX("written", [
        OP_CREATE("ticket_messages", {
            "store_id": "{{ steps.ticket.output.store_id }}",
            "ticket_id": "{{ steps.ticket.output.id }}",
            "author_id": "{{ auth.user_id }}",
            "author_kind": "staff",
            "body": "{{ input.body.message }}",
            "is_internal": "{{ input.body.internal }}",
        }),
        OP_UPDATE("tickets", "{{ steps.ticket.output.id }}", {
            "status": "{{ input.body.status }}",
            "last_message_at": "{{ steps.now.output }}",
            "first_response_at": "{{ steps.ticket.output.first_response_at }}",
        }),
    ]),
    IF("public_reply", {"eq": ["$input.body.internal", False]}),
    MAIL("mail", "{{ steps.ticket.output.requester_email }}", "support_reply", {
        "ticket": "{{ steps.ticket.output }}", "message": "{{ input.body.message }}",
    }),
    NOTIFY("notify", "{{ steps.ticket.output.store_id }}", "{{ steps.ticket.output.user_id }}",
           "ticket_replied", "Reply sent: {{ steps.ticket.output.subject }}",
           "{{ input.body.message | first }}", link="/support/{{ steps.ticket.output.id }}"),
    EMIT("announce", "ticket.replied", {
        "ticket_id": "{{ steps.ticket.output.id }}", "store_id": "{{ steps.ticket.output.store_id }}",
        "status": "{{ input.body.status }}",
    }),
    REPLY("outcome", out("written", "output", "1")),
]
_e += [
    ("in", "ticket"), ("ticket", "live"), ("live", "gone", "false"), ("live", "now", "true"),
    ("now", "written"), ("written", "public_reply"),
    ("public_reply", "mail", "true"), ("mail", "notify"),
    ("public_reply", "notify", "false"), ("notify", "announce"), ("announce", "outcome"),
]
FLOWS.append(flow(
    "staff_reply",
    "POST /support/tickets/{id}/reply: log the message and move the ticket in one transaction; an internal note stays in the back office, "
    "a public one is mailed to the shopper.",
    _n, _e, timeout=30,
))

# __APPEND__
