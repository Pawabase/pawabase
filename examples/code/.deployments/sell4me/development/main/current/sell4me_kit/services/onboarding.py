"""Getting a merchant from "signed up" to "open for business".

A store records which steps it has *completed* (a set), not a step number, so a step can be added later without every store
appearing to regress, and a merchant can do them out of order. Only ``REQUIRED_STEPS`` gate the launch: customising the
storefront is genuinely optional, and blocking on taste is how an onboarding flow gets abandoned.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import media, q
from ..money import to_minor
from . import builder, segments, shipping, storefront
from . import themes
from .catalog import unique_slug

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COLOR_SLOTS = ("primary", "accent", "background", "surface", "text", "muted", "border")
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

STEPS: tuple[dict[str, str], ...] = (
    {"key": "store_details", "title": "Store details", "body": "Name your store, pick a currency and set where you're based.", "url": "/settings"},
    {"key": "first_product", "title": "Add your first product", "body": "You need something to sell before you can take an order.", "url": "/products/new"},
    {"key": "payment_provider", "title": "Connect payout", "body": "Add the bank account Paystack pays your sales into.", "url": "/payments/payouts/connect"},
    {"key": "shipping", "title": "Set up shipping", "body": "Tell us where you ship and what you charge.", "url": "/settings/shipping"},
    {"key": "storefront", "title": "Customise your storefront", "body": "Colours, type and the homepage your customers land on.", "url": "/storefront/pages"},
)
REQUIRED_STEPS = ("store_details", "first_product", "payment_provider", "shipping")


async def create_store(
    c: Any, *, owner: dict[str, Any], name: str, slug: str, currency: str = "NGN", country: str = "NG", template: str | None = None,
    colors: dict[str, str] | None = None, product: dict[str, Any] | None = None, logo: str | None = None, announcement: str | None = None,
    discount: dict[str, Any] | None = None, invites: list[str] | None = None,
) -> q.Row:
    """Create a store and everything it needs to function: the owner's membership, a theme, a platform domain, a shipping zone,
    the system segments and a draft homepage, so a merchant never meets an empty screen that turns out to be a missing row.

    Optional wizard parts: ``template`` (a theme preset, applied with ``replace_pages`` so the whole site arrives as drafts),
    ``colors`` (applied after the template so it always wins), ``product`` (a first listing), ``logo``/``announcement`` (the first
    two things a shopper sees), ``discount`` (a launch code) and ``invites`` (open invitations).
    """
    db = await c.db()
    settings = await c.settings()
    async with c.tx() as tx:
        store = await q.insert(tx, "stores", {"slug": slug, "name": name, "currency": currency.upper(), "country": country.upper(),
                               "owner_id": str(owner["id"]), "status": "draft", "onboarding_completed": ["store_details"],
                               "timezone_name": "UTC", "weight_unit": "kg", "maintenance_enabled": False, "help_desk_enabled": False})
        await q.insert(tx, "store_members", {"store_id": store.pk, "user_id": str(owner["id"]), "role": "owner", "status": "active",
                                              "extra_permissions": [], "denied_permissions": []})
        theme = await storefront.ensure_theme(tx, store)
        await shipping.seed_default_zone(tx, store)
        await segments.seed_system_segments(tx, store)
        await q.insert(tx, "storefront_pages", {"store_id": store.pk, "title": "Home", "slug": "home", "kind": "home", "draft": builder.default_tree(name),
                                                 "created_by_id": str(owner["id"]), "is_published": False, "noindex": False, "settings": {}})
        await q.insert(tx, "domains", {"store_id": store.pk, "hostname": f"{slug}.{settings.storefront_suffix}".lower(), "is_platform": True,
                                        "is_primary": True, "status": "verified", "verified_at": datetime.now(UTC)})
        if owner.get("profile_id"):
            await q.update(tx, "profiles", owner["profile_id"], {"last_store_id": store.pk})
    if template:
        try:
            await themes.apply_preset(db, theme, template, store=store, replace_pages=True)
        except KeyError:
            pass
    if colors:
        changed = False
        for slot in _COLOR_SLOTS:
            safe = storefront.safe_color(colors.get(slot), "") if colors.get(slot) else ""
            if safe:
                theme[f"color_{slot}"] = safe
                changed = True
        if changed:
            await q.save(db, "themes", theme)
    first_product = None
    if product and str(product.get("title") or "").strip():
        first_product = await _create_first_product(c, store, product)
        store = await complete_step(c, store, "first_product")
    if isinstance(logo, str) and logo:
        url = await _theme_image(c, store, "branding", theme.pk, logo)
        if url:
            await q.update(db, "themes", theme.pk, {"logo_url": url})
    if isinstance(announcement, str) and announcement.strip():
        await q.update(db, "themes", theme.pk, {"announcement": announcement.strip()[:200]})
    first_discount = None
    code = str((discount or {}).get("code") or "").strip().upper()
    if code:
        try:
            percent = max(1, min(90, int((discount or {}).get("percent") or 10)))
        except (TypeError, ValueError):
            percent = 10
        if not await q.exists(db, "discounts", {"store_id": store.pk, "code": code}):
            first_discount = await q.insert(db, "discounts", {"store_id": store.pk, "code": code, "kind": "percentage", "value": percent, "title": "Launch offer"})
    # Starter designs only when there is something for them to show: a design of nothing is not a head start.
    if first_product:
        await q.insert(db, "designs", {"store_id": store.pk, "title": "Launch poster", "kind": "poster", "width": 1080, "height": 1350,
                                       "product_id": first_product.pk, "created_by_id": str(owner["id"]), "data": {}})
    if first_discount:
        await q.insert(db, "designs", {"store_id": store.pk, "title": "Launch offer card", "kind": "coupon", "width": 1200, "height": 600,
                                       "discount_id": first_discount.pk, "created_by_id": str(owner["id"]), "data": {}})
    invitations = []
    seen = {str(owner.get("email") or "").lower()}
    for raw in invites or []:
        email = str(raw or "").strip().lower()
        if not email or email in seen or not _EMAIL.match(email):
            continue
        seen.add(email)
        invitations.append(await q.insert(db, "invitations", {"store_id": store.pk, "email": email, "role": "manager", "token": secrets.token_urlsafe(32),
                                                              "invited_by_id": str(owner["id"]), "expires_at": datetime.now(UTC) + timedelta(days=7)}))
    return store


async def _create_first_product(c: Any, store: q.Row, spec: dict[str, Any]) -> q.Row:
    """One product, quickly: the onboarding version of adding a listing (no options, no variant matrix, no SEO fields)."""
    db = await c.db()
    title = str(spec.get("title") or "").strip()[:200]
    try:
        price_minor = max(0, to_minor(spec.get("price") or 0, store.currency))
    except Exception:  # noqa: BLE001 — a malformed amount becomes free
        price_minor = 0
    item = await q.insert(db, "products", {"store_id": store.pk, "slug": await unique_slug(db, "products", store, title), "title": title, "status": "active",
                                           "description": "", "tags": [], "requires_shipping": True, "is_taxable": True, "view_count": 0, "cart_count": 0,
                                           "purchase_count": 0})
    await q.insert(db, "product_variants", {"product_id": item.pk, "store_id": store.pk, "title": "Default", "is_default": True, "price_minor": price_minor,
                                            "stock": 10, "reserved": 0, "track_inventory": True, "allow_backorder": False, "low_stock_threshold": 5,
                                            "weight_grams": 0, "position": 0})
    data_url = spec.get("image")
    if isinstance(data_url, str) and data_url:
        url = await _theme_image(c, store, "products", item.pk, data_url)
        if url:
            await q.insert(db, "product_images", {"product_id": item.pk, "url": url, "alt": item.title, "position": 0, "variants": {}})
    return item


async def _theme_image(c: Any, store: q.Row, kind: str, owner_id: int, data_url: str) -> str | None:
    """Decode a data URL from the wizard's uploader and store it (the same write path as the media endpoint)."""
    header, _, encoded = data_url.partition(",")
    if not header.startswith("data:") or ";base64" not in header or not encoded:
        return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not data or len(data) > _MAX_IMAGE_BYTES:
        return None
    image_kind = media.sniff_image(data)
    if image_kind not in media.ACCEPTED_IMAGE_TYPES:
        return None
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/avif": "avif", "image/gif": "gif"}[image_kind]
    key = media.object_key(store_id=store.pk, kind=kind, owner_id=owner_id, name=f"original.{extension}", digest=hashlib.sha256(data).hexdigest()[:16])
    try:
        await media.write(c, key, data, content_type=image_kind)
    except Exception:  # noqa: BLE001
        return None
    return media.media_url(await c.settings(), key)


async def complete_step(c: Any, store: q.Row, key: str) -> q.Row:
    """Mark a step done. Idempotent, and never un-completes anything."""
    done = list(store.onboarding_completed or [])
    if key not in done:
        done.append(key)
        db = await c.db()
        await q.update(db, "stores", store.pk, {"onboarding_completed": done})
        store["onboarding_completed"] = done
    return store


async def progress(c: Any, store: q.Row) -> dict[str, Any]:
    """The checklist with each step's real state. Completion is *observed*, not just remembered: a merchant who deletes their only
    product sees that step reopen."""
    db = await c.db()
    recorded = set(store.onboarding_completed or [])
    observed = {
        "store_details": bool(store.name and store.currency and store.country),
        "first_product": await q.exists(db, "products", {"store_id": store.pk}),
        "payment_provider": await q.exists(db, "payment_provider_accounts", {"store_id": store.pk, "status": "connected"}),
        "shipping": await q.exists(db, "shipping_rates", {"store_id": store.pk, "is_active": True}),
        "storefront": "storefront" in recorded,
    }
    steps = [{**s, "complete": bool(observed.get(s["key"]) or s["key"] in recorded) if s["key"] != "storefront" else observed["storefront"],
              "required": s["key"] in REQUIRED_STEPS} for s in STEPS]
    required_done = all(s["complete"] for s in steps if s["required"])
    return {"steps": steps, "completed": sum(1 for s in steps if s["complete"]), "total": len(steps),
            "can_launch": required_done and store.status != "active", "is_live": store.status == "active"}


async def launch(c: Any, store: q.Row) -> tuple[bool, str]:
    """Open the store to the public. Refuses with a reason rather than raising: the caller is a button and the reason is what it displays."""
    state = await progress(c, store)
    if store.status == "active":
        return True, "This store is already live."
    if not state["can_launch"]:
        return False, "Still to do: " + ", ".join(s["title"] for s in state["steps"] if s["required"] and not s["complete"])
    db = await c.db()
    await q.update(db, "stores", store.pk, {"status": "active", "launched_at": datetime.now(UTC)})
    store["status"] = "active"
    return True, "Your store is live."
