"""Catalog resources: stores, taxonomy, products, variants, media, wishlists.

Every store-owned row carries ``store_id`` (the organization slug). Writes are
restricted to staff of that store, and reads follow the catalog-visibility
policies so anonymous storefront traffic can only see what is published.
"""

from __future__ import annotations

from helpers import BT, F, HM, HANDLE, SLUG, bps, money, ops, resource

S = F("store_id", required=True, pattern=SLUG, description="The store (organization slug)")
P = F("product_id", "integer", required=True)
V = F("variant_id", "integer", required=True)
W = F("warehouse_id", "integer", required=True)

STORE_REF = F(
    "ref",
    required=True,
    pattern=SLUG,
    description="The organization slug this storefront belongs to",
)

RESOURCES = []

RESOURCES.append(
    resource(
        "stores",
        "A storefront: the shop a customer buys from. One per organization.",
        [
            STORE_REF,
            F("name", required=True, max_length=120),
            F("currency", required=True, pattern=r"^[A-Z]{3}$", default="NGN"),
            F("country", required=True, pattern=r"^[A-Z]{2}$", default="NG"),
            F("email", "email", required=True),
            F("phone", max_length=32),
            F("timezone", max_length=60, default="Africa/Lagos"),
            F("support_email", "email"),
            F("logo_url", "url"),
            F("description", "text", max_length=4000),
            F("order_prefix", max_length=8, default="ORD"),
            F("order_sequence", "integer", minimum=0, default=1000),
            F("free_shipping_threshold_minor", "integer", minimum=0, default=0),
            bps("tax_rate_bps", default=750),
            F("tax_inclusive", "boolean", default=False),
            F("low_stock_threshold", "integer", minimum=0, default=5),
            F("loyalty_enabled", "boolean", default=True),
            bps("loyalty_earn_bps", default=500),
            F("loyalty_redeem_value_minor", "integer", minimum=0, default=100),
            F("is_active", "boolean", default=True),
            F("accepts_orders", "boolean", default=True),
            F("gateway_mode", enum=["test", "live"], default="test"),
        ],
        ops(
            list_="public",
            get="published_read",
            create="service_only",
            update="store_owner",
            delete="service_only",
        ),
        tags=["Catalog", "Settings"],
        cache_ttl=30,
    )
)

RESOURCES.append(
    resource(
        "categories",
        "A node in the catalog tree. Products hang off the deepest node.",
        [
            S,
            F("name", required=True, max_length=120),
            F("handle", pattern=HANDLE),
            F("parent_id", "integer", nullable=True),
            F("position", "integer", minimum=0, default=0),
            F("description", "text", max_length=4000),
            F("image_url", "url"),
            F("product_count", "integer", minimum=0, default=0, read_only=True),
            F("is_published", "boolean", default=True),
        ],
        ops(
            list_="public",
            get="published_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        relations=[BT("parent", "categories", "parent_id")],
        tags=["Catalog"],
        cache_ttl=60,
    )
)

RESOURCES.append(
    resource(
        "brands",
        "A manufacturer or label, used to group products and power brand pages.",
        [
            S,
            F("name", required=True, max_length=120),
            F("slug", pattern=HANDLE),
            F("logo_url", "url"),
            F("description", "text", max_length=4000),
            F("is_published", "boolean", default=True),
        ],
        ops(
            list_="public",
            get="published_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        tags=["Catalog"],
        cache_ttl=60,
    )
)

RESOURCES.append(
    resource(
        "collections",
        "A curated (or automatic) group of products, for merchandising.",
        [
            S,
            F("name", required=True, max_length=160),
            F("handle", pattern=HANDLE),
            F("description", "text", max_length=4000),
            F("image_url", "url"),
            F("is_automatic", "boolean", default=False),
            F("rules", "json", description="Automatic membership rules"),
            F("sort_order", enum=["manual", "newest", "price_asc", "price_desc", "best_selling"]),
            F("is_published", "boolean", default=True),
            F("product_count", "integer", minimum=0, default=0, read_only=True),
        ],
        ops(
            list_="public",
            get="published_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        tags=["Catalog"],
        cache_ttl=60,
    )
)

RESOURCES.append(
    resource(
        "collection_products",
        "Manual membership of a collection.",
        [
            S,
            F("collection_id", "integer", required=True),
            P,
            F("position", "integer", minimum=0, default=0),
        ],
        ops(
            list_="published_read",
            get="published_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_editor",
        ),
        relations=[
            BT("collection", "collections", "collection_id"),
            BT("product", "products", "product_id"),
        ],
        tags=["Catalog"],
    )
)

RESOURCES.append(
    resource(
        "products",
        "A sellable item. Price and stock live on its variants.",
        [
            S,
            F("title", required=True, max_length=200),
            F("handle", pattern=HANDLE),
            F("subtitle", max_length=200),
            F("description", "text", max_length=20000),
            F("category_id", "integer", nullable=True),
            F("brand_id", "integer", nullable=True),
            F("status", enum=["draft", "active", "archived"], default="draft"),
            F("is_published", "boolean", default=False),
            F("tax_class", max_length=40, default="standard"),
            F("weight_grams", "integer", minimum=0, default=0),
            F("tags", "array", items={"type": "string"}),
            F("internal_note", "text", max_length=2000),
            bps("rating_avg", default=0, read_only=True),
            F("rating_count", "integer", minimum=0, default=0, read_only=True),
            F("sold_count", "integer", minimum=0, default=0, read_only=True),
            F("view_count", "integer", minimum=0, default=0, read_only=True),
            money("min_price_minor", read_only=True),
            money("max_price_minor", read_only=True),
            F("in_stock", "boolean", default=True, read_only=True),
            F("published_at", "datetime", nullable=True),
            F("published_by", max_length=64),
        ],
        ops(
            list_="active_read",
            get="published_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        relations=[
            BT("category", "categories", "category_id"),
            BT("brand", "brands", "brand_id"),
            HM("variants", "product_variants", "product_id"),
            HM("media", "product_media", "product_id"),
            HM("reviews", "reviews", "product_id"),
        ],
        tags=["Catalog"],
        transformer="public_product",
        cache_ttl=30,
    )
)

RESOURCES.append(
    resource(
        "product_variants",
        "A buyable version of a product: its own SKU, price and inventory policy.",
        [
            S,
            P,
            F("title", required=True, max_length=120),
            F("sku", required=True, max_length=64),
            F("barcode", max_length=64),
            money("price_minor", required=True),
            money("compare_at_minor"),
            money("cost_minor"),
            F("weight_grams", "integer", minimum=0, default=0),
            F("is_default", "boolean", default=False),
            F("track_inventory", "boolean", default=True),
            F("allow_backorder", "boolean", default=False),
            F("reorder_point", "integer", minimum=0, default=0),
            F("supplier_sku", max_length=64),
            F("available", "integer", default=0, read_only=True),
            F("status", enum=["active", "archived"], default="active"),
        ],
        ops(
            list_="active_read",
            get="active_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_manager",
        ),
        relations=[
            BT("product", "products", "product_id"),
            HM("stock_levels", "stock_levels", "variant_id"),
        ],
        tags=["Catalog"],
        transformer="public_variant",
        cache_ttl=20,
    )
)

RESOURCES.append(
    resource(
        "product_media",
        "Images and video attached to a product, a variant or a category.",
        [
            S,
            F("product_id", "integer", nullable=True),
            F("variant_id", "integer", nullable=True),
            F("category_id", "integer", nullable=True),
            F("kind", enum=["image", "video"], default="image"),
            F("url", "url", required=True),
            F("alt", max_length=200),
            F("position", "integer", minimum=0, default=0),
        ],
        ops(
            list_="active_read",
            get="active_read",
            create="store_editor_create",
            update="store_editor",
            delete="store_editor",
        ),
        relations=[BT("product", "products", "product_id")],
        tags=["Catalog"],
    )
)

RESOURCES.append(
    resource(
        "wishlists",
        "A shopper's saved list. Belongs to one user, in one store.",
        [
            S,
            F("user_id", required=True, max_length=64),
            F("name", max_length=120, default="My list"),
            F("is_default", "boolean", default=True),
            F("is_public", "boolean", default=False),
            F("share_token", max_length=64),
        ],
        ops(
            list_="customer_self",
            get="customer_self",
            create="customer_self_create",
            update="customer_self",
            delete="customer_self",
        ),
        relations=[HM("items", "wishlist_items", "wishlist_id")],
        owner="user_id",
        tags=["Storefront"],
    )
)

RESOURCES.append(
    resource(
        "wishlist_items",
        "One saved variant, optionally watched for a restock.",
        [
            S,
            F("wishlist_id", "integer", required=True),
            F("user_id", required=True, max_length=64),
            V,
            F("note", max_length=200),
            F("notify_on_restock", "boolean", default=True),
            money("notified_price_minor"),
        ],
        ops(
            list_="customer_self",
            get="customer_self",
            create="customer_self_create",
            update="customer_self",
            delete="customer_self",
        ),
        relations=[
            BT("wishlist", "wishlists", "wishlist_id"),
            BT("variant", "product_variants", "variant_id"),
        ],
        owner="user_id",
        tags=["Storefront"],
    )
)

RESOURCES.append(
    resource(
        "stock_alerts",
        "A back-in-stock request from a storefront visitor.",
        [
            S,
            V,
            F("email", "email", required=True),
            F("user_id", max_length=64),
            F("status", enum=["waiting", "notified", "cancelled"], default="waiting"),
            F("notified_at", "datetime", nullable=True),
        ],
        ops(
            list_="store_editor",
            get="store_editor",
            create="public",
            update="store_editor",
            delete="store_editor",
        ),
        tags=["Storefront"],
    )
)

# __APPEND__
