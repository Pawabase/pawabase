"""The design studio: posters, coupons, social cards.

The browser owns the canvas (Fabric.js) and sends back its document as JSON. The server stores it under a size cap, keeps a validated image
thumbnail for the list, and resolves the products and coupon codes the editor offers, so a poster shows real prices and real, valid codes.
"""

from __future__ import annotations

import base64
import json as jsonlib
import re
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.audit import record_from
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import not_found, unprocessable
from sell4me_kit.money import format_money
from sell4me_kit.services import pagedata

KINDS = {"poster": (1080, 1350), "square": (1080, 1080), "story": (1080, 1920), "banner": (1500, 500), "coupon": (1200, 600), "link": (1200, 630),
         "thumbnail": (1600, 900), "a4": (1240, 1754), "custom": (1080, 1080)}
MAX_DOCUMENT_BYTES = 4_000_000
MAX_THUMBNAIL_CHARS = 120_000
_IMAGE = re.compile(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")


def _size(value: Any, fallback: int) -> int:
    try:
        return max(100, min(4000, int(value)))
    except (TypeError, ValueError):
        return fallback


def discount_label(discount: q.Row, currency: str) -> str:
    if discount.kind == "free_shipping":
        return "FREE SHIPPING"
    if discount.kind == "percentage":
        return f"{discount.value}% OFF"
    return f"{format_money(int(discount.value), currency)} OFF"


def _card(d: q.Row) -> dict[str, Any]:
    return {"id": d.pk, "title": d.title, "kind": d.kind, "width": d.width, "height": d.height, "thumbnail": d.thumbnail, "updated_at": d.updated_at}


async def _design(c: Ctx) -> q.Row:
    design = await q.first(await c.db(), "designs", {"id": c.int_arg("design_id", required=True), "store_id": c.store.pk})
    if design is None:
        raise not_found("That design")
    return design


@endpoint("designs.list", "GET", "/dash/{store}/designs", area="designs", permission="campaigns.read", summary="Saved designs and the canvas presets", original="GET /designs")
async def designs_list(c: Ctx):
    await c.dashboard("campaigns.read")
    rows = await q.find(await c.db(), "designs", {"store_id": c.store.pk}, order="updated_at DESC", limit=200)
    return {"data": [_card(d) for d in rows], "kinds": [{"key": k, "width": w, "height": h} for k, (w, h) in KINDS.items() if k != "custom"]}


@endpoint("designs.create", "POST", "/dash/{store}/designs", area="designs", permission="campaigns.manage", summary="Start a design", fields=[{"name": "kind", "type": "string"},
          {"name": "title", "type": "string"}, {"name": "width", "type": "integer"}, {"name": "height", "type": "integer"}], original="POST /designs")
async def design_create(c: Ctx):
    await c.dashboard("campaigns.manage")
    data = c.input
    kind = data.get("kind") if data.get("kind") in KINDS else "poster"
    width, height = KINDS[kind]
    if kind == "custom":
        width, height = _size(data.get("width"), 1080), _size(data.get("height"), 1080)
    design = await q.insert(await c.db(), "designs", {"store_id": c.store.pk, "title": str(data.get("title") or "").strip()[:200] or "Untitled design", "kind": kind,
                                                      "width": width, "height": height, "data": {}, "created_by_id": c.user_id})
    await record_from(c, action="design.created", resource_type="design", resource_id=design.pk, summary=f"Started the design {design.title}")
    return _card(design)


@endpoint("designs.show", "GET", "/dash/{store}/designs/{design_id}", area="designs", permission="campaigns.read", summary="The editor's payload: the document, products with real prices, valid coupons",
          original="GET /designs/{id}")
async def design_show(c: Ctx):
    await c.dashboard("campaigns.read")
    db, store = await c.db(), c.store
    design = await _design(c)
    products = await q.find(db, "products", {"store_id": store.pk, "status": "active"}, order="title", limit=100)
    cards = [await pagedata.product_card(db, p, store) for p in products]
    discounts = await q.find(db, "discounts", {"store_id": store.pk, "is_active": True, "is_automatic": False}, limit=100)
    return {"design": {**_card(design), "data": design.data or {}, "product_id": design.product_id, "discount_id": design.discount_id},
            "store": {"name": store.name, "currency": store.currency},
            "products": [{"id": k["id"], "title": k["title"], "image_url": k["image_url"], "price": k["price"]["formatted"],
                          "compare_at": k["compare_at"]["formatted"] if k["compare_at"] else None} for k in cards],
            "coupons": [{"id": d.pk, "code": d.code, "label": discount_label(d, store.currency), "title": d.title, "state": d.state} for d in discounts]}


@endpoint("designs.save", "PATCH", "/dash/{store}/designs/{design_id}", area="designs", permission="campaigns.manage", summary="Save a design (document size-capped, thumbnail validated)",
          fields=[{"name": "title", "type": "string"}, {"name": "data", "type": "json"}, {"name": "width", "type": "integer"}, {"name": "height", "type": "integer"},
                  {"name": "thumbnail", "type": "text"}, {"name": "product_id", "type": "integer"}, {"name": "discount_id", "type": "integer"}], original="POST /designs/{id}/save")
async def design_save(c: Ctx):
    await c.dashboard("campaigns.manage")
    db, store, data = await c.db(), c.store, c.input
    design = await _design(c)
    changes: dict[str, Any] = {}
    if str(data.get("title") or "").strip():
        changes["title"] = str(data["title"]).strip()[:200]
    if "data" in data:
        document = data["data"]
        if isinstance(document, str):
            try:
                document = jsonlib.loads(document)
            except ValueError:
                raise unprocessable("That design could not be read.", "validation_failed") from None
        if not isinstance(document, dict) or len(jsonlib.dumps(document)) > MAX_DOCUMENT_BYTES:
            raise unprocessable("That design is too large to save.", "validation_failed")
        changes["data"] = document
    for name in ("width", "height"):
        if name in data:
            changes[name] = _size(data[name], design[name])
    thumb = data.get("thumbnail")
    if isinstance(thumb, str) and thumb and len(thumb) <= MAX_THUMBNAIL_CHARS and _IMAGE.match(thumb):
        base64.b64decode(thumb.split(",", 1)[1], validate=True)
        changes["thumbnail"] = thumb
    # A product or coupon must belong to this store: an id from another tenant is silently ignored, never linked.
    for key, table in (("product_id", "products"), ("discount_id", "discounts")):
        if key in data:
            if data[key] in (None, ""):
                changes[key] = None
            elif await q.exists(db, table, {"id": int(data[key]), "store_id": store.pk}):
                changes[key] = int(data[key])
    if changes:
        await q.update(db, "designs", design.pk, changes)
    return {"ok": True, "updated_at": (await q.get(db, "designs", design.pk)).updated_at}


@endpoint("designs.duplicate", "POST", "/dash/{store}/designs/{design_id}/duplicate", area="designs", permission="campaigns.manage", summary="Copy a design", original="POST /designs/{id}/duplicate")
async def design_duplicate(c: Ctx):
    await c.dashboard("campaigns.manage")
    design = await _design(c)
    copy = await q.insert(await c.db(), "designs", {"store_id": design.store_id, "title": f"{design.title} copy"[:200], "kind": design.kind, "width": design.width, "height": design.height,
                                                   "data": design.data, "thumbnail": design.thumbnail, "product_id": design.product_id, "discount_id": design.discount_id,
                                                   "created_by_id": design.created_by_id})
    return _card(copy)


@endpoint("designs.delete", "DELETE", "/dash/{store}/designs/{design_id}", area="designs", permission="campaigns.manage", summary="Delete a design", original="POST /designs/{id}/delete")
async def design_delete(c: Ctx):
    await c.dashboard("campaigns.manage")
    design = await _design(c)
    await q.soft_delete(await c.db(), "designs", design.pk)
    await record_from(c, action="design.deleted", resource_type="design", resource_id=design.pk, summary=f"Deleted the design {design.title}")
    return {"deleted": True}
