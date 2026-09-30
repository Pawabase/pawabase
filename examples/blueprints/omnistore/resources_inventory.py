"""Inventory resources: warehouses, stock on hand, and the ledger that moves it.

Stock is never edited in place. ``stock_levels.on_hand`` and
``stock_levels.reserved`` are the current truth, and every movement writes an
``inventory_ledger`` row in the same transaction, so the ledger can always be
replayed to explain a discrepancy.
"""

from __future__ import annotations

from helpers import BT, F, HM, SLUG, money, ops, resource

S = F("store_id", required=True, pattern=SLUG, description="The store (organization slug)")
V = F("variant_id", "integer", required=True)
W = F("warehouse_id", "integer", required=True)

RESOURCES = []

RESOURCES.append(
    resource(
        "warehouses",
        "A place stock sits: a shop floor, a 3PL, a dark store.",
        [
            S,
            F("name", required=True, max_length=120),
            F("code", max_length=20),
            F("kind", enum=["physical", "dark_store", "third_party", "dropship"], default="physical"),
            F("line1", max_length=160),
            F("city", max_length=80),
            F("country", pattern=r"^[A-Z]{2}$", default="NG"),
            F("phone", max_length=32),
            F("is_default", "boolean", default=False),
            F("is_active", "boolean", default=True),
            F("priority", "integer", minimum=0, default=100),
            F("cutoff_hour", "integer", minimum=0, maximum=23, default=14),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="store_manager_create",
            update="store_manager",
            delete="store_owner",
        ),
        relations=[HM("stock_levels", "stock_levels", "warehouse_id")],
        tags=["Inventory"],
        cache_ttl=120,
    )
)

RESOURCES.append(
    resource(
        "stock_levels",
        "What one variant has at one warehouse: on hand, and how much is spoken for.",
        [
            S,
            W,
            V,
            F("on_hand", "integer", default=0),
            F("reserved", "integer", minimum=0, default=0),
            F("reorder_point", "integer", minimum=0, default=0),
            F("bin", max_length=32),
            F("last_counted_at", "datetime", nullable=True),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="service_only",
            update="inventory_staff",
            delete="service_only",
        ),
        relations=[
            BT("warehouse", "warehouses", "warehouse_id"),
            BT("variant", "product_variants", "variant_id"),
        ],
        tags=["Inventory"],
        realtime=True,
    )
)

RESOURCES.append(
    resource(
        "inventory_ledger",
        "An append-only journal: every stock movement, with its reason and author.",
        [
            S,
            W,
            V,
            F("delta", "integer", required=True),
            F("reason", required=True, max_length=40),
            F("reference_type", max_length=40),
            F("reference_id", "integer", nullable=True),
            F("on_hand_after", "integer"),
            F("reserved_after", "integer"),
            F("note", "text", max_length=500),
            F("actor_id", max_length=64),
            F("actor_kind", max_length=20, default="staff"),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="service_only",
            update=False,
            delete=False,
        ),
        relations=[BT("variant", "product_variants", "variant_id")],
        tags=["Inventory"],
        events=True,
    )
)

RESOURCES.append(
    resource(
        "stock_transfers",
        "Stock moving between two warehouses, completed as one atomic pair of writes.",
        [
            S,
            F("from_warehouse_id", "integer", required=True),
            F("to_warehouse_id", "integer", required=True),
            V,
            F("quantity", "integer", required=True, minimum=1),
            F("status", enum=["pending", "completed", "cancelled"], default="pending"),
            F("note", "text", max_length=500),
            F("completed_at", "datetime", nullable=True),
            F("created_by", max_length=64),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="inventory_staff",
            update="inventory_staff",
            delete="store_manager",
        ),
        relations=[
            BT("from_warehouse", "warehouses", "from_warehouse_id"),
            BT("to_warehouse", "warehouses", "to_warehouse_id"),
            BT("variant", "product_variants", "variant_id"),
        ],
        tags=["Inventory"],
    )
)

RESOURCES.append(
    resource(
        "suppliers",
        "Who the store buys stock from.",
        [
            S,
            F("name", required=True, max_length=160),
            F("email", "email"),
            F("phone", max_length=32),
            F("country", pattern=r"^[A-Z]{2}$", default="NG"),
            F("lead_time_days", "integer", minimum=0, default=7),
            F("payment_terms_days", "integer", minimum=0, default=30),
            F("is_active", "boolean", default=True),
            F("note", "text", max_length=1000),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="store_manager_create",
            update="store_manager",
            delete="store_owner",
        ),
        tags=["Inventory"],
    )
)

RESOURCES.append(
    resource(
        "purchase_orders",
        "An order placed with a supplier, received line by line.",
        [
            S,
            F("supplier_id", "integer", required=True),
            F("code", max_length=24),
            F("warehouse_id", "integer", required=True),
            F("status", enum=["draft", "sent", "partially_received", "received", "cancelled"], default="draft"),
            F("expected_at", "datetime", nullable=True),
            money("subtotal_minor", default=0),
            money("shipping_minor", default=0),
            money("total_minor", default=0),
            F("currency", max_length=3),
            F("note", "text", max_length=2000),
            F("created_by", max_length=64),
            F("sent_at", "datetime", nullable=True),
            F("received_at", "datetime", nullable=True),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="inventory_staff",
            update="inventory_staff",
            delete="store_manager",
        ),
        relations=[
            BT("supplier", "suppliers", "supplier_id"),
            BT("warehouse", "warehouses", "warehouse_id"),
            HM("items", "purchase_order_items", "purchase_order_id"),
        ],
        tags=["Inventory"],
    )
)

RESOURCES.append(
    resource(
        "purchase_order_items",
        "One line of a purchase order, with what has been received so far.",
        [
            S,
            F("purchase_order_id", "integer", required=True),
            V,
            F("quantity", "integer", required=True, minimum=1),
            money("unit_cost_minor", required=True),
            F("received_quantity", "integer", minimum=0, default=0),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="inventory_staff",
            update="inventory_staff",
            delete="inventory_staff",
        ),
        relations=[
            BT("purchase_order", "purchase_orders", "purchase_order_id"),
            BT("variant", "product_variants", "variant_id"),
        ],
        tags=["Inventory"],
    )
)

RESOURCES.append(
    resource(
        "stock_counts",
        "A physical count header; the variance flow reconciles on_hand to what was counted.",
        [
            S,
            F("warehouse_id", "integer", required=True),
            F("status", enum=["open", "posted", "cancelled"], default="open"),
            F("started_by", max_length=64),
            F("posted_at", "datetime", nullable=True),
            F("note", "text", max_length=1000),
            F("line_count", "integer", minimum=0, default=0),
            F("adjusted_lines", "integer", minimum=0, default=0),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="inventory_staff",
            update="inventory_staff",
            delete="store_manager",
        ),
        relations=[HM("lines", "stock_count_lines", "stock_count_id")],
        tags=["Inventory"],
    )
)

RESOURCES.append(
    resource(
        "stock_count_lines",
        "One counted variant inside a stock count.",
        [
            S,
            F("stock_count_id", "integer", required=True),
            V,
            F("counted_quantity", "integer", required=True, minimum=0),
            F("system_quantity", "integer"),
            F("variance", "integer"),
        ],
        ops(
            list_="store_staff",
            get="store_staff",
            create="inventory_staff",
            update="inventory_staff",
            delete="inventory_staff",
        ),
        relations=[BT("stock_count", "stock_counts", "stock_count_id")],
        tags=["Inventory"],
    )
)

# __APPEND__
