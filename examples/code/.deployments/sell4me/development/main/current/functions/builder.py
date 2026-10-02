"""The page builder's endpoints.

The browser sends a *tree* and gets one back, and every tree passes through ``builder.sanitise`` on the way in. There is no endpoint that
writes a block, moves a block or edits a field: the client owns the editing experience and the server owns the schema. Draft and published
are separate columns so rearranging the homepage at 11pm is not done live in front of customers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.audit import record_from
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import not_found, unprocessable
from sell4me_kit.events import emit
from sell4me_kit.services import builder as builder_service
from sell4me_kit.services import pagedata
from sell4me_kit.services import sections as section_presets
from sell4me_kit.services import storefront as storefront_service
from sell4me_kit.services import themes as theme_service
from sell4me_kit.services.catalog import unique_slug
from sell4me_kit.urls import storefront_url

PAGE_KINDS = ("home", "page", "collection_template", "product_template", "header")
SINGLETON_KINDS = ("home", "collection_template", "product_template", "header")
#: Revisions kept per page: enough to undo a bad afternoon, few enough that a page edited for a year is not a thousand rows of JSON.
REVISION_LIMIT = 30


def _has_changes(page: q.Row) -> bool:
    return bool(page.is_published and (page.draft or []) != (page.published or []))


def _path(page: q.Row) -> str:
    return {"home": "/", "collection_template": "/collections", "product_template": "/products"}.get(page.kind, f"/pages/{page.slug}")


async def _page(c: Ctx) -> q.Row:
    page = await q.first(await c.db(), "storefront_pages", {"id": c.int_arg("page_id", required=True), "store_id": c.store.pk})
    if page is None:
        raise not_found("That page")
    return page


async def _snapshot(c: Ctx, db: Any, page: q.Row, *, reason: str, label: str | None = None) -> None:
    """A copy of the current draft *before* it changes (that is what a revision is for), pruning the oldest."""
    await q.insert(db, "page_revisions", {"page_id": page.pk, "store_id": page.store_id, "tree": page.draft or [], "reason": reason, "label": label, "actor_id": c.user_id})
    keep = await db.fetch("SELECT id FROM page_revisions WHERE page_id = ? AND deleted_at IS NULL ORDER BY id DESC LIMIT ?", [page.pk, REVISION_LIMIT])
    if len(keep) >= REVISION_LIMIT:
        oldest_kept = min(int(r["id"]) for r in keep)
        await db.execute("DELETE FROM page_revisions WHERE page_id = ? AND id < ?", [page.pk, oldest_kept])


def _imagery_for(theme: Any) -> str:
    """Matched by the theme's *name*: a store on a hand-built palette gets the neutral set; we know the colours they chose and nothing about what they sell."""
    name = (getattr(theme, "name", "") or "").strip().lower()
    for preset in theme_service.PRESETS:
        if preset.name.lower() == name:
            return preset.imagery
    return "studio"


def _summary(page: q.Row) -> dict[str, Any]:
    return {"id": page.pk, "title": page.title, "slug": page.slug, "kind": page.kind, "path": _path(page), "is_published": page.is_published, "has_changes": _has_changes(page),
            "blocks": builder_service.count_blocks(page.draft or []), "updated_at": page.updated_at, "published_at": page.published_at}


@endpoint("pages.list", "GET", "/dash/{store}/pages", area="storefront", permission="storefront.read", summary="Every page this store has built", original="GET /storefront/pages")
async def pages_list(c: Ctx):
    await c.dashboard("storefront.read")
    rows = await q.find(await c.db(), "storefront_pages", {"store_id": c.store.pk}, order="kind, title")
    return {"data": [_summary(p) for p in rows],
            "kinds": [{"key": k, "label": k.replace("_", " ").title(), "singleton": k in SINGLETON_KINDS} for k in PAGE_KINDS],
            "storefront_url": storefront_url(await c.settings(), c.store, preview_link=True)}


@endpoint("pages.show", "GET", "/dash/{store}/pages/{page_id}", area="storefront", permission="storefront.update",
          summary="The builder's payload: the tree, the block registry, theme, palettes, section presets, pickable products/collections, real canvas data, revisions",
          original="GET /storefront/pages/{id}")
async def page_show(c: Ctx):
    """Ships the whole registry alongside the tree: the block library, inspector controls and renderer are generated from it, so a new block type never needs a matching change here."""
    await c.dashboard("storefront.update")
    db, store = await c.db(), c.store
    page = await _page(c)
    theme = await storefront_service.ensure_theme(db, store)
    revisions = await db.fetch(
        "SELECT r.id, r.reason, r.label, r.created_at, u.full_name AS actor_name FROM page_revisions r LEFT JOIN profiles u ON u.user_id = r.actor_id "
        "WHERE r.page_id = ? AND r.deleted_at IS NULL ORDER BY r.id DESC LIMIT 20", [page.pk])
    collections = await db.fetch("SELECT id, title FROM collections WHERE store_id = ? AND deleted_at IS NULL ORDER BY title", [store.pk])
    products = await db.fetch("SELECT id, title FROM products WHERE store_id = ? AND status = 'active' AND deleted_at IS NULL ORDER BY title LIMIT 500", [store.pk])
    return {
        "page": {**_summary(page), "tree": builder_service.expand_legacy(page.draft or []), "seo_title": page.seo_title, "seo_description": page.seo_description,
                 "og_image_url": page.og_image_url, "noindex": page.noindex, "settings": page.settings or {}},
        "catalogue": builder_service.catalogue(),
        "theme": storefront_service.theme_prop(theme, store),
        "palettes": theme_service.palette_props(),
        "site": {"logo_url": theme.logo_url, "announcement": theme.announcement, "header_links": theme.header_links or [], "footer_links": theme.footer_links or [],
                 "footer_text": theme.footer_text, "palette": theme.name},
        "sections": section_presets.catalogue(store.name, _imagery_for(theme)),
        "collections": [{"id": str(r["id"]), "title": r["title"]} for r in collections],
        "products": [{"id": str(r["id"]), "title": r["title"]} for r in products],
        # Real products and collections resolved exactly as the shop resolves them, so a grid on the canvas shows the merchant's own tiles.
        "canvas": await pagedata.block_context(db, store, page.draft or []),
        "revisions": [{"id": r["id"], "reason": r["reason"], "label": r["label"], "actor": r["actor_name"] or "System", "created_at": r["created_at"]} for r in revisions],
        "preview_url": storefront_url(await c.settings(), store, _path(page), preview_link=True),
    }


@endpoint("pages.create", "POST", "/dash/{store}/pages", area="storefront", permission="storefront.update", summary="Add a page (home and the two templates and header are singletons)",
          fields=[{"name": "title", "type": "string", "required": True}, {"name": "kind", "type": "string"}, {"name": "slug", "type": "string"}], original="POST /storefront/pages")
async def page_create(c: Ctx):
    await c.dashboard("storefront.update")
    db, store, data = await c.db(), c.store, c.input
    title = (data.get("title") or "").strip()
    kind = data.get("kind") if data.get("kind") in PAGE_KINDS else "page"
    if not title:
        raise unprocessable("Give the page a title.", "validation_failed", {"title": "Give the page a title."})
    if kind in SINGLETON_KINDS and await q.exists(db, "storefront_pages", {"store_id": store.pk, "kind": kind}):
        message = f"This store already has a {kind.replace('_', ' ')}. Edit that one instead."
        raise unprocessable(message, "validation_failed", {"kind": message})
    slug = "home" if kind == "home" else await unique_slug(db, "storefront_pages", store, data.get("slug") or title)
    draft = builder_service.default_tree(store.name) if kind == "home" else builder_service.default_header() if kind == "header" else []
    page = await q.insert(db, "storefront_pages", {"store_id": store.pk, "title": title, "slug": slug, "kind": kind, "draft": draft, "is_published": False, "created_by_id": c.user_id})
    await record_from(c, action="page.created", resource_type="page", resource_id=page.pk, summary=f"Created the page {title}")
    return {**_summary(page), "message": "Page created."}


@endpoint("pages.save", "PATCH", "/dash/{store}/pages/{page_id}", area="storefront", permission="storefront.update",
          summary="Autosave the draft: the tree is rebuilt from the registry, so nothing the browser invents is stored; answers with the server's version",
          fields=[{"name": "tree", "type": "json"}, {"name": "title", "type": "string"}, {"name": "seo_title", "type": "string"}, {"name": "seo_description", "type": "string"},
                  {"name": "og_image_url", "type": "string"}, {"name": "noindex", "type": "boolean"}, {"name": "settings", "type": "json"}],
          original="POST /storefront/pages/{id}/save")
async def page_save(c: Ctx):
    await c.dashboard("storefront.update")
    db, data = await c.db(), c.input
    page = await _page(c)
    changes: dict[str, Any] = {}
    tree = page.draft or []
    if "tree" in data:
        tree = builder_service.sanitise(data.get("tree"))
        if page.draft and page.draft != tree:
            await _snapshot(c, db, page, reason="autosave")
        changes["draft"] = tree
    for name in ("seo_title", "seo_description"):
        if name in data:
            changes[name] = str(data[name] or "").strip() or None
    if "og_image_url" in data:
        url = str(data["og_image_url"] or "").strip()
        changes["og_image_url"] = url if url.startswith(("http://", "https://", "/")) else None
    if "noindex" in data:
        changes["noindex"] = bool(data["noindex"])
    if str(data.get("title") or "").strip():
        changes["title"] = str(data["title"]).strip()[:200]
    if isinstance(data.get("settings"), dict):
        changes["settings"] = data["settings"]
    if changes:
        await q.update(db, "storefront_pages", page.pk, changes)
        page = await q.get(db, "storefront_pages", page.pk)
    # The tree comes back so the client adopts the server's version: anything it sent that was dropped disappears from the canvas at once.
    return {"saved": True, "blocks": builder_service.count_blocks(tree), "has_changes": _has_changes(page), "saved_at": datetime.now(UTC).isoformat(), "tree": tree}


async def _publish_one(c: Ctx, db: Any, page: q.Row, label: str) -> None:
    await _snapshot(c, db, page, reason="publish", label=label)
    await q.update(db, "storefront_pages", page.pk, {"published": page.draft, "is_published": True, "published_at": datetime.now(UTC)})
    await record_from(c, action="page.published", resource_type="page", resource_id=page.pk, summary=f"Published the page {page.title}")


@endpoint("pages.publish", "POST", "/dash/{store}/pages/{page_id}/publish", area="storefront", permission="storefront.update", summary="Make the draft live", original="POST /storefront/pages/{id}/publish")
async def page_publish(c: Ctx):
    await c.dashboard("storefront.update")
    db, store = await c.db(), c.store
    page = await _page(c)
    if not page.draft:
        raise unprocessable("There is nothing on this page to publish yet.", "validation_failed")
    await _publish_one(c, db, page, "Published")
    await emit(c, "store.launched" if page.kind == "home" else "product.updated", store=store)
    return {"published": True, "message": f"{page.title} is live."}


@endpoint("pages.publish_all", "POST", "/dash/{store}/pages/publish-all", area="storefront", permission="storefront.update",
          summary="Publish every page whose draft would change what is live (empty drafts are skipped, each page still gets its snapshot and audit entry)",
          original="POST /storefront/pages/publish-all")
async def pages_publish_all(c: Ctx):
    await c.dashboard("storefront.update")
    db, store = await c.db(), c.store
    done = [p for p in await q.find(db, "storefront_pages", {"store_id": store.pk}, order="kind, title") if p.draft and (not p.is_published or _has_changes(p))]
    for page in done:
        await _publish_one(c, db, page, "Published (all pages)")
    if done:
        await emit(c, "store.launched" if any(p.kind == "home" for p in done) else "product.updated", store=store)
    return {"published": len(done), "message": f"{len(done)} page{'s' if len(done) != 1 else ''} published." if done else "Everything is already live."}


@endpoint("pages.unpublish", "POST", "/dash/{store}/pages/{page_id}/unpublish", area="storefront", permission="storefront.update",
          summary="Take a page off the storefront, keeping the draft (the published tree is cleared too, so it never says 'not published' while still serving)",
          original="POST /storefront/pages/{id}/unpublish")
async def page_unpublish(c: Ctx):
    await c.dashboard("storefront.update")
    page = await _page(c)
    await q.update(await c.db(), "storefront_pages", page.pk, {"is_published": False, "published": None})
    return {"published": False, "message": f"{page.title} is no longer public."}


@endpoint("pages.restore", "POST", "/dash/{store}/pages/{page_id}/restore/{revision_id}", area="storefront", permission="storefront.update",
          summary="Put an earlier version back into the *draft* (never straight to live); what it replaces is snapshotted first, so a restore is undoable",
          original="POST /storefront/pages/{id}/revisions/{rev}/restore")
async def page_restore(c: Ctx):
    await c.dashboard("storefront.update")
    db = await c.db()
    page = await _page(c)
    revision = await q.first(db, "page_revisions", {"id": c.int_arg("revision_id", required=True), "page_id": page.pk})
    if revision is None:
        raise not_found("That version")
    await _snapshot(c, db, page, reason="restore", label="Before restore")
    tree = builder_service.sanitise(revision.tree)
    await q.update(db, "storefront_pages", page.pk, {"draft": tree})
    return {"tree": tree, "message": "That version is back in your draft. Publish when you are happy."}


@endpoint("pages.delete", "DELETE", "/dash/{store}/pages/{page_id}", area="storefront", permission="storefront.update", summary="Delete a page (refused for the singletons)",
          original="POST /storefront/pages/{id}/delete")
async def page_delete(c: Ctx):
    await c.dashboard("storefront.update")
    db = await c.db()
    page = await _page(c)
    if page.kind in SINGLETON_KINDS:
        raise unprocessable(f"Your {page.kind.replace('_', ' ')} cannot be deleted — unpublish it instead.", "validation_failed")
    await record_from(c, action="page.deleted", resource_type="page", resource_id=page.pk, summary=f"Deleted the page {page.title}")
    await q.soft_delete(db, "storefront_pages", page.pk)
    return {"deleted": True}


@endpoint("pages.new_block", "POST", "/dash/{store}/pages/blocks/new", area="storefront", permission="storefront.update",
          summary="A fresh block of a requested type, built on the server so it has exactly the shape sanitise will accept", fields=[{"name": "type", "type": "string", "required": True}],
          original="POST /storefront/blocks")
async def new_block(c: Ctx):
    await c.dashboard("storefront.update")
    try:
        return {"block": builder_service.new_block(str(c.input.get("type") or ""))}
    except KeyError as error:
        raise unprocessable(str(error), "validation_failed") from None


@endpoint("pages.new_section", "POST", "/dash/{store}/pages/sections/new", area="storefront", permission="storefront.update",
          summary="A section preset (a whole prebuilt band) drawn with this store's theme imagery", fields=[{"name": "key", "type": "string", "required": True}],
          original="(the sections panel's preset catalogue)")
async def new_section(c: Ctx):
    await c.dashboard("storefront.update")
    theme = await storefront_service.ensure_theme(await c.db(), c.store)
    try:
        return {"blocks": section_presets.build(str(c.input.get("key") or ""), c.store.name, _imagery_for(theme))}
    except KeyError as error:
        raise unprocessable(str(error), "validation_failed") from None
