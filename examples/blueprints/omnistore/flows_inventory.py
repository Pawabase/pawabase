"""Inventory flows: adjustments, receiving, transfers and cycle counts.

Every one of these moves stock through a ``db.transaction`` that writes the
``stock_levels`` row and its ``inventory_ledger`` row together — the whole point
of the module. A count's variance is computed against what the system believes,
so the ledger records the *correction*, not a guess.
"""

from __future__ import annotations

from helpers import (
    CALC, CREATE, EMIT, FAIL, FOREACH, GET, IF, LIST, N, NOW, OP_CREATE, OP_UPDATE,
    QUERY, REPLY, SET, TRIGGER_HTTP, TX, UPDATE, body, flow, out, param, query,
)
from flows_util import LOW_STOCK_SQL

FLOWS = []

# ── a counted correction, with a reason ──────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/inventory/adjust", "inventory_staff")], []
_n += [
    SET("vars", store=body("store_id"), variant_id=body("variant_id"), warehouse_id=body("warehouse_id"),
        delta=body("delta"), reason=body("reason")),
    NOW("now"),
    LIST("level", "stock_levels", {"store_id": "{{ vars.store }}", "variant_id": "{{ vars.variant_id }}",
                                   "warehouse_id": "{{ vars.warehouse_id }}"}, limit=1),
    IF("has_level", {"truthy": "$steps.level.output.total"}),
    FAIL("no_level", 404, "no_stock_row", "That variant has no stock row at that warehouse"),
    CALC("after", "add", ["{{ steps.level.output.data.0.on_hand }}", "{{ vars.delta }}"], digits=0),
    IF("not_negative", {"gte": ["$steps.after.output", 0]}),
    FAIL("negative", 422, "would_go_negative", "That adjustment would take stock below zero"),
    TX("adjust", [
        OP_UPDATE("stock_levels", "{{ steps.level.output.data.0.id }}", {"on_hand": "{{ steps.after.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ vars.store }}",
            "warehouse_id": "{{ vars.warehouse_id }}",
            "variant_id": "{{ vars.variant_id }}",
            "delta": "{{ vars.delta }}",
            "reason": "{{ vars.reason }}",
            "reference_type": "manual",
            "on_hand_after": "{{ steps.after.output }}",
            "reserved_after": "{{ steps.level.output.data.0.reserved }}",
            "note": "{{ input.body.note }}",
            "actor_id": "{{ auth.user_id }}",
            "actor_kind": "staff",
        }),
    ]),
    EMIT("announce", "stock.adjusted", {
        "store_id": "{{ vars.store }}", "variant_id": "{{ vars.variant_id }}",
        "warehouse_id": "{{ vars.warehouse_id }}", "on_hand": "{{ steps.after.output }}",
        "reason": "{{ vars.reason }}", "actor_id": "{{ auth.user_id }}",
    }),
    REPLY("reply", {"on_hand": out("after", "output"),
                    "reserved": out("level", "output", "data", "0", "reserved")}),
]
_e += [
    ("in", "vars"), ("vars", "now"), ("now", "level"), ("level", "has_level"),
    ("has_level", "no_level", "false"), ("has_level", "after", "true"), ("after", "not_negative"),
    ("not_negative", "negative", "false"), ("not_negative", "adjust", "true"),
    ("adjust", "announce"), ("announce", "reply"),
]
FLOWS.append(flow(
    "stock_adjust",
    "POST /inventory/adjust: change one shelf's count with a reason — never below zero, always journaled, always announced.",
    _n, _e, timeout=20,
))


# ── goods arrive from a supplier ─────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/purchase-orders/{id}/receive", "inventory_staff")], []
_n += [
    GET("po", "purchase_orders", param("id")),
    FAIL("no_po", 404, "no_such_purchase_order", "No such purchase order"),
    IF("open", {"in": [out("po", "output", "status"), ["draft", "sent", "partially_received"]]}),
    FAIL("closed", 409, "not_receivable", "That purchase order is not expecting goods"),
    NOW("now"),
    QUERY("lines", "SELECT id, variant_id, quantity, received_quantity FROM purchase_order_items "
                   "WHERE purchase_order_id = ?", ["{{ steps.po.output.id }}"]),
    FOREACH("each", "{{ steps.lines.output }}"),
    CALC("due", "subtract", ["{{ item.quantity }}", "{{ item.received_quantity }}"], digits=0),
    IF("owed", {"gt": ["$steps.due.output", 0]}),
    SET("skip", received="nothing outstanding on this line"),
    LIST("level", "stock_levels", {"store_id": "{{ steps.po.output.store_id }}",
                                   "variant_id": "{{ item.variant_id }}",
                                   "warehouse_id": "{{ steps.po.output.warehouse_id }}"}, limit=1),
    IF("has_level", {"truthy": "$steps.level.output.total"}),
    CALC("on_hand", "add", ["{{ steps.level.output.data.0.on_hand }}", "{{ steps.due.output }}"], digits=0),
    TX("landed", [
        OP_UPDATE("stock_levels", "{{ steps.level.output.data.0.id }}", {"on_hand": "{{ steps.on_hand.output }}"}),
        OP_UPDATE("purchase_order_items", "{{ item.id }}", {"received_quantity": "{{ item.quantity }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ steps.po.output.store_id }}",
            "warehouse_id": "{{ steps.po.output.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "delta": "{{ steps.due.output }}",
            "reason": "purchase_receipt",
            "reference_type": "purchase_order",
            "reference_id": "{{ steps.po.output.id }}",
            "on_hand_after": "{{ steps.on_hand.output }}",
            "note": "Landed against line {{ item.id }}",
            "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    TX("first_shelf", [
        OP_CREATE("stock_levels", {
            "store_id": "{{ steps.po.output.store_id }}",
            "warehouse_id": "{{ steps.po.output.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "on_hand": "{{ steps.due.output }}",
            "reserved": 0,
        }),
        OP_UPDATE("purchase_order_items", "{{ item.id }}", {"received_quantity": "{{ item.quantity }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ steps.po.output.store_id }}",
            "warehouse_id": "{{ steps.po.output.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "delta": "{{ steps.due.output }}",
            "reason": "purchase_receipt",
            "reference_type": "purchase_order",
            "reference_id": "{{ steps.po.output.id }}",
            "on_hand_after": "{{ steps.due.output }}",
            "note": "First stock at this warehouse, line {{ item.id }}",
            "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    UPDATE("po_status", "purchase_orders", "{{ steps.po.output.id }}", {
        "status": "received", "received_at": "{{ steps.now.output }}",
    }),
    EMIT("announce", "purchase.received", {
        "purchase_order_id": "{{ steps.po.output.id }}", "store_id": "{{ steps.po.output.store_id }}",
        "warehouse_id": "{{ steps.po.output.warehouse_id }}", "supplier_id": "{{ steps.po.output.supplier_id }}",
    }),
    REPLY("reply", {"purchase_order": out("po", "output")}),
]
_e += [
    ("in", "po"), ("po", "open"), ("open", "closed", "false"), ("open", "now", "true"),
    ("now", "lines"), ("lines", "each"), ("each", "due", "each"), ("due", "owed"),
    ("owed", "skip", "false"), ("owed", "level", "true"), ("level", "has_level"),
    ("has_level", "on_hand", "true"), ("on_hand", "landed"),
    ("has_level", "first_shelf", "false"),
    ("each", "po_status", "done"),
    ("po_status", "announce"), ("announce", "reply"),
]
FLOWS.append(flow(
    "receive_purchase_order",
    "POST /purchase-orders/{id}/receive: land everything still outstanding — one transaction per line puts it on the shelf (or opens the shelf), "
    "closes the line and journals the receipt against the order.",
    _n, _e, timeout=45,
))


# ── moving stock between two shelves ─────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/inventory/transfers", "inventory_staff")], []
_n += [
    SET("vars", store=body("store_id"), variant_id=body("variant_id"),
        from_id=body("from_warehouse_id"), to_id=body("to_warehouse_id"), quantity=body("quantity")),
    IF("different", {"ne": ["$vars.from_id", "$vars.to_id"]}),
    FAIL("same_place", 422, "same_warehouse", "A transfer needs two different warehouses"),
    LIST("from_level", "stock_levels", {"store_id": "{{ vars.store }}", "variant_id": "{{ vars.variant_id }}",
                                        "warehouse_id": "{{ vars.from_id }}"}, limit=1),
    IF("has_from", {"truthy": "$steps.from_level.output.total"}),
    FAIL("nothing_to_send", 409, "no_stock_row", "There is nothing to move at that warehouse"),
    LIST("to_level", "stock_levels", {"store_id": "{{ vars.store }}", "variant_id": "{{ vars.variant_id }}",
                                      "warehouse_id": "{{ vars.to_id }}"}, limit=1),
    CALC("after", "subtract", ["{{ steps.from_level.output.data.0.on_hand }}", "{{ vars.quantity }}"], digits=0),
    IF("enough", {"gte": ["$steps.after.output", 0]}),
    FAIL("short", 409, "insufficient_stock", "That warehouse does not have that many"),
    NOW("now"),
    IF("has_to", {"truthy": "$steps.to_level.output.total"}),
    TX("moved", [
        OP_UPDATE("stock_levels", "{{ steps.from_level.output.data.0.id }}", {"on_hand": "{{ steps.after.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ vars.store }}", "warehouse_id": "{{ vars.from_id }}",
            "variant_id": "{{ vars.variant_id }}", "delta": "{{ vars.quantity }}",
            "reason": "transfer_out", "reference_type": "stock_transfer",
            "on_hand_after": "{{ steps.after.output }}",
            "reserved_after": "{{ steps.from_level.output.data.0.reserved }}",
            "note": "{{ input.body.note }}", "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    TX("moved_in", [
        OP_UPDATE("stock_levels", "{{ steps.to_level.output.data.0.id }}", {
            "on_hand": "{{ steps.arrived.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ vars.store }}", "warehouse_id": "{{ vars.to_id }}",
            "variant_id": "{{ vars.variant_id }}", "delta": "{{ vars.quantity }}",
            "reason": "transfer_in", "reference_type": "stock_transfer",
            "on_hand_after": "{{ steps.arrived.output }}",
            "note": "{{ input.body.note }}", "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    CALC("arrived", "add", ["{{ steps.to_level.output.data.0.on_hand }}", "{{ vars.quantity }}"], digits=0),
    TX("first_arrival", [
        OP_CREATE("stock_levels", {
            "store_id": "{{ vars.store }}", "warehouse_id": "{{ vars.to_id }}",
            "variant_id": "{{ vars.variant_id }}", "on_hand": "{{ vars.quantity }}", "reserved": 0,
        }),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ vars.store }}", "warehouse_id": "{{ vars.to_id }}",
            "variant_id": "{{ vars.variant_id }}", "delta": "{{ vars.quantity }}",
            "reason": "transfer_in", "reference_type": "stock_transfer",
            "on_hand_after": "{{ vars.quantity }}",
            "note": "First stock at this warehouse", "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    CREATE("transfer", "stock_transfers", {
        "store_id": "{{ vars.store }}", "from_warehouse_id": "{{ vars.from_id }}",
        "to_warehouse_id": "{{ vars.to_id }}", "variant_id": "{{ vars.variant_id }}",
        "quantity": "{{ vars.quantity }}", "status": "completed",
        "note": "{{ input.body.note }}", "completed_at": "{{ steps.now.output }}",
        "created_by": "{{ auth.user_id }}",
    }),
    EMIT("announce", "stock.transferred", {
        "store_id": "{{ vars.store }}", "variant_id": "{{ vars.variant_id }}",
        "from_warehouse_id": "{{ vars.from_id }}", "to_warehouse_id": "{{ vars.to_id }}",
        "quantity": "{{ vars.quantity }}",
    }),
    REPLY("reply", {"transfer": out("transfer", "output")}),
]
_e += [
    ("in", "vars"), ("vars", "different"), ("different", "same_place", "false"),
    ("different", "from_level", "true"), ("from_level", "has_from"),
    ("has_from", "nothing_to_send", "false"), ("has_from", "to_level", "true"),
    ("to_level", "after"), ("after", "enough"), ("enough", "short", "false"),
    ("enough", "now", "true"), ("now", "has_to"),
    ("has_to", "moved", "false"),
    ("has_to", "arrived", "true"),
    ("moved", "first_arrival"), ("arrived", "moved_in"),
    ("first_arrival", "transfer"), ("moved_in", "transfer"),
    ("transfer", "announce"), ("announce", "reply"),
]
FLOWS.append(flow(
    "transfer_stock",
    "POST /inventory/transfers: move stock between warehouses in one atomic pair of ledger entries — out of one shelf, into the other, never a "
    "half-move, and the destination shelf is opened if this is its first arrival.",
    _n, _e, timeout=30,
))


# ── a physical count, reconciled against what we believed ────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/inventory/counts/{id}/post", "inventory_staff")], []
_n += [
    GET("count", "stock_counts", param("id")),
    FAIL("no_count", 404, "no_such_count", "No such stock count"),
    IF("open", {"eq": [out("count", "output", "status"), "open"]}),
    FAIL("closed", 409, "already_posted", "That count is already posted"),
    NOW("now"),
    QUERY("lines", "SELECT id, variant_id, counted_quantity FROM stock_count_lines WHERE stock_count_id = ?",
          ["{{ steps.count.output.id }}"]),
    FOREACH("each", "{{ steps.lines.output }}"),
    QUERY("level", "SELECT id, on_hand, reserved, warehouse_id FROM stock_levels "
                   "WHERE store_id = ? AND variant_id = ? AND warehouse_id = ? LIMIT 1",
          ["{{ steps.count.output.store_id }}", "{{ item.variant_id }}", "{{ steps.count.output.warehouse_id }}"]),
    IF("booked", {"truthy": "$steps.level.output.0.id"}),
    SET("aligned", system_quantity="{{ steps.level.output.0.on_hand }}"),
    SET("missing", system_quantity=0),
    CALC("variance", "subtract", ["{{ item.counted_quantity }}", "{{ vars.system_quantity }}"], digits=0),
    IF("differs", {"ne": ["$steps.variance.output", 0]}),
    SET("skip_line", counted="no change needed"),
    TX("corrected", [
        OP_UPDATE("stock_levels", "{{ steps.level.output.0.id }}",
                  {"on_hand": "{{ item.counted_quantity }}", "last_counted_at": "{{ steps.now.output }}"}),
        OP_UPDATE("stock_count_lines", "{{ item.id }}",
                  {"system_quantity": "{{ vars.system_quantity }}", "variance": "{{ steps.variance.output }}"}),
        OP_CREATE("inventory_ledger", {
            "store_id": "{{ steps.count.output.store_id }}",
            "warehouse_id": "{{ steps.level.output.0.warehouse_id }}",
            "variant_id": "{{ item.variant_id }}",
            "delta": "{{ steps.variance.output }}",
            "reason": "cycle_count",
            "reference_type": "stock_count",
            "reference_id": "{{ steps.count.output.id }}",
            "on_hand_after": "{{ item.counted_quantity }}",
            "note": "Counted {{ item.counted_quantity }}, believed {{ vars.system_quantity }}",
            "actor_id": "{{ auth.user_id }}",
        }),
    ]),
    UPDATE("noted", "stock_count_lines", "{{ item.id }}",
           {"system_quantity": "{{ vars.system_quantity }}", "variance": "{{ steps.variance.output }}"}),
    UPDATE("posted_close", "stock_counts", "{{ steps.count.output.id }}", {
        "status": "posted", "posted_at": "{{ steps.now.output }}",
        "adjusted_lines": "{{ steps.lines.output | length }}",
    }),
    EMIT("announce", "stock.counted", {
        "stock_count_id": "{{ steps.count.output.id }}",
        "store_id": "{{ steps.count.output.store_id }}", "lines": "{{ steps.lines.output | length }}",
    }),
    REPLY("reply", {"count": out("count", "output"), "lines": out("lines", "output")}),
]
_e += [
    ("in", "count"), ("count", "open"), ("open", "closed", "false"), ("open", "now", "true"),
    ("now", "lines"), ("lines", "each"), ("each", "level", "each"), ("level", "booked"),
    ("booked", "aligned", "true"), ("booked", "missing", "false"),
    ("aligned", "variance"), ("missing", "variance"),
    ("variance", "differs"), ("differs", "skip_line", "false"), ("differs", "corrected", "true"),
    ("corrected", "noted"), ("skip_line", "noted"),
    ("each", "posted_close", "done"), ("posted_close", "announce"), ("announce", "reply"),
]
FLOWS.append(flow(
    "post_stock_count",
    "POST /inventory/counts/{id}/post: reconcile each counted line against what the system believed — where they differ the shelf is corrected and "
    "the ledger records the variance — then close the count.",
    _n, _e, timeout=60,
))


# ── what's running out, in one read ──────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "GET", "/inventory/low", "store_staff")], []
_n += [
    SET("vars", store=query("store")),
    QUERY("low", LOW_STOCK_SQL, ["{{ vars.store }}"]),
    QUERY("count", "SELECT COUNT(*) AS n FROM stock_levels sl "
                   "WHERE sl.store_id = ? AND sl.on_hand - sl.reserved <= sl.reorder_point",
          ["{{ vars.store }}"]),
    REPLY("reply", {"low_stock": out("low", "output"),
                    "count": out("count", "output", "0", "n")}),
]
_e += [("in", "vars"), ("vars", "low"), ("low", "count"), ("count", "reply")]
FLOWS.append(flow(
    "low_stock_view",
    "GET /inventory/low: every active variant at or under its reorder point, with how many are left — one grouped read, no order scanning.",
    _n, _e, timeout=20,
))

# __APPEND__
