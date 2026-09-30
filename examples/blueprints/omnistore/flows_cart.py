"""Cart flows: add, change, view, coupon, quote.

Each of these answers a storefront request, so each ends in ``response.return``
with a cart the client can render — never a bare write. A basket is repriced
from its own lines every time (:func:`flows_util.cart_totals`), so the total the
shopper sees and the total checkout charges cannot drift apart.
"""

from __future__ import annotations

from helpers import (
    CALC, CACHE_GET, CACHE_SET, CHECK, CREATE, FAIL, ID_TOKEN, IF, LIST, N, NOW, QUERY, REPLY, SET,
    TRIGGER_HTTP, UPDATE, body, flow, out, param, query, vref,
)
from flows_util import CART_LINES_SQL, cart_totals, stock_guard, store_guard, variant_guard

FLOWS = []


def frag(target, *fragments):
    """Splice ``(nodes, edges)`` fragments into a flow's own lists."""
    for nodes, edges in fragments:
        target[0].extend(nodes)
        target[1].extend(edges)


# ── add a line ───────────────────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/storefront/cart/items", "public")], []
_n += [
    SET("vars", store=query("store"), variant_id=body("variant_id"), quantity=body("quantity"),
        cart_token=body("cart_token"), email=body("email")),
    CHECK("check", "{{ input.body }}", [
        {"name": "variant_id", "type": "integer", "required": True, "minimum": 1},
        {"name": "quantity", "type": "integer", "required": True, "minimum": 1, "maximum": 999},
        {"name": "cart_token", "max_length": 64},
        {"name": "email", "type": "email"},
    ]),
    FAIL("bad_request", 422, "bad_request", "Send a variant and a quantity between 1 and 999"),
]
_e += [("in", "vars"), ("vars", "check"), ("check", "bad_request", "invalid")]
_g = [_n, _e]
frag(_g, store_guard("sg"))
_e += [("check", "sg_store"), ("sg_store_ok", "tok", "true")]
_n += [
    IF("tok", {"truthy": "$vars.cart_token"}),
    LIST("find_cart", "carts", {"token": "{{ vars.cart_token }}", "store_id": "{{ vars.store }}", "status": "open"}, limit=1),
    IF("have_cart", {"truthy": "$steps.find_cart.output.total"}),
    FAIL("stale_cart", 409, "cart_closed", "That basket was checked out or has expired"),
    SET("cart_known", cart_id=out("find_cart", "output", "data", "0", "id"), cart_token=out("find_cart", "output", "data", "0", "token")),
    ID_TOKEN("new_token", kind="token", length=32),
    CREATE("new_cart", "carts", {
        "store_id": "{{ vars.store }}",
        "token": "{{ steps.new_token.output }}",
        "user_id": "{{ auth.user_id }}",
        "email": "{{ vars.email }}",
        "currency": "{{ steps.sg_store.output.data.0.currency }}",
        "status": "open",
        "channel": "web",
    }),
    SET("cart_new", cart_id=out("new_cart", "output", "id"), cart_token=out("new_cart", "output", "token")),
]
_e += [
    ("tok", "find_cart", "true"),
    ("tok", "new_token", "false"),
    ("find_cart", "have_cart"),
    ("have_cart", "stale_cart", "false"),
    ("have_cart", "cart_known", "true"),
    ("new_token", "new_cart"),
    ("new_cart", "cart_new"),
    ("cart_known", "vg_variant"),
    ("cart_new", "vg_variant"),
]
frag(_g, variant_guard("vg"))
_e += [("vg_vset", "stk_tracked")]
frag(_g, stock_guard("stk"))
_e += [("stk_enough", "line", "true"), ("stk_skip", "line")]
_n += [
    LIST("line", "cart_items", {"cart_id": "{{ vars.cart_id }}", "variant_id": "{{ vars.variant_id }}"}, limit=1),
    IF("had_line", {"truthy": "$steps.line.output.total"}),
    CALC("sum_qty", "add", [out("line", "output", "data", "0", "quantity"), "{{ vars.quantity }}"], digits=0),
    CALC("capped", "min", [out("sum_qty", "output"), 999], digits=0),
    UPDATE("upd_line", "cart_items", out("line", "output", "data", "0", "id"), {"quantity": out("capped", "output")}),
    CREATE("add_line", "cart_items", {
        "store_id": "{{ vars.store }}",
        "cart_id": "{{ vars.cart_id }}",
        "variant_id": "{{ vars.variant_id }}",
        "quantity": "{{ vars.quantity }}",
        "unit_price_minor": "{{ vars.variant.price_minor }}",
    }),
]
_e += [
    ("line", "had_line"),
    ("had_line", "sum_qty", "true"),
    ("sum_qty", "capped"),
    ("capped", "upd_line"),
    ("had_line", "add_line", "false"),
    ("upd_line", "ct_totals"),
    ("add_line", "ct_totals"),
]
frag(_g, cart_totals("ct"))
_e += [("ct_save", "lines")]
_n += [
    QUERY("lines", CART_LINES_SQL, ["{{ vars.cart_id }}"]),
    REPLY("reply", {"cart_token": "{{ vars.cart_token }}", "cart": out("ct_save", "output"), "lines": out("lines", "output")}),
]
_e += [("lines", "reply")]
FLOWS.append(flow(
    "cart_add",
    "POST /storefront/cart/items: add a variant to a basket, opening one when the shopper has no token yet. Checks stock, freezes today's price on the line, and returns the whole cart.",
    _n, _e, timeout=20,
))


# ── change or remove a line ──────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/storefront/cart/lines", "public")], []
_n += [
    SET("vars", store=query("store"), line_id=body("line_id"), quantity=body("quantity")),
    CHECK("check", "{{ input.body }}", [
        {"name": "line_id", "type": "integer", "required": True, "minimum": 1},
        {"name": "quantity", "type": "integer", "required": True, "minimum": 0, "maximum": 999},
    ]),
    FAIL("bad_request", 422, "bad_request", "Send the line and the new quantity (0 to drop it)"),
    LIST("find", "cart_items", {"id": "{{ vars.line_id }}", "store_id": "{{ vars.store }}"}, limit=1),
    IF("found", {"truthy": "$steps.find.output.total"}),
    FAIL("no_line", 404, "no_such_line", "That line is not in a basket here"),
    SET("ctx", cart_id=out("find", "output", "data", "0", "cart_id"),
        variant_id=out("find", "output", "data", "0", "variant_id")),
    IF("clear", {"eq": [vref("quantity"), 0]}),
    N("drop", "resource.delete", resource="cart_items", id="{{ vars.line_id }}"),
    REPLY("dropped", {"removed": True, "line_id": "{{ vars.line_id }}"}),
]
_e += [
    ("in", "vars"), ("vars", "check"), ("check", "bad_request", "invalid"),
    ("check", "find"), ("find", "found"), ("found", "no_line", "false"),
    ("found", "ctx", "true"), ("ctx", "clear"), ("clear", "drop", "true"), ("drop", "dropped"),
]
_n += [
    QUERY("avail", "SELECT COALESCE(SUM(on_hand - reserved), 0) AS available, "
                   "EXISTS(SELECT 1 FROM product_variants pv WHERE pv.id = ? AND pv.track_inventory = TRUE) AS tracked "
                   "FROM stock_levels sl WHERE sl.store_id = ? AND sl.variant_id = ?",
          ["{{ vars.variant_id }}", "{{ vars.store }}", "{{ vars.variant_id }}"]),
    IF("tracked", {"truthy": "$steps.avail.output.0.tracked"}),
    CALC("fits", "subtract", [out("avail", "output", "0", "available"), "{{ vars.quantity }}"], digits=0),
    IF("ok", {"gte": ["$steps.fits.output", 0]}),
    FAIL("short", 409, "insufficient_stock", "Not enough stock left for that"),
    UPDATE("set_qty", "cart_items", "{{ vars.line_id }}", {"quantity": "{{ vars.quantity }}"}),
]
_e += [
    ("clear", "avail", "false"),
    ("avail", "tracked"),
    ("tracked", "set_qty", "false"),
    ("tracked", "fits", "true"),
    ("fits", "ok"),
    ("ok", "short", "false"),
]
_g = [_n, _e]
frag(_g, cart_totals("ct", cart_id="{{ vars.cart_id }}"))
_e += [("ok", "ct_totals", "true"), ("ct_save", "reply")]
_n.append(REPLY("reply", {"line": out("set_qty", "output"), "cart": out("ct_save", "output")}))
FLOWS.append(flow(
    "cart_update",
    "POST /storefront/cart/lines: set a line's quantity, or drop it with 0. The basket is repriced from its lines either way.",
    _n, _e, timeout=20,
))

# ── view a cart ──────────────────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "GET", "/storefront/cart/{token}", "public")], []
_n += [
    SET("vars", store=query("store")),
    LIST("find", "carts", {"token": param("token"), "store_id": "{{ vars.store }}"}, limit=1),
    IF("found", {"truthy": "$steps.find.output.total"}),
    FAIL("gone", 404, "no_cart", "That basket is gone"),
    CACHE_GET("cached", "cart:{{ vars.store }}:{{ input.params.token }}"),
    REPLY("from_cache", "{{ steps.cached.output.value }}"),
    QUERY("lines", CART_LINES_SQL, [out("find", "output", "data", "0", "id")]),
    LIST("coupon", "coupons", {"store_id": "{{ vars.store }}", "code": "{{ steps.find.output.data.0.coupon_code }}"}, limit=1),
    N("body", "control.set", values={"cart": out("find", "output", "data", "0"),
                                     "lines": out("lines", "output"),
                                     "coupon": out("coupon", "output", "data", "0")}),
    REPLY("reply", "{{ steps.body.output }}"),
    CACHE_SET("store_cache", "cart:{{ vars.store }}:{{ input.params.token }}", "{{ steps.body.output }}", ttl=15, tags=["cart"]),
]
_e += [
    ("in", "vars"), ("vars", "find"), ("find", "found"), ("found", "gone", "false"),
    ("found", "cached", "true"), ("cached", "from_cache", "hit"), ("cached", "lines", "miss"),
    ("lines", "coupon"), ("coupon", "body"), ("body", "store_cache"), ("store_cache", "reply"),
]
FLOWS.append(flow(
    "cart_view",
    "GET /storefront/cart/{token}: the basket with its lines and any coupon, straight from a fifteen-second cache when we have one.",
    _n, _e, timeout=15,
))


# ── apply a coupon ───────────────────────────────────────────────────────────

_n, _e = [TRIGGER_HTTP("in", "POST", "/storefront/cart/discount", "public")], []
_n += [
    SET("vars", store=query("store"), token=body("cart_token"), code=body("code")),
    LIST("find", "carts", {"token": "{{ vars.token }}", "store_id": "{{ vars.store }}", "status": "open"}, limit=1),
    IF("found", {"truthy": "$steps.find.output.total"}),
    FAIL("no_cart", 404, "no_cart", "No open basket to put that code on"),
    SET("ctx", cart_id=out("find", "output", "data", "0", "id"), subtotal=out("find", "output", "data", "0", "subtotal_minor")),
    NOW("now"),
    LIST("coupon", "coupons", {"store_id": "{{ vars.store }}", "code": "{{ vars.code }}", "is_active": True}, limit=1),
    IF("known", {"truthy": "$steps.coupon.output.total"}),
    FAIL("unknown_code", 422, "unknown_coupon", "We don't recognise that code"),
    IF("started", {"any": [
        {"empty": "$steps.coupon.output.data.0.starts_at"},
        {"lte": ["$steps.coupon.output.data.0.starts_at", "$steps.now.output"]},
    ]}),
    FAIL("not_yet", 422, "coupon_not_started", "That code is not live yet"),
    IF("in_time", {"any": [
        {"empty": "$steps.coupon.output.data.0.ends_at"},
        {"gte": ["$steps.coupon.output.data.0.ends_at", "$steps.now.output"]},
    ]}),
    FAIL("expired", 422, "coupon_expired", "That code has expired"),
    IF("big_enough", {"gte": [vref("subtotal"), "$steps.coupon.output.data.0.minimum_order_minor"]}),
    FAIL("too_small", 422, "below_minimum", "The basket is too small for that code"),
    IF("not_spent", {"any": [
        {"eq": ["$steps.coupon.output.data.0.usage_limit", 0]},
        {"lt": ["$steps.coupon.output.data.0.usage_count", "$steps.coupon.output.data.0.usage_limit"]},
    ]}),
    FAIL("used_up", 422, "coupon_spent", "That code has been used up"),
    N("kind", "control.switch", value="{{ steps.coupon.output.data.0.kind }}",
      cases={"percentage": "pct_raw", "fixed_amount": "fixed", "free_shipping": "ship"}),
    CALC("pct_raw", "multiply", ["{{ vars.subtotal }}", "{{ steps.coupon.output.data.0.value }}"], digits=0),
    CALC("pct", "divide", [out("pct_raw", "output"), 10000], digits=0),
    IF("cap_it", {"gt": ["$steps.coupon.output.data.0.maximum_discount_minor", 0]}),
    CALC("capped", "min", [out("pct", "output"), "{{ steps.coupon.output.data.0.maximum_discount_minor }}"], digits=0),
    CALC("fixed", "min", ["{{ steps.coupon.output.data.0.value }}", "{{ vars.subtotal }}"], digits=0),
    SET("ship", discount_minor=0, free_shipping=True),
    SET("final_pct", discount_minor="{{ steps.pct.output }}", free_shipping=False),
    SET("final_capped", discount_minor="{{ steps.capped.output }}", free_shipping=False),
    SET("final_fixed", discount_minor="{{ steps.fixed.output }}", free_shipping=False),
    UPDATE("save", "carts", "{{ vars.cart_id }}", {
        "coupon_code": "{{ vars.code }}",
        "discount_minor": "{{ vars.discount_minor }}",
    }),
    REPLY("reply", {"coupon": out("coupon", "output", "data", "0"), "discount_minor": out("save", "output", "discount_minor")}),
]
_e += [
    ("in", "vars"), ("vars", "find"), ("find", "found"), ("found", "no_cart", "false"),
    ("found", "ctx", "true"), ("ctx", "now"), ("now", "coupon"), ("coupon", "known"),
    ("known", "unknown_code", "false"), ("known", "started", "true"),
    ("started", "not_yet", "false"), ("started", "in_time", "true"),
    ("in_time", "expired", "false"), ("in_time", "big_enough", "true"),
    ("big_enough", "too_small", "false"), ("big_enough", "not_spent", "true"),
    ("not_spent", "used_up", "false"), ("not_spent", "kind", "true"),
    ("kind", "pct_raw", "percentage"), ("kind", "fixed", "fixed_amount"), ("kind", "ship", "free_shipping"),
    ("pct_raw", "pct"), ("pct", "cap_it"),
    ("cap_it", "capped", "true"), ("cap_it", "final_pct", "false"),
    ("capped", "final_capped"), ("fixed", "final_fixed"), ("ship", "save"),
    ("final_pct", "save"), ("final_capped", "save"), ("final_fixed", "save"), ("save", "reply"),
]
FLOWS.append(flow(
    "cart_coupon",
    "POST /storefront/cart/discount: prove a code is real, live, in date, above its minimum and not used up, then price it onto the basket.",
    _n, _e, timeout=15,
))

# __APPEND__
