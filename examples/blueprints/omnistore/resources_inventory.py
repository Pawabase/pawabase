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

# __APPEND__
