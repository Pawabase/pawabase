"""Accounts, stores and onboarding.

Identity (sign-up, sign-in, sign-out, password reset, MFA) is Akountz's: the SDK's ``auth.*`` methods replace ``/login``,
``/register``, ``/logout``, ``/forgot-password`` and ``/reset-password/{token}``. What Sell4me kept on its own ``User``
(name, avatar, timezone, last store, the signup survey) is the ``profiles`` resource, and these endpoints keep it.
"""

from __future__ import annotations

import re
from typing import Any

import sell4me_kit.listeners  # noqa: F401  (registers the event listeners)
from sell4me_kit import audit, q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import ApiError, forbidden, unprocessable
from sell4me_kit.events import emit
from sell4me_kit.money import SUPPORTED_CURRENCIES
from sell4me_kit.profiles import HEARD_FROM, SIGNUP_GOALS, ensure_profile, profile_prop
from sell4me_kit.services import onboarding
from sell4me_kit.services import themes as theme_service
from sell4me_kit.services.catalog import slugify

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
RESERVED_SLUGS = frozenset({"www", "api", "admin", "app", "dashboard", "shop", "store", "static", "assets", "mail", "webhooks", "sandbox", "help", "support", "status", "blog"})


async def stores_for(db: Any, user_id: str) -> list[dict[str, Any]]:
    rows = await db.fetch("SELECT s.id, s.slug, s.name, s.status, s.currency, m.role FROM store_members m JOIN stores s ON s.id = m.store_id "
                          "WHERE m.user_id = ? AND m.status = 'active' AND m.deleted_at IS NULL AND s.deleted_at IS NULL ORDER BY m.id", [user_id])
    return [dict(r) for r in rows]


@endpoint("account.me", "GET", "/account/me", area="account", summary="The signed-in user, their profile and the stores they work in",
          original="GET /login, GET /onboarding (ChooseStore), shared props")
async def me(c: Ctx):
    user_id = c.require_user()
    profile = await ensure_profile(c)
    stores = await stores_for(await c.db(), user_id)
    return {"user": profile_prop(profile), "stores": stores, "platform_admin": "platform_admin" in (c.auth.get("roles") or []),
            "needs_onboarding": not stores}


@endpoint("account.update_profile", "PATCH", "/account/profile", area="account", summary="Update the signed-in user's profile",
          fields=[{"name": "full_name", "type": "string", "max_length": 150}, {"name": "avatar_url", "type": "string", "max_length": 500},
                  {"name": "timezone_name", "type": "string", "max_length": 64}, {"name": "heard_from", "type": "string"}, {"name": "signup_goal", "type": "string"}],
          original="registration survey; the profile fields on User")
async def update_profile(c: Ctx):
    profile = await ensure_profile(c)
    db = await c.db()
    changes: dict[str, Any] = {}
    for field in ("full_name", "avatar_url", "timezone_name"):
        if field in c.input:
            changes[field] = (str(c.input[field]).strip() or None) if c.input[field] is not None else None
    if c.input.get("heard_from") in HEARD_FROM:
        changes["heard_from"] = c.input["heard_from"]
    if c.input.get("signup_goal") in SIGNUP_GOALS:
        changes["signup_goal"] = c.input["signup_goal"]
    if changes:
        await q.update(db, "profiles", profile.pk, changes)
    return profile_prop(await q.get(db, "profiles", profile.pk))


@endpoint("account.switch_store", "POST", "/account/stores/{store}/switch", area="account", summary="Make a store the one a return visit lands in",
          original="POST /switch-store")
async def switch_store(c: Ctx):
    await c.dashboard()
    db = await c.db()
    profile = await ensure_profile(c)
    await q.update(db, "profiles", profile.pk, {"last_store_id": c.store.pk})
    return {"store": {"id": c.store.pk, "slug": c.store.slug, "name": c.store.name, "status": c.store.status}, "role": c.member.role,
            "permissions": c.permissions()}


@endpoint("onboarding.options", "GET", "/onboarding/options", area="onboarding", summary="What the create-store wizard offers: templates, categories, currencies",
          original="GET /onboarding (CreateStore)")
async def onboarding_options(c: Ctx):
    c.require_user()
    return {"templates": theme_service.preset_props(), "categories": list(theme_service.CATEGORIES), "currencies": list(SUPPORTED_CURRENCIES),
            "reserved_slugs": sorted(RESERVED_SLUGS)}


@endpoint("onboarding.check_slug", "GET", "/onboarding/slug", area="onboarding", summary="Whether a store address is free",
          original="the slug check inside POST /onboarding/store")
async def check_slug(c: Ctx):
    c.require_user()
    slug = slugify(c.arg("slug") or "")
    if len(slug) < 3:
        return {"slug": slug, "available": False, "reason": "Use at least three characters."}
    if slug in RESERVED_SLUGS:
        return {"slug": slug, "available": False, "reason": "That address is reserved."}
    taken = await q.exists(await c.db(), "stores", {"slug": slug})
    return {"slug": slug, "available": not taken, "reason": "That address is taken." if taken else None}


@endpoint("onboarding.create_store", "POST", "/onboarding/stores", area="onboarding", summary="Create a store and everything it needs to function",
          fields=[{"name": "name", "type": "string", "required": True, "max_length": 150}, {"name": "slug", "type": "string"}, {"name": "currency", "type": "string"},
                  {"name": "country", "type": "string"}, {"name": "template", "type": "string"}, {"name": "colors", "type": "json"},
                  {"name": "product", "type": "json"}, {"name": "logo", "type": "string"}, {"name": "announcement", "type": "string"},
                  {"name": "discount", "type": "json"}, {"name": "invites", "type": "json"}],
          original="POST /onboarding/store")
async def create_store(c: Ctx):
    user_id = c.require_user()
    profile = await ensure_profile(c)
    data = c.input
    name = (data.get("name") or "").strip()
    slug = slugify(data.get("slug") or name)
    currency = (data.get("currency") or "NGN").upper()
    country = (data.get("country") or "NG").upper()
    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Your store needs a name."
    if currency not in SUPPORTED_CURRENCIES:
        errors["currency"] = f"Choose one of: {', '.join(SUPPORTED_CURRENCIES)}."
    db = await c.db()
    if len(slug) < 3:
        errors["slug"] = "Use at least three characters."
    elif slug in RESERVED_SLUGS:
        errors["slug"] = "That address is reserved."
    elif await q.exists(db, "stores", {"slug": slug}):
        errors["slug"] = "That address is taken."
    if errors:
        raise unprocessable("Check the highlighted fields.", "validation_failed", errors)
    owner = {"id": user_id, "email": c.email, "full_name": profile.full_name, "profile_id": profile.pk}
    try:
        store = await onboarding.create_store(
            c, owner=owner, name=name, slug=slug, currency=currency, country=country, template=data.get("template") or None,
            colors=data.get("colors") if isinstance(data.get("colors"), dict) else None, product=data.get("product") if isinstance(data.get("product"), dict) else None,
            logo=data.get("logo") if isinstance(data.get("logo"), str) else None, announcement=data.get("announcement") if isinstance(data.get("announcement"), str) else None,
            discount=data.get("discount") if isinstance(data.get("discount"), dict) else None, invites=[str(e) for e in data["invites"]] if isinstance(data.get("invites"), list) else None)
    except Exception as error:  # the unique slug index is the lock: a race with another sign-up lands here
        if "unique" in str(error).lower() or "duplicate" in str(error).lower():
            raise unprocessable("That address is taken.", "validation_failed", {"slug": "That address is taken."}) from error
        raise
    c.store = store
    await audit.record(db, store=store, actor={"id": user_id, "email": c.email, "full_name": profile.full_name}, action="store.created", resource_type="store",
                       resource_id=store.pk, summary=f"Created the store {store.name}", ip_address=c.client_ip)
    await emit(c, "store.created", store=store, owner=owner)
    return {"store": {"id": store.pk, "slug": store.slug, "name": store.name, "status": store.status, "currency": store.currency},
            "progress": await onboarding.progress(c, store)}
