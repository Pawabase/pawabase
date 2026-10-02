"""Storefront configuration: theme, templates, palettes, navigation, domains, SEO and shipping.

Everything a merchant can set here is *data*, never code: colours are matched against a strict pattern, fonts against a fixed list, and homepage
sections against a known set of types, because a "fully customisable" storefront that accepts arbitrary strings into a rendered page is a
stored-XSS feature with a friendly name.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.audit import record_from
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import not_found, unprocessable
from sell4me_kit.events import emit
from sell4me_kit.money import Money, to_minor
from sell4me_kit.services import onboarding
from sell4me_kit.services import storefront as storefront_service
from sell4me_kit.services import themes as theme_service
from sell4me_kit.services.dns import lookup_txt
from sell4me_kit.urls import storefront_url

_HOSTNAME = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


def _url_or_none(value: Any) -> str | None:
    """Only an http(s) or root-relative URL: a ``javascript:`` or ``data:`` URL in a logo field would be executed the moment the storefront renders it."""
    text = str(value or "").strip()
    return text[:1000] if text and (text.startswith("/") or text.startswith(("http://", "https://"))) else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── templates, palettes, theme ───────────────────────────────────────────

@endpoint("storefront.templates", "GET", "/dash/{store}/storefront/templates", area="storefront", permission="storefront.read",
          summary="The template gallery: finished shops (only each homepage tree is sent; ten whole sites is most of a megabyte)", original="GET /storefront/templates, GET /storefront, GET /storefront/themes")
async def templates(c: Ctx):
    await c.dashboard("storefront.read")
    db, store = await c.db(), c.store
    theme = await storefront_service.ensure_theme(db, store)
    settings = await c.settings()
    return {"templates": theme_service.template_props(store.name), "active": theme.name, "storefront_url": storefront_url(settings, store, preview_link=True),
            "palettes": theme_service.palette_props(), "categories": list(theme_service.CATEGORIES)}


@endpoint("storefront.template", "GET", "/dash/{store}/storefront/templates/{key}", area="storefront", permission="storefront.read",
          summary="Every page of one template, as block trees, for the preview", original="GET /storefront/templates/{key}")
async def template_detail(c: Ctx):
    await c.dashboard("storefront.read")
    try:
        return {"pages": theme_service.template_pages(c.params.get("key"), c.store.name)}
    except KeyError:
        raise not_found("That template") from None


@endpoint("storefront.theme", "GET", "/dash/{store}/storefront/theme", area="storefront", permission="storefront.read", summary="The store's theme as the storefront renders it (sanitised)",
          original="the theme props of the builder")
async def theme_get(c: Ctx):
    await c.dashboard("storefront.read")
    db = await c.db()
    theme = await storefront_service.ensure_theme(db, c.store)
    return {"theme": storefront_service.theme_prop(theme, c.store), "fonts": list(storefront_service.FONTS)}


@endpoint("storefront.save_theme", "PATCH", "/dash/{store}/storefront/theme", area="storefront", permission="storefront.update", summary="Save the merchant's own theme edits (every value sanitised)",
          fields=[{"name": "colors", "type": "json"}, {"name": "fonts", "type": "json"}, {"name": "corner_style", "type": "string"}, {"name": "announcement", "type": "string"},
                  {"name": "footer_text", "type": "text"}, {"name": "logo_url", "type": "string"}, {"name": "header_links", "type": "json"}, {"name": "footer_links", "type": "json"},
                  {"name": "social_links", "type": "json"}, {"name": "name", "type": "string"}], original="POST /storefront/palette")
async def theme_save(c: Ctx):
    await c.dashboard("storefront.update")
    db, store = await c.db(), c.store
    theme = await storefront_service.ensure_theme(db, store)
    await theme_service.save_theme(db, theme, store, c.input)
    await onboarding.complete_step(c, store, "storefront")
    await record_from(c, action="storefront.updated", resource_type="theme", resource_id=theme.pk, summary=f"Updated the theme {theme.name}")
    return {"theme": storefront_service.theme_prop(await q.get(db, "themes", theme.pk), store), "message": "Theme saved."}


@endpoint("storefront.apply_template", "POST", "/dash/{store}/storefront/templates/apply", area="storefront", permission="storefront.update",
          summary="Apply a template: its look, and (only if asked) its pages, as drafts", fields=[{"name": "preset", "type": "string", "required": True}, {"name": "replace_pages", "type": "boolean"}],
          original="POST /storefront/templates/apply")
async def apply_template(c: Ctx):
    """``replace_pages`` is the merchant's explicit choice and defaults to off. Off: a theme fills in only the pages the store lacks; on: every page it ships is rewritten into the
    *draft*, so nothing reaches a shopper until they publish. Getting this wrong once loses somebody an afternoon's writing, which is why the screen asks."""
    await c.dashboard("storefront.update")
    db, store = await c.db(), c.store
    theme = await storefront_service.ensure_theme(db, store)
    try:
        theme, written = await theme_service.apply_preset(db, theme, str(c.input.get("preset") or ""), store=store, replace_pages=bool(c.input.get("replace_pages")))
    except KeyError:
        raise not_found("That theme") from None
    await onboarding.complete_step(c, store, "storefront")
    await record_from(c, action="storefront.updated", resource_type="theme", resource_id=theme.pk, summary=f"Applied the {theme.name} theme ({written} pages)")
    return {"theme": theme.name, "pages_written": written,
            "message": f"{theme.name} applied." + (f" {written} page{'s' if written != 1 else ''} added as drafts — publish when ready." if written else " Your existing pages were left alone.")}


@endpoint("storefront.apply_palette", "POST", "/dash/{store}/storefront/palette/apply", area="storefront", permission="storefront.update",
          summary="Recolour the theme from a palette; no pages are touched", fields=[{"name": "palette", "type": "string", "required": True}], original="POST /storefront/palette/apply")
async def apply_palette(c: Ctx):
    await c.dashboard("storefront.update")
    db, store = await c.db(), c.store
    theme = await storefront_service.ensure_theme(db, store)
    try:
        theme = await theme_service.apply_palette(db, theme, str(c.input.get("palette") or ""))
    except KeyError:
        raise not_found("That palette") from None
    await onboarding.complete_step(c, store, "storefront")
    await record_from(c, action="storefront.updated", resource_type="theme", resource_id=theme.pk, summary=f"Applied the {theme.name} palette")
    return {"theme": storefront_service.theme_prop(await q.get(db, "themes", theme.pk), store), "message": f"{theme.name} applied."}


# ── domains ──────────────────────────────────────────────────────────────

def _domain_prop(d: q.Row, settings: Any) -> dict[str, Any]:
    return {"id": d.pk, "hostname": d.hostname, "is_platform": d.is_platform, "is_primary": d.is_primary, "status": d.status, "verification_token": d.verification_token,
            "verified_at": d.verified_at, "check_message": d.check_message, "url": settings.shop_url("", d.hostname)}


@endpoint("domains.list", "GET", "/dash/{store}/storefront/domains", area="storefront", permission="settings.read", summary="Domains, and what to publish in DNS to verify one", original="GET /storefront/domains")
async def domains_list(c: Ctx):
    await c.dashboard("settings.read")
    settings = await c.settings()
    rows = await q.find(await c.db(), "domains", {"store_id": c.store.pk}, order="is_platform DESC, hostname")
    return {"data": [_domain_prop(d, settings) for d in rows],
            # What the merchant has to publish, shown beside each pending domain rather than in documentation they would have to go and find.
            "instructions": {"record_type": "TXT", "host": "_commerce-verify", "cname_target": settings.storefront_suffix.split(":")[0]}}


@endpoint("domains.add", "POST", "/dash/{store}/storefront/domains", area="storefront", permission="settings.update", summary="Add a custom domain (pending until its TXT record is verified)",
          fields=[{"name": "hostname", "type": "string", "required": True}], original="POST /storefront/domains")
async def domain_add(c: Ctx):
    await c.dashboard("settings.update")
    db = await c.db()
    hostname = (c.input.get("hostname") or "").strip().lower().rstrip(".").removeprefix("https://").removeprefix("http://").split("/")[0]
    if not _HOSTNAME.match(hostname):
        raise unprocessable("Enter a hostname like shop.example.com.", "validation_failed", {"hostname": "Enter a hostname like shop.example.com."})
    if await q.exists(db, "domains", {"hostname": hostname}):
        # The same message whether it is taken by this store or another: distinguishing them would let anyone probe which domains the platform serves.
        raise unprocessable("That domain is already registered.", "validation_failed", {"hostname": "That domain is already registered."})
    domain = await q.insert(db, "domains", {"store_id": c.store.pk, "hostname": hostname, "status": "pending", "verification_token": secrets.token_urlsafe(24),
                                            "is_platform": False, "is_primary": False})
    return {**_domain_prop(domain, await c.settings()), "message": f"{hostname} added. Publish the TXT record, then verify."}


@endpoint("domains.verify", "POST", "/dash/{store}/storefront/domains/{domain_id}/verify", area="storefront", permission="settings.update",
          summary="Check a custom domain's DNS (a real TXT lookup: an unverified domain stays unusable, or anyone could serve another merchant's shop from a hostname they control)",
          original="POST /storefront/domains/{id}/verify")
async def domain_verify(c: Ctx):
    await c.dashboard("settings.update")
    db = await c.db()
    domain = await q.first(db, "domains", {"id": c.int_arg("domain_id", required=True), "store_id": c.store.pk})
    if domain is None or domain.is_platform:
        raise not_found("That domain")
    now = datetime.now(UTC)
    records = await lookup_txt(f"_commerce-verify.{domain.hostname}")
    if records is None:  # no resolver: stay pending and say so, rather than marking verified on an inconclusive check
        message = "Could not query DNS from this server. Check again in a moment."
        await q.update(db, "domains", domain.pk, {"last_checked_at": now, "check_message": message})
        return {"status": domain.status, "verified": False, "message": message}
    if domain.verification_token and domain.verification_token in records:
        await q.update(db, "domains", domain.pk, {"status": "verified", "verified_at": now, "last_checked_at": now, "check_message": None})
        await emit(c, "domain.verified", store=c.store, domain=domain, actor={"id": c.user_id, "email": c.email}, ip_address=c.client_ip)
        return {"status": "verified", "verified": True, "message": f"{domain.hostname} is verified."}
    message = "The TXT record wasn't found. DNS can take a while to propagate."
    await q.update(db, "domains", domain.pk, {"last_checked_at": now, "check_message": message})
    return {"status": domain.status, "verified": False, "message": message}


# ── SEO ──────────────────────────────────────────────────────────────────

@endpoint("seo.get", "GET", "/dash/{store}/storefront/seo", area="storefront", permission="storefront.read", summary="SEO settings, canonical addresses, and how many products lack metadata",
          original="GET /storefront/seo")
async def seo_get(c: Ctx):
    await c.dashboard("storefront.read")
    db, store = await c.db(), c.store
    theme = await storefront_service.ensure_theme(db, store)
    base = storefront_url(await c.settings(), store)  # unsigned on purpose: a preview token in a canonical URL would be a token in someone's sitemap
    return {"seo": {"title": theme.seo_title, "description": theme.seo_description, "og_image_url": theme.og_image_url, "robots": theme.robots_policy},
            "urls": {"sitemap": f"{base}sitemap.xml", "robots": f"{base}robots.txt", "canonical": base},
            "products_missing": await q.count(db, "products", {"store_id": store.pk, "status": "active", "seo_description": None}),
            "products_total": await q.count(db, "products", {"store_id": store.pk, "status": "active"})}


@endpoint("seo.save", "POST", "/dash/{store}/storefront/seo", area="storefront", permission="storefront.update", summary="Save SEO settings",
          fields=[{"name": "title", "type": "string"}, {"name": "description", "type": "string"}, {"name": "og_image_url", "type": "string"}, {"name": "robots", "type": "string"}],
          original="POST /storefront/seo")
async def seo_save(c: Ctx):
    await c.dashboard("storefront.update")
    db, data = await c.db(), c.input
    theme = await storefront_service.ensure_theme(db, c.store)
    changes: dict[str, Any] = {"seo_title": (data.get("title") or "").strip()[:200] or None, "seo_description": (data.get("description") or "").strip()[:400] or None,
                               "og_image_url": _url_or_none(data.get("og_image_url"))}
    if data.get("robots") in ("index,follow", "noindex,nofollow", "index,nofollow", "noindex,follow"):
        changes["robots_policy"] = data["robots"]
    await q.update(db, "themes", theme.pk, changes)
    return {"message": "SEO settings saved."}


# ── shipping ─────────────────────────────────────────────────────────────

@endpoint("shipping.list", "GET", "/dash/{store}/shipping", area="settings", permission="settings.read", summary="Shipping zones and their rates", original="GET /settings/shipping")
async def shipping_list(c: Ctx):
    await c.dashboard("settings.read")
    db, store = await c.db(), c.store
    out = []
    for zone in await q.find(db, "shipping_zones", {"store_id": store.pk}, order="position"):
        rates = await q.find(db, "shipping_rates", {"zone_id": zone.pk}, order="position")
        out.append({"id": zone.pk, "name": zone.name, "countries": zone.countries or [],
                    "rates": [{"id": r.pk, "name": r.name, "description": r.description, "kind": r.kind, "price": Money(r.price_minor, store.currency).as_prop(), "price_minor": r.price_minor,
                               "min_weight_grams": r.min_weight_grams, "max_weight_grams": r.max_weight_grams,
                               "min_subtotal": Money(r.min_subtotal_minor, store.currency).as_prop() if r.min_subtotal_minor is not None else None,
                               "max_subtotal": Money(r.max_subtotal_minor, store.currency).as_prop() if r.max_subtotal_minor is not None else None,
                               "delivery_estimate": r.delivery_estimate, "is_active": r.is_active} for r in rates]})
    return {"zones": out}


@endpoint("shipping.save_zone", "POST", "/dash/{store}/shipping", area="settings", permission="settings.update",
          summary="Save a shipping zone and its rates (rates are replaced wholesale for the zone being saved; no other zone is touched)",
          fields=[{"name": "id", "type": "integer"}, {"name": "name", "type": "string", "required": True}, {"name": "countries", "type": "json"}, {"name": "rates", "type": "json"}],
          original="POST /settings/shipping")
async def shipping_save(c: Ctx):
    await c.dashboard("settings.update")
    db, store, data = await c.db(), c.store, c.input
    name = (data.get("name") or "").strip()
    if not name:
        raise unprocessable("A zone needs a name.", "validation_failed", {"name": "A zone needs a name."})
    zone = await q.first(db, "shipping_zones", {"id": int(data["id"]), "store_id": store.pk}) if data.get("id") else None
    countries = [str(x).strip().upper()[:2] if str(x).strip() != "*" else "*" for x in (data.get("countries") or []) if str(x).strip()]
    async with c.tx() as tx:
        if zone is None:
            zone = await q.insert(tx, "shipping_zones", {"store_id": store.pk, "name": name, "countries": countries or ["*"], "position": 0})
        else:
            await q.update(tx, "shipping_zones", zone.pk, {"name": name, "countries": countries or ["*"]})
        if isinstance(data.get("rates"), list):
            await tx.execute("DELETE FROM shipping_rates WHERE zone_id = ?", [zone.pk])
            for position, spec in enumerate(data["rates"]):
                if not isinstance(spec, dict) or not str(spec.get("name") or "").strip():
                    continue
                await q.insert(tx, "shipping_rates", {
                    "zone_id": zone.pk, "store_id": store.pk, "name": str(spec["name"]).strip()[:150], "description": str(spec.get("description") or "").strip() or None,
                    "kind": spec.get("kind") if spec.get("kind") in ("flat", "weight", "price") else "flat", "price_minor": to_minor(spec.get("price") or 0, store.currency),
                    "min_weight_grams": _int_or_none(spec.get("min_weight_grams")), "max_weight_grams": _int_or_none(spec.get("max_weight_grams")),
                    "min_subtotal_minor": to_minor(spec["min_subtotal"], store.currency) if spec.get("min_subtotal") not in (None, "") else None,
                    "max_subtotal_minor": to_minor(spec["max_subtotal"], store.currency) if spec.get("max_subtotal") not in (None, "") else None,
                    "delivery_estimate": str(spec.get("delivery_estimate") or "").strip() or None, "is_active": bool(spec.get("is_active", True)), "position": position})
    await onboarding.complete_step(c, store, "shipping")
    return {"id": zone.pk, "message": "Shipping saved."}


@endpoint("shipping.delete_zone", "DELETE", "/dash/{store}/shipping/{zone_id}", area="settings", permission="settings.update",
          summary="Delete a shipping zone and its rates", original="(added: the original could only replace a zone's rates)")
async def shipping_delete(c: Ctx):
    await c.dashboard("settings.update")
    db = await c.db()
    zone = await q.first(db, "shipping_zones", {"id": c.int_arg("zone_id", required=True), "store_id": c.store.pk})
    if zone is None:
        raise not_found("That zone")
    await db.execute("UPDATE shipping_rates SET deleted_at = ? WHERE zone_id = ?", [datetime.now(UTC), zone.pk])
    await q.soft_delete(db, "shipping_zones", zone.pk)
    return {"deleted": True}
