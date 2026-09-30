"""The OmniStore model: roles, reusable schemas, transformers and policies.

Everything here is referenced by name from the resource and flow modules, so it
is the vocabulary the rest of the blueprint speaks.

Tenancy
-------
A *store* is an Akountz organization, so every store-owned row carries
``store_id`` (the organization slug) and ``$auth.org`` is the caller's active
store. Store rules are then a single equality the policy engine pushes into SQL,
which keeps list pagination correct:

    {"eq": ["$record.store_id", "$auth.org"]}

Org roles decide staff power: ``owner``/``admin`` manage, ``member`` operates,
``viewer`` reads. Project roles decide what a signed-in shopper may do.
"""

from __future__ import annotations

from helpers import ALL, ANY, EQ, EXISTS, IN, OWNER

# ── constants shared with the resource and flow modules ─────────────────────

STAFF_ROLES = ["owner", "admin", "member", "viewer"]
EDITOR_ROLES = ["owner", "admin", "member"]
MANAGER_ROLES = ["owner", "admin"]
OWNER_ROLE = ["owner"]

CURRENCIES = ["NGN", "USD", "GBP", "EUR", "KES", "GHS", "ZAR", "CAD"]
ORDER_STATUSES = [
    "pending",
    "paid",
    "processing",
    "partially_fulfilled",
    "fulfilled",
    "cancelled",
    "refunded",
    "closed",
]
PAYMENT_STATUSES = ["pending", "authorized", "paid", "partially_refunded", "refunded", "failed"]
FULFILMENT_STATUSES = ["unfulfilled", "partial", "fulfilled", "returned"]
SHIPMENT_STATUSES = [
    "label_created",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "failed",
    "returned",
]
RETURN_STATUSES = ["requested", "approved", "rejected", "received", "refunded", "cancelled"]
PRODUCT_STATUSES = ["draft", "active", "archived"]
REVIEW_STATUSES = ["pending", "approved", "rejected", "spam"]
TICKET_STATUSES = ["open", "pending", "resolved", "closed"]
LEDGER_REASONS = [
    "purchase_receipt",
    "sale",
    "release",
    "return_restock",
    "damage",
    "shrinkage",
    "transfer_out",
    "transfer_in",
    "cycle_count",
    "manual",
]


# ── roles ────────────────────────────────────────────────────────────────────

ROLES = [
    {
        "name": "platform_admin",
        "description": "Operates the whole marketplace: every store, every order.",
        "permissions": ["*"],
    },
    {
        "name": "support_agent",
        "description": "Reads orders and answers tickets across stores, without editing the catalog.",
        "permissions": [
            "orders.read",
            "orders.respond",
            "tickets.respond",
            "reviews.moderate",
            "customers.read",
        ],
    },
    {
        "name": "warehouse_operator",
        "description": "Receives stock and ships orders at an assigned warehouse.",
        "permissions": ["inventory.adjust", "fulfilment.ship", "orders.read"],
    },
    {
        "name": "marketing_editor",
        "description": "Owns content, campaigns and promotions, and moderates reviews.",
        "permissions": ["content.publish", "promotions.manage", "reviews.moderate"],
    },
    {
        "name": "customer",
        "description": "A shopper. This is the role every new account gets.",
        "permissions": ["shop.buy", "shop.review", "shop.return"],
    },
]

# ── transformers ─────────────────────────────────────────────────────────────

TRANSFORMERS = [
    {
        "name": "public_variant",
        "description": "A variant as shoppers see it: no cost price, no reservations, no supplier data.",
        "definition": {"omit": ["cost_minor", "reserved", "reorder_point", "supplier_sku"]},
    },
    {
        "name": "public_product",
        "description": "A product as shoppers see it: no internal notes, no cost rollup.",
        "definition": {"omit": ["internal_note", "cost_minor", "search_terms"]},
    },
    {
        "name": "public_review",
        "description": "An approved review for the storefront: no moderation trail.",
        "definition": {"omit": ["moderated_by", "moderation_note", "author_ip", "user_id"]},
    },
    {
        "name": "public_page",
        "description": "A published CMS page without its editorial metadata.",
        "definition": {"omit": ["editor_note", "checksum", "updated_by"]},
    },
    {
        "name": "customer_order",
        "description": "An order as its buyer sees it: no risk signals, no internal notes, no cost.",
        "definition": {
            "omit": ["risk_score", "risk_flags", "internal_note", "client_ip", "cost_minor"]
        },
    },
    {
        "name": "customer_shipment",
        "description": "Tracking detail a buyer may see, without carrier cost.",
        "definition": {"omit": ["cost_minor", "carrier_account", "label_url"]},
    },
    {
        "name": "storefront_banner",
        "description": "Only what the storefront renders for a banner.",
        "definition": {
            "pick": [
                "id",
                "store_id",
                "slot",
                "headline",
                "subhead",
                "image_url",
                "link_url",
                "position",
            ]
        },
    },
    {
        "name": "packing_slip",
        "description": "The fields a warehouse prints on a packing slip.",
        "definition": {
            "pick": [
                "id",
                "order_id",
                "order_number",
                "store_id",
                "warehouse_id",
                "shipping_address",
                "currency",
                "total_minor",
                "fulfilment_status",
            ]
        },
    },
]

# ── policies ─────────────────────────────────────────────────────────────────

PLATFORM_ADMIN = {"role": ["platform_admin"]}


def store_scope(roles):
    """Staff of the *record's* store. Pushed down as ``store_id = <org>``."""
    return ALL(
        EXISTS("$auth.org"),
        IN("$auth.org_role", roles),
        EQ("$record.store_id", "$auth.org"),
    )


def store_input(roles):
    """Creating a row for your own store only."""
    return ALL(
        EXISTS("$auth.org"),
        IN("$auth.org_role", roles),
        EQ("$input.store_id", "$auth.org"),
    )


POLICIES = [
    # ── platform level ──────────────────────────────────────────────────────
    {
        "name": "platform_admin",
        "description": "Marketplace operators.",
        "condition": PLATFORM_ADMIN,
    },
    {
        "name": "support_desk",
        "description": "Support agents and platform admins: read across stores, never edit the catalog.",
        "condition": ANY(PLATFORM_ADMIN, {"permission": "orders.read"}),
    },
    {
        "name": "moderator",
        "description": "Anyone permitted to moderate user-generated content.",
        "condition": ANY(PLATFORM_ADMIN, {"permission": "reviews.moderate"}),
    },
    # ── store staff, scoped to the record ───────────────────────────────────
    {
        "name": "store_staff",
        "description": "Any staff member of the record's store (owner, admin, member, viewer).",
        "condition": ANY(store_scope(STAFF_ROLES), PLATFORM_ADMIN),
    },
    {
        "name": "store_editor",
        "description": "Store staff who may operate: owner, admin or member.",
        "condition": ANY(store_scope(EDITOR_ROLES), PLATFORM_ADMIN),
    },
    {
        "name": "store_manager",
        "description": "Store owners and admins only.",
        "condition": ANY(store_scope(MANAGER_ROLES), PLATFORM_ADMIN),
    },
    {
        "name": "store_owner",
        "description": "Only the store's owner (or a platform admin).",
        "condition": ANY(store_scope(OWNER_ROLE), PLATFORM_ADMIN),
    },
    # ── store staff, scoped to a create payload ─────────────────────────────
    {
        "name": "store_editor_create",
        "description": "Operating staff creating a row inside their own store.",
        "condition": ANY(store_input(EDITOR_ROLES), PLATFORM_ADMIN),
    },
    {
        "name": "store_manager_create",
        "description": "Owners and admins creating a row inside their own store.",
        "condition": ANY(store_input(MANAGER_ROLES), PLATFORM_ADMIN),
    },
    # ── the shopper ─────────────────────────────────────────────────────────
    {
        "name": "customer_self",
        "description": "The signed-in shopper the record belongs to.",
        "condition": ANY(OWNER("user_id"), PLATFORM_ADMIN),
    },
    {
        "name": "customer_self_create",
        "description": "A signed-in shopper creating a row owned by themselves.",
        "condition": ALL(EXISTS("$input.user_id"), EQ("$input.user_id", "$auth.user_id")),
    },
    {
        "name": "customer_or_store",
        "description": "The buyer the row belongs to, or staff of the store that owns it.",
        "condition": ANY(OWNER("user_id"), store_scope(STAFF_ROLES), PLATFORM_ADMIN),
    },
    # ── catalog visibility ──────────────────────────────────────────────────
    {
        "name": "published_read",
        "description": "Everyone sees published rows; staff see their own drafts and archives too.",
        "condition": ANY(EQ("$record.is_published", True), store_scope(STAFF_ROLES), PLATFORM_ADMIN),
    },
    {
        "name": "active_read",
        "description": "Everyone sees active rows; staff see their own drafts and archives.",
        "condition": ANY(EQ("$record.status", "active"), store_scope(STAFF_ROLES), PLATFORM_ADMIN),
    },
    {
        "name": "approved_read",
        "description": "Everyone sees approved rows; moderators and the author see the rest.",
        "condition": ANY(
            EQ("$record.status", "approved"),
            OWNER("user_id"),
            {"permission": "reviews.moderate"},
            PLATFORM_ADMIN,
        ),
    },
    {
        "name": "owned_read",
        "description": "The owning shopper, or staff of the store, may read the row.",
        "condition": ANY(OWNER("user_id"), store_scope(STAFF_ROLES), PLATFORM_ADMIN),
    },
    # ── inventory and fulfilment ────────────────────────────────────────────
    {
        "name": "inventory_staff",
        "description": "Staff who may move stock: store managers, warehouse operators, admins.",
        "condition": ANY(
            store_scope(MANAGER_ROLES),
            {"permission": "inventory.adjust"},
            PLATFORM_ADMIN,
        ),
    },
    {
        "name": "fulfilment_staff",
        "description": "Staff who may pick, pack and ship.",
        "condition": ANY(
            store_scope(EDITOR_ROLES),
            {"permission": "fulfilment.ship"},
            PLATFORM_ADMIN,
        ),
    },
    # ── credentials ─────────────────────────────────────────────────────────
    {
        "name": "authenticated",
        "description": "Any signed-in caller: shopper, staff, operator or service.",
        "condition": {"authenticated": True},
    },
    {
        "name": "service_only",
        "description": "Only a server-side credential: a secret key, operator or service token.",
        "condition": {"service": True},
    },
    {
        "name": "public",
        "description": "Anyone, including anonymous browsers holding a publishable key.",
        "condition": True,
    },
]

