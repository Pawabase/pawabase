"""Shared flow fragments: the SQL and the small guarded subgraphs flows reuse.

Fragments are plain ``(nodes, edges)`` builders, so the same stock check, the
same cart totals and the same checkout maths appear identically in every flow
that needs them — one fix reaches all of them.

All SQL is written for both bundled dialects (SQLite and Postgres): ``?``
placeholders, ``COALESCE``, ``SUM``/``COUNT``/``ABS``/``CAST`` and plain
comparisons only — nothing vendor-specific — so the blueprint runs unchanged
against either.

Fragments read and write well-known values:

* ``vars.store``      — the store slug every query is scoped by;
* ``vars.variant_id`` — the variant being checked;
* ``vars.cart_id``    — the cart being worked on.
"""

from __future__ import annotations

from helpers import CALC, FAIL, IF, LIST, N, QUERY, SET, UPDATE

# ── SQL ──────────────────────────────────────────────────────────────────────

#: Stock a variant can still be sold, summed over the store's active warehouses.
#: Params: store, variant.
AVAILABLE_SQL = """
SELECT COALESCE(SUM(sl.on_hand - sl.reserved), 0) AS available
FROM stock_levels sl
JOIN warehouses w ON w.id = sl.warehouse_id
WHERE sl.store_id = ? AND sl.variant_id = ? AND w.is_active = TRUE
"""

#: A cart's money and weight, recomputed from its lines. Param: cart id.
CART_TOTALS_SQL = """
SELECT COALESCE(SUM(ci.quantity * ci.unit_price_minor), 0) AS subtotal_minor,
       COALESCE(SUM(ci.quantity), 0) AS item_count,
       COALESCE(SUM(COALESCE(pv.weight_grams, 0) * ci.quantity), 0) AS weight_grams
FROM cart_items ci
JOIN product_variants pv ON pv.id = ci.variant_id
WHERE ci.cart_id = ?
"""

#: A cart's lines with the product detail a storefront needs. Param: cart id.
CART_LINES_SQL = """
SELECT ci.id, ci.variant_id, ci.quantity, ci.unit_price_minor,
       ci.quantity * ci.unit_price_minor AS total_minor,
       pv.sku, pv.title AS variant_title, pv.weight_grams, pv.track_inventory,
       p.id AS product_id, p.title AS product_title, p.handle, p.subtitle
FROM cart_items ci
JOIN product_variants pv ON pv.id = ci.variant_id
JOIN products p ON p.id = pv.product_id
WHERE ci.cart_id = ?
ORDER BY ci.id
"""

#: What an order consumed, per line, with the stock row to move. Param: order id.
ORDER_STOCK_SQL = """
SELECT oi.variant_id, oi.warehouse_id, oi.quantity, oi.product_id, pv.track_inventory,
       pv.reorder_point, sl.id AS level_id, sl.on_hand, sl.reserved
FROM order_items oi
JOIN product_variants pv ON pv.id = oi.variant_id
LEFT JOIN stock_levels sl ON sl.variant_id = oi.variant_id AND sl.warehouse_id = oi.warehouse_id
WHERE oi.order_id = ?
"""

#: The warehouse an order's stock should be held in. Param: store.
DEFAULT_WAREHOUSE_SQL = """
SELECT id FROM warehouses
WHERE store_id = ? AND is_active = TRUE
ORDER BY is_default DESC, priority ASC LIMIT 1
"""

#: Yesterday's money for one store. Params: store, day start, day end.
DAILY_ROLLUP_SQL = """
SELECT COUNT(*) AS orders_count,
       COALESCE(SUM(total_minor), 0) AS gross_minor,
       COALESCE(SUM(refunded_minor), 0) AS refunds_minor,
       COALESCE(SUM(shipping_minor), 0) AS shipping_minor,
       COALESCE(SUM(tax_minor), 0) AS tax_minor,
       COALESCE(SUM(discount_minor), 0) AS discount_minor
FROM orders
WHERE store_id = ? AND status NOT IN ('cancelled') AND placed_at >= ? AND placed_at < ?
"""

#: Units that left the shelves in a window. Params: store, day start, day end.
UNITS_SQL = """
SELECT COALESCE(SUM(oi.quantity), 0) AS units_sold
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
WHERE o.store_id = ? AND o.placed_at >= ? AND o.placed_at < ? AND o.status NOT IN ('cancelled')
"""

#: Variants at or below their low-stock threshold. Param: store.
LOW_STOCK_SQL = """
SELECT pv.id AS variant_id, pv.sku, pv.title, p.title AS product_title, p.id AS product_id,
       COALESCE(SUM(sl.on_hand - sl.reserved), 0) AS available, pv.reorder_point
FROM product_variants pv
JOIN products p ON p.id = pv.product_id
LEFT JOIN stock_levels sl ON sl.variant_id = pv.id AND sl.store_id = pv.store_id
WHERE pv.store_id = ? AND p.status = 'active' AND pv.track_inventory = TRUE
GROUP BY pv.id, pv.sku, pv.title, p.title, p.id, pv.reorder_point
HAVING COALESCE(SUM(sl.on_hand - sl.reserved), 0) <= pv.reorder_point
ORDER BY available ASC
LIMIT 50
"""


# ── guarded fragments ────────────────────────────────────────────────────────


def store_guard(prefix):
    """Stops the run unless ``vars.store`` names an active store."""
    nodes = [
        LIST(f"{prefix}_store", "stores", {"ref": "{{ vars.store }}"}, limit=1),
        IF(f"{prefix}_store_ok", {"truthy": f"$steps.{prefix}_store.output.total"}),
        FAIL(f"{prefix}_no_store", 422, "unknown_store", "No store for this storefront"),
    ]
    edges = [
        (f"{prefix}_store", f"{prefix}_store_ok"),
        (f"{prefix}_store_ok", f"{prefix}_no_store", "false"),
    ]
    return nodes, edges


def variant_guard(prefix):
    """Loads an active variant of this store into ``vars.variant``, or stops."""
    nodes = [
        LIST(f"{prefix}_variant", "product_variants",
             {"store_id": "{{ vars.store }}", "id": "{{ vars.variant_id }}"}, limit=1),
        IF(f"{prefix}_variant_ok", {"truthy": f"$steps.{prefix}_variant.output.total"}),
        FAIL(f"{prefix}_no_variant", 404, "unknown_variant", "That variant is not in this store"),
        QUERY(f"{prefix}_live", "SELECT status FROM products WHERE id = ? AND store_id = ?",
              [f"{{{{ steps.{prefix}_variant.output.data.0.product_id }}}}", "{{ vars.store }}"]),
        IF(f"{prefix}_on_sale", {"eq": [f"$steps.{prefix}_live.output.0.status", "active"]}),
        FAIL(f"{prefix}_off", 409, "unavailable", "This product is not on sale right now"),
        SET(f"{prefix}_vset", variant=f"{{{{ steps.{prefix}_variant.output.data.0 }}}}",
            product_id=f"{{{{ steps.{prefix}_variant.output.data.0.product_id }}}}"),
    ]
    edges = [
        (f"{prefix}_variant", f"{prefix}_variant_ok"),
        (f"{prefix}_variant_ok", f"{prefix}_no_variant", "false"),
        (f"{prefix}_variant_ok", f"{prefix}_live", "true"),
        (f"{prefix}_live", f"{prefix}_on_sale"),
        (f"{prefix}_on_sale", f"{prefix}_off", "false"),
        (f"{prefix}_on_sale", f"{prefix}_vset", "true"),
    ]
    return nodes, edges


def stock_guard(prefix, quantity="{{ vars.quantity }}", tracked_only=True):
    """Stops when the run asks for more units than the shelves can give.

    A variant with ``track_inventory`` off is never blocked: the check is
    skipped through the ``skip`` node so both paths rejoin afterwards.
    """
    nodes = [
        QUERY(f"{prefix}_avail", AVAILABLE_SQL, ["{{ vars.store }}", "{{ vars.variant_id }}"]),
        CALC(f"{prefix}_left", "subtract",
             [f"{{{{ steps.{prefix}_avail.output.0.available }}}}", quantity], digits=0),
        IF(f"{prefix}_enough", {"gte": [f"$steps.{prefix}_left.output", 0]}),
        FAIL(f"{prefix}_short", 409, "insufficient_stock", "Not enough stock left for that"),
    ]
    edges = [
        (f"{prefix}_avail", f"{prefix}_left"),
        (f"{prefix}_left", f"{prefix}_enough"),
        (f"{prefix}_enough", f"{prefix}_short", "false"),
    ]
    if tracked_only:
        nodes.insert(0, IF(f"{prefix}_tracked", {"truthy": "$vars.variant.track_inventory"}))
        nodes.append(SET(f"{prefix}_skip", stock_checked="untracked"))
        edges = [(f"{prefix}_tracked", f"{prefix}_avail", "true"),
                 (f"{prefix}_tracked", f"{prefix}_skip", "false")] + edges
    return nodes, edges



def cart_totals(prefix, cart_id="{{ vars.cart_id }}"):
    """Recomputes a cart's money from its lines and writes it back onto the cart."""
    nodes = [
        QUERY(f"{prefix}_totals", CART_TOTALS_SQL, [cart_id]),
        IF(f"{prefix}_blank", {"eq": [f"$steps.{prefix}_totals.output.0.item_count", 0]}),
        FAIL(f"{prefix}_empty", 409, "empty_cart", "Add something to the cart first"),
        UPDATE(f"{prefix}_save", "carts", cart_id, {
            "subtotal_minor": f"{{{{ steps.{prefix}_totals.output.0.subtotal_minor }}}}",
            "item_count": f"{{{{ steps.{prefix}_totals.output.0.item_count }}}}",
            "weight_grams": f"{{{{ steps.{prefix}_totals.output.0.weight_grams }}}}",
        }),
    ]
    edges = [
        (f"{prefix}_totals", f"{prefix}_blank"),
        (f"{prefix}_blank", f"{prefix}_empty", "true"),
        (f"{prefix}_blank", f"{prefix}_save", "false"),
    ]
    return nodes, edges

