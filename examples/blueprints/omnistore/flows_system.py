"""Scheduled and event-driven flows: the housekeeping nobody should have to press.

These run without a caller — a schedule ticks, an event arrives — so none of
them answers a client. Each ends in a state write or a plain ``done``: a run
that only replied would have nowhere to reply to.
"""

from __future__ import annotations

from helpers import (
    CALC, CREATE, DONE, EMIT, FAIL, FOREACH, IF, LIST, LOG, MAIL, N, NOW, NOTIFY, OP_CREATE, OP_UPDATE,
    PUBLISH, QUERY, REPLY, SET, TRIGGER_EVENT, TRIGGER_HTTP, TRIGGER_SCHEDULE, TX, UPDATE, WEBHOOK,
    body, flow, out, param, query,
)
from flows_util import DAILY_ROLLUP_SQL, UNITS_SQL

FLOWS = []

# ── a variant has run down ───────────────────────────────────────────────────

_n, _e = [TRIGGER_EVENT("in", "stock.low")], []
_n += [
    LIST("variant", "product_variants", {"store_id": "{{ input.event.payload.store_id }}",
                                         "id": "{{ input.event.payload.variant_id }}"}, limit=1),
    IF("known", {"truthy": "$steps.variant.output.total"}),
    DONE("ignore", {"skipped": True}),
    LIST("store", "stores", {"ref": "{{ input.event.payload.store_id }}"}, limit=1),
    IF("watching", {"gt": ["$steps.variant.output.data.0.reorder_point", 0]}),
    LOG("note", "low stock", level="warning",
        data={"variant_id": "{{ input.event.payload.variant_id }}", "available": "{{ input.event.payload.available }}"},
        category="inventory"),
    NOTIFY("notify", "{{ input.event.payload.store_id }}", "{{ steps.store.output.data.0.owner_user_id }}",
           "low_stock", "Running low: {{ steps.variant.output.data.0.title }}",
           "{{ input.event.payload.available }} left of {{ steps.variant.output.data.0.sku }}",
           link="/products/{{ input.event.payload.product_id }}", severity="warning"),
    MAIL("mail", "{{ steps.store.output.data.0.support_email | default:steps.store.output.data.0.email }}",
         "low_stock", {
             "product": "{{ steps.variant.output.data.0.title }}",
             "variant": "{{ steps.variant.output.data.0.sku }}",
             "stock": "{{ input.event.payload.available }}",
         }),
    PUBLISH("live", "store:{{ input.event.payload.store_id }}", "stock.low",
            {"variant_id": "{{ input.event.payload.variant_id }}",
             "available": "{{ input.event.payload.available }}"}),
    WEBHOOK("hook", "stock.low", {
        "store_id": "{{ input.event.payload.store_id }}",
        "variant_id": "{{ input.event.payload.variant_id }}",
        "available": "{{ input.event.payload.available }}",
    }),
    DONE("done"),
]
_e += [
    ("in", "variant"), ("variant", "known"), ("known", "ignore", "false"),
    ("known", "store", "true"), ("store", "watching"),
    ("watching", "note", "true"), ("note", "notify"), ("notify", "mail"), ("mail", "live"),
    ("live", "hook"), ("hook", "done"),
    ("watching", "live", "false"),
]
FLOWS.append(flow(
    "low_stock_alert",
    "On stock.low: name the variant, tell the store in app, by mail, live and over its webhook — and only when a reorder point is actually set.",
    _n, _e, timeout=30,
))

# ── a basket someone walked away from ───────────────────────────────────────

_n, _e = [TRIGGER_SCHEDULE("tick", every=1800)], []
_n += [
    NOW("now"),
    N("cutoff", "time.now", offset_seconds=-3600, format="iso"),
    QUERY("idle", "SELECT id, store_id, email, currency, subtotal_minor, item_count, token, user_id "
                  "FROM carts WHERE status = 'open' AND item_count > 0 AND email IS NOT NULL "
                  "AND recovery_status = 'none' AND last_activity_at <= ? LIMIT 200",
          ["{{ steps.cutoff.output }}"]),
    FOREACH("each", "{{ steps.idle.output }}"),
    UPDATE("mark", "carts", "{{ item.id }}", {"recovery_status": "sent"}),
    MAIL("mail", "{{ item.email }}", "cart_recovery", {
        "cart": "{{ item }}", "items": "{{ item.item_count }}",
        "currency": "{{ item.currency }}", "value_minor": "{{ item.subtotal_minor }}",
        "token": "{{ item.token }}",
    }),
    EMIT("announce", "cart.abandoned", {
        "cart_id": "{{ item.id }}", "store_id": "{{ item.store_id }}",
        "value_minor": "{{ item.subtotal_minor }}",
    }),
    DONE("done"),
]
_e += [
    ("tick", "now"), ("now", "cutoff"), ("cutoff", "idle"), ("idle", "each"),
    ("each", "mark", "each"), ("mark", "mail"), ("mail", "announce"),
    ("each", "done", "done"),
]
FLOWS.append(flow(
    "abandoned_cart_sweep",
    "Every 30 minutes: baskets with an email, idle for an hour and not yet chased, get one recovery mail and are marked — one sweep, no repeats.",
    _n, _e, timeout=120,
))


# ── a shopper was told to wait; it's back ────────────────────────────────────

_n, _e = [TRIGGER_EVENT("in", "stock.back_in_stock")], []
_n += [
    LIST("variant", "product_variants", {"store_id": "{{ input.event.payload.store_id }}",
                                         "id": "{{ input.event.payload.variant_id }}"}, limit=1),
    IF("known", {"truthy": "$steps.variant.output.total"}),
    DONE("ignore", {"skipped": True}),
    LIST("waiting", "stock_alerts", {"store_id": "{{ input.event.payload.store_id }}",
                                     "variant_id": "{{ input.event.payload.variant_id }}",
                                     "status": "waiting"}, limit=500),
    FOREACH("each", "{{ steps.waiting.output.data }}"),
    MAIL("mail", "{{ item.email }}", "back_in_stock", {
        "product": "{{ steps.variant.output.data.0.title }}",
        "sku": "{{ steps.variant.output.data.0.sku }}",
        "handle": "{{ steps.variant.output.data.0.product_id }}",
    }),
    UPDATE("counted", "stock_alerts", "{{ item.id }}", {"status": "notified"}),
    DONE("done"),
]
_e += [
    ("in", "variant"), ("variant", "known"), ("known", "ignore", "false"),
    ("known", "waiting", "true"), ("waiting", "each"),
    ("each", "mail", "each"), ("mail", "counted"),
    ("each", "done", "done"),
]
FLOWS.append(flow(
    "restock_notify",
    "On stock.back_in_stock: everyone who asked to be told gets told, once — each alert is marked so a second swing of the shelf stays quiet.",
    _n, _e, timeout=60,
))

# ── yesterday's numbers, folded up once ─────────────────────────────────────

_n, _e = [TRIGGER_SCHEDULE("tick", cron="10 0 * * *")], []
_n += [
    NOW("day", format="iso"),
    N("start", "time.now", offset_seconds=-86400, format="iso"),
    QUERY("stores", "SELECT ref AS slug FROM stores WHERE is_active = TRUE LIMIT 200"),
    FOREACH("each", "{{ steps.stores.output }}"),
    QUERY("money", DAILY_ROLLUP_SQL, ["{{ item.slug }}", "{{ steps.start.output }}", "{{ steps.day.output }}"]),
    QUERY("units", UNITS_SQL, ["{{ item.slug }}", "{{ steps.start.output }}", "{{ steps.day.output }}"]),
    QUERY("day_key", "SELECT date(?) AS day", ["{{ steps.day.output }}"]),
    LIST("existing", "daily_stats", {"store_id": "{{ item.slug }}", "day": "{{ steps.day_key.output.0.day }}"}, limit=1),
    IF("fresh", {"truthy": "$steps.existing.output.total"}),
    UPDATE("refresh", "daily_stats", "{{ steps.existing.output.data.0.id }}", {
        "orders_count": "{{ steps.money.output.0.orders_count }}",
        "gross_minor": "{{ steps.money.output.0.gross_minor }}",
        "refunds_minor": "{{ steps.money.output.0.refunds_minor }}",
        "shipping_minor": "{{ steps.money.output.0.shipping_minor }}",
        "tax_minor": "{{ steps.money.output.0.tax_minor }}",
        "discount_minor": "{{ steps.money.output.0.discount_minor }}",
        "units_sold": "{{ steps.units.output.0.units_sold }}",
    }),
    CREATE("record", "daily_stats", {
        "store_id": "{{ item.slug }}", "day": "{{ steps.day_key.output.0.day }}",
        "orders_count": "{{ steps.money.output.0.orders_count }}",
        "gross_minor": "{{ steps.money.output.0.gross_minor }}",
        "refunds_minor": "{{ steps.money.output.0.refunds_minor }}",
        "shipping_minor": "{{ steps.money.output.0.shipping_minor }}",
        "tax_minor": "{{ steps.money.output.0.tax_minor }}",
        "discount_minor": "{{ steps.money.output.0.discount_minor }}",
        "units_sold": "{{ steps.units.output.0.units_sold }}",
    }),
    DONE("done"),
]
_e += [
    ("tick", "day"), ("day", "start"), ("start", "stores"), ("stores", "each"),
    ("each", "money", "each"), ("money", "units"), ("units", "day_key"), ("day_key", "existing"),
    ("existing", "fresh"), ("fresh", "refresh", "true"), ("fresh", "record", "false"),
    ("each", "done", "done"),
]
FLOWS.append(flow(
    "daily_rollup",
    "Every day at 00:10 UTC: fold each store's yesterday into daily_stats — updating the row if it exists, writing it if it does not. The dashboard then reads one small table instead of the order book.",
    _n, _e, timeout=120,
))

# __APPEND__
