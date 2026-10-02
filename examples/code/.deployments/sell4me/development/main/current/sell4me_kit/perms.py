"""Roles and permissions: the table the guard reads, and the screen describes."""

from __future__ import annotations

from typing import Any

ALL_PERMISSIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "Orders": (
        ("orders.read", "View orders"),
        ("orders.update", "Edit orders and add notes"),
        ("orders.fulfil", "Mark orders fulfilled and add tracking"),
        ("orders.cancel", "Cancel orders"),
    ),
    "Catalog": (
        ("products.read", "View products"),
        ("products.create", "Create products"),
        ("products.update", "Edit products"),
        ("products.delete", "Delete products"),
        ("inventory.read", "View stock levels"),
        ("inventory.update", "Adjust stock"),
    ),
    "Customers": (
        ("customers.read", "View customers"),
        ("customers.update", "Edit customers and segments"),
    ),
    "Marketing": (
        ("discounts.read", "View discounts"),
        ("discounts.manage", "Create and edit discounts"),
        ("campaigns.read", "View campaigns"),
        ("campaigns.manage", "Create and run campaigns"),
    ),
    "Finance": (
        ("payments.read", "View payments and transactions"),
        ("refunds.create", "Issue refunds"),
        ("payouts.read", "View payouts"),
    ),
    "Point of Sale": (
        ("pos.read", "View POS sessions and history"),
        ("pos.create", "Process sales at the POS terminal"),
        ("pos.manage", "Configure POS devices, domains and settings"),
    ),
    "Insight": (
        ("analytics.read", "View analytics"),
        ("reports.export", "Export data"),
    ),
    "Support": (
        ("support.read", "View help desk tickets"),
        ("support.manage", "Reply to and close help desk tickets"),
    ),
    "Store": (
        ("storefront.read", "View storefront settings"),
        ("storefront.update", "Edit the storefront and theme"),
        ("settings.read", "View store settings"),
        ("settings.update", "Change store settings"),
        ("staff.read", "View staff"),
        ("staff.manage", "Invite and remove staff"),
        ("developers.read", "View API keys and webhooks"),
        ("developers.manage", "Create API keys and webhooks"),
    ),
}

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "owner": ("*",),
    "admin": (
        "orders.read", "orders.update", "orders.fulfil", "orders.cancel",
        "products.read", "products.create", "products.update", "products.delete",
        "inventory.read", "inventory.update",
        "customers.read", "customers.update",
        "discounts.read", "discounts.manage",
        "campaigns.read", "campaigns.manage",
        "payments.read", "refunds.create", "payouts.read",
        "analytics.read", "reports.export",
        "settings.read", "settings.update",
        "staff.read", "staff.manage",
        "developers.read", "developers.manage",
        "storefront.read", "storefront.update",
        "pos.read", "pos.create",
        "support.read", "support.manage",
    ),
    "manager": (
        "orders.read", "orders.update", "orders.fulfil", "orders.cancel",
        "products.read", "products.create", "products.update",
        "inventory.read", "inventory.update",
        "customers.read", "customers.update",
        "discounts.read", "discounts.manage",
        "campaigns.read", "campaigns.manage",
        "payments.read", "analytics.read", "reports.export",
        "storefront.read", "storefront.update",
        "settings.read",
        "pos.read", "pos.create",
    ),
    "support": (
        "orders.read", "orders.update",
        "products.read", "inventory.read",
        "customers.read", "customers.update",
        "payments.read",
        "pos.read", "pos.create",
        "support.read", "support.manage",
    ),
    "marketing": (
        "products.read", "customers.read",
        "discounts.read", "discounts.manage",
        "campaigns.read", "campaigns.manage",
        "analytics.read", "reports.export",
        "storefront.read", "storefront.update",
    ),
    "inventory": (
        "products.read", "products.update",
        "inventory.read", "inventory.update",
        "orders.read", "orders.fulfil",
    ),
    "finance": (
        "orders.read", "payments.read", "refunds.create", "payouts.read",
        "analytics.read", "reports.export", "customers.read",
    ),
}

ROLES = ("owner", "admin", "manager", "support", "marketing", "inventory", "finance")


def permissions_of(member: Any) -> set[str]:
    """Everything a membership may do, role and overrides resolved. An owner holds ``*``."""
    if member.get("role") == "owner":
        return {"*"}
    granted = set(ROLE_PERMISSIONS.get(member.get("role"), ()))
    granted.update(member.get("extra_permissions") or [])
    granted.difference_update(member.get("denied_permissions") or [])
    return granted


def member_can(member: Any, permission: str) -> bool:
    if member.get("status") != "active":
        return False
    granted = permissions_of(member)
    if "*" in granted or permission in granted:
        return True
    return f"{permission.split('.', 1)[0]}.*" in granted


def role_matrix() -> dict[str, Any]:
    return {
        "roles": [
            {
                "key": role,
                "label": role.replace("_", " ").title(),
                "permissions": ["*"] if role == "owner" else sorted(ROLE_PERMISSIONS.get(role, ())),
            }
            for role in ROLES
        ],
        "groups": [
            {"name": group, "permissions": [{"key": key, "label": label} for key, label in entries]}
            for group, entries in ALL_PERMISSIONS.items()
        ],
    }
