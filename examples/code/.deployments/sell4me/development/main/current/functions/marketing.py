"""Discounts and campaigns.

A discount is the rule; a campaign is the reason it exists plus what it earned. They are separate so the same rule can serve two campaigns
and still be reported on separately. Campaign results are *attributed*, narrowly and honestly: an order counts if it used the campaign's
discount code or names the campaign.
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
from sell4me_kit.money import Money, to_minor
from sell4me_kit.services import campaigns, segments

CAMPAIGN_TYPES = ("discount", "product_promotion", "abandoned_cart", "reactivation", "seasonal", "announcement")


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed or None


def _date_or_none(value: Any) -> datetime | None:
    """A browser ``datetime-local`` value as an aware UTC datetime (it carries no zone, and a naive datetime compared with an aware one raises)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ── discounts ────────────────────────────────────────────────────────────

def _discount_prop(d: q.Row, currency: str) -> dict[str, Any]:
    return {
        "id": d.pk, "code": d.code, "title": d.title, "description": d.description, "kind": d.kind, "value": d.value,
        # A percentage's value is a percent, a fixed amount's is money: the label is decided here, where the kind is known.
        "value_label": f"{d.value}%" if d.kind == "percentage" else "Free shipping" if d.kind == "free_shipping" else Money(d.value, currency).format(),
        "scope": d.scope, "scope_ids": d.scope_ids or [], "minimum_order": Money(d.minimum_order_minor or 0, currency).as_prop(),
        "maximum_discount": Money(d.maximum_discount_minor, currency).as_prop() if d.maximum_discount_minor else None,
        "usage_limit": d.usage_limit, "per_customer_limit": d.per_customer_limit, "usage_count": d.usage_count, "starts_at": d.starts_at, "ends_at": d.ends_at,
        "is_active": d.is_active, "is_automatic": d.is_automatic, "segment_id": d.segment_id, "state": d.state}


async def _options(db: Any, store_id: int) -> dict[str, list[dict[str, Any]]]:
    return {"segments": [{"id": r.pk, "name": r.name} for r in await q.find(db, "segments", {"store_id": store_id}, order="name")],
            "products": [{"id": r.pk, "title": r.title} for r in await q.find(db, "products", {"store_id": store_id}, order="title", limit=500)]}


@endpoint("discounts.list", "GET", "/dash/{store}/discounts", area="marketing", permission="discounts.read", summary="List discounts with state counts and the pickers' options",
          original="GET /marketing/discounts")
async def discounts_list(c: Ctx):
    await c.dashboard("discounts.read")
    db, store = await c.db(), c.store
    state = c.arg("state", "all")
    rows = await q.find(db, "discounts", {"store_id": store.pk}, order="id DESC", limit=300)
    data = [_discount_prop(d, store.currency) for d in rows]
    counts: dict[str, int] = {"all": len(rows)}
    for d in rows:
        counts[d.state] = counts.get(d.state, 0) + 1
    return {"data": [d for d in data if state == "all" or d["state"] == state], "state": state, "counts": counts, **await _options(db, store.pk)}


@endpoint("discounts.save", "POST", "/dash/{store}/discounts", area="marketing", permission="discounts.manage", summary="Create or update a discount (percentage, fixed amount, free shipping; automatic or by code)",
          fields=[{"name": "id", "type": "integer"}, {"name": "code", "type": "string", "required": True}, {"name": "title", "type": "string"}, {"name": "description", "type": "string"},
                  {"name": "kind", "type": "string"}, {"name": "value", "type": "string"}, {"name": "scope", "type": "string"}, {"name": "scope_ids", "type": "json"},
                  {"name": "minimum_order", "type": "string"}, {"name": "maximum_discount", "type": "string"}, {"name": "usage_limit", "type": "integer"},
                  {"name": "per_customer_limit", "type": "integer"}, {"name": "starts_at", "type": "string"}, {"name": "ends_at", "type": "string"},
                  {"name": "is_active", "type": "boolean"}, {"name": "is_automatic", "type": "boolean"}, {"name": "segment_id", "type": "integer"}],
          original="POST /marketing/discounts")
async def discount_save(c: Ctx):
    await c.dashboard("discounts.manage")
    db, shop, data = await c.db(), c.store, c.input
    code = (data.get("code") or "").strip().upper()
    kind = data.get("kind") if data.get("kind") in ("percentage", "fixed_amount", "free_shipping") else "percentage"
    errors: dict[str, str] = {}
    if not code:
        errors["code"] = "A discount needs a code."
    if kind == "percentage":
        try:
            percent = int(data.get("value") or 0)
        except (TypeError, ValueError):
            percent = 0
        if not 1 <= percent <= 100:
            errors["value"] = "Enter a percentage between 1 and 100."
    existing = await q.first(db, "discounts", {"id": int(data["id"]), "store_id": shop.pk}) if data.get("id") else None
    if code and not errors and await q.exists(db, "discounts", {"store_id": shop.pk, "code": code, "id": q.ne(existing.pk if existing else 0)}):
        errors["code"] = "That code is already in use."
    if errors:
        raise unprocessable("Check the highlighted fields.", "validation_failed", errors)
    value = int(data.get("value") or 0) if kind == "percentage" else to_minor(data.get("value") or 0, shop.currency) if kind == "fixed_amount" else 0
    values: dict[str, Any] = {
        "code": code, "title": (data.get("title") or "").strip() or None, "description": (data.get("description") or "").strip() or None, "kind": kind, "value": value,
        "scope": data.get("scope") if data.get("scope") in ("order", "products", "collections") else "order",
        "scope_ids": [int(i) for i in (data.get("scope_ids") or []) if str(i).isdigit()], "minimum_order_minor": to_minor(data.get("minimum_order") or 0, shop.currency),
        "maximum_discount_minor": to_minor(data["maximum_discount"], shop.currency) if data.get("maximum_discount") else None,
        "usage_limit": _int_or_none(data.get("usage_limit")), "per_customer_limit": _int_or_none(data.get("per_customer_limit")), "starts_at": _date_or_none(data.get("starts_at")),
        "ends_at": _date_or_none(data.get("ends_at")), "is_active": bool(data.get("is_active", True)), "is_automatic": bool(data.get("is_automatic")),
        "segment_id": _int_or_none(data.get("segment_id"))}
    if existing is None:
        discount = await q.insert(db, "discounts", {"store_id": shop.pk, "usage_count": 0, **values})
    else:
        await q.update(db, "discounts", existing.pk, values)
        discount = await q.get(db, "discounts", existing.pk)
    await record_from(c, action="discount.saved", resource_type="discount", resource_id=discount.pk, summary=f"Saved the discount {discount.code}")
    return _discount_prop(discount, shop.currency)


@endpoint("discounts.toggle", "POST", "/dash/{store}/discounts/{discount_id}/toggle", area="marketing", permission="discounts.manage", summary="Enable or disable a discount",
          original="POST /marketing/discounts/{id}/toggle")
async def discount_toggle(c: Ctx):
    await c.dashboard("discounts.manage")
    db = await c.db()
    discount = await q.first(db, "discounts", {"id": c.int_arg("discount_id", required=True), "store_id": c.store.pk})
    if discount is None:
        raise not_found("That discount")
    await q.update(db, "discounts", discount.pk, {"is_active": not discount.is_active})
    return _discount_prop(await q.get(db, "discounts", discount.pk), c.store.currency)


# ── campaigns ────────────────────────────────────────────────────────────

def _campaign_prop(cm: q.Row, currency: str, *, segment: q.Row | None = None, discount: q.Row | None = None) -> dict[str, Any]:
    return {"id": cm.pk, "name": cm.name, "kind": cm.kind, "description": cm.description, "status": cm.status, "segment_id": cm.segment_id,
            "segment": segment.name if segment else None, "discount_id": cm.discount_id, "discount_code": discount.code if discount else None,
            "product_ids": cm.product_ids or [], "starts_at": cm.starts_at, "ends_at": cm.ends_at,
            "budget": Money(cm.budget_minor, currency).as_prop() if cm.budget_minor else None, "spend": Money(cm.spend_minor or 0, currency).as_prop(),
            "revenue": Money(cm.revenue_minor or 0, currency).as_prop(), "roi": Money(cm.roi_minor, currency).as_prop(), "orders_count": cm.orders_count,
            "recipients_count": cm.recipients_count, "recovered_count": cm.recovered_count, "last_run_at": cm.last_run_at}


async def _segment_size(db: Any, store: q.Row, segment_id: int | None) -> int:
    segment = await q.first(db, "segments", {"id": segment_id, "store_id": store.pk}) if segment_id else None
    return await segments.count(db, store.pk, segment) if segment else 0


@endpoint("campaigns.list", "GET", "/dash/{store}/campaigns", area="marketing", permission="campaigns.read", summary="List campaigns with status counts and the pickers' options",
          original="GET /marketing/campaigns")
async def campaigns_list(c: Ctx):
    await c.dashboard("campaigns.read")
    db, store = await c.db(), c.store
    status = c.arg("status", "all")
    where: dict[str, Any] = {"store_id": store.pk}
    if status != "all":
        where["status"] = status
    rows = await q.find(db, "campaigns", where, order="id DESC", limit=200)
    data = []
    for cm in rows:
        data.append(_campaign_prop(cm, store.currency, segment=await q.get(db, "segments", cm.segment_id), discount=await q.get(db, "discounts", cm.discount_id)))
    counts = {k: await q.count(db, "campaigns", {"store_id": store.pk} | ({} if k == "all" else {"status": k})) for k in ("all", "draft", "scheduled", "active", "paused", "completed")}
    return {"data": data, "status": status, "counts": counts, "types": [{"key": k, "label": k.replace("_", " ").title()} for k in CAMPAIGN_TYPES],
            "discounts": [{"id": r.pk, "code": r.code} for r in await q.find(db, "discounts", {"store_id": store.pk}, order="code")], **await _options(db, store.pk)}


@endpoint("campaigns.show", "GET", "/dash/{store}/campaigns/{campaign_id}", area="marketing", permission="campaigns.read",
          summary="One campaign with refreshed results, redemptions and audience size", original="GET /marketing/campaigns/{id}")
async def campaigns_show(c: Ctx):
    await c.dashboard("campaigns.read")
    db, store = await c.db(), c.store
    cm = await q.first(db, "campaigns", {"id": c.int_arg("campaign_id", required=True), "store_id": store.pk})
    if cm is None:
        raise not_found("That campaign")
    await campaigns.refresh_results(db, cm)
    cm = await q.get(db, "campaigns", cm.pk)
    redemptions = []
    if cm.discount_id:
        rows = await db.fetch("SELECT u.*, o.number AS order_number, cu.email AS customer_email FROM discount_usages u LEFT JOIN orders o ON o.id = u.order_id "
                              "LEFT JOIN customers cu ON cu.id = u.customer_id WHERE u.discount_id = ? AND u.deleted_at IS NULL ORDER BY u.id DESC LIMIT 50", [cm.discount_id])
        redemptions = [{"id": r["id"], "order_id": r["order_id"], "order_number": r["order_number"], "customer": r["customer_email"],
                        "amount": Money(r["amount_minor"], store.currency).as_prop(), "created_at": r["created_at"]} for r in rows]
    return {"campaign": _campaign_prop(cm, store.currency, segment=await q.get(db, "segments", cm.segment_id), discount=await q.get(db, "discounts", cm.discount_id)),
            "redemptions": redemptions, "audience_size": await _segment_size(db, store, cm.segment_id) if cm.segment_id else None}


@endpoint("campaigns.save", "POST", "/dash/{store}/campaigns", area="marketing", permission="campaigns.manage", summary="Create or update a campaign",
          fields=[{"name": "id", "type": "integer"}, {"name": "name", "type": "string", "required": True}, {"name": "kind", "type": "string"}, {"name": "description", "type": "string"},
                  {"name": "segment_id", "type": "integer"}, {"name": "discount_id", "type": "integer"}, {"name": "product_ids", "type": "json"}, {"name": "starts_at", "type": "string"},
                  {"name": "ends_at", "type": "string"}, {"name": "budget", "type": "string"}, {"name": "spend", "type": "string"}], original="POST /marketing/campaigns")
async def campaign_save(c: Ctx):
    await c.dashboard("campaigns.manage")
    db, shop, data = await c.db(), c.store, c.input
    name = (data.get("name") or "").strip()
    if not name:
        raise unprocessable("A campaign needs a name.", "validation_failed", {"name": "A campaign needs a name."})
    campaign = await q.first(db, "campaigns", {"id": int(data["id"]), "store_id": shop.pk}) if data.get("id") else None
    values: dict[str, Any] = {
        "name": name, "kind": data.get("kind") if data.get("kind") in CAMPAIGN_TYPES else "discount", "description": (data.get("description") or "").strip() or None,
        "segment_id": _int_or_none(data.get("segment_id")), "discount_id": _int_or_none(data.get("discount_id")),
        "product_ids": [int(i) for i in (data.get("product_ids") or []) if str(i).isdigit()], "starts_at": _date_or_none(data.get("starts_at")),
        "ends_at": _date_or_none(data.get("ends_at")), "budget_minor": to_minor(data["budget"], shop.currency) if data.get("budget") else None,
        "spend_minor": to_minor(data.get("spend") or 0, shop.currency)}
    if campaign is None:
        campaign = await q.insert(db, "campaigns", {"store_id": shop.pk, "status": "draft", "created_by_id": c.user_id, "orders_count": 0, "revenue_minor": 0,
                                                    "recipients_count": 0, "recovered_count": 0, **values})
    else:
        await q.update(db, "campaigns", campaign.pk, values)
        campaign = await q.get(db, "campaigns", campaign.pk)
    # A campaign scheduled to start later becomes `scheduled`, so the hourly job picks it up without the merchant coming back at the right moment.
    if campaign.status == "draft" and campaign.starts_at:
        await q.update(db, "campaigns", campaign.pk, {"status": "scheduled"})
    if not campaign.recipients_count and campaign.segment_id:
        await q.update(db, "campaigns", campaign.pk, {"recipients_count": await _segment_size(db, shop, campaign.segment_id)})
    campaign = await q.get(db, "campaigns", campaign.pk)
    return _campaign_prop(campaign, shop.currency, segment=await q.get(db, "segments", campaign.segment_id), discount=await q.get(db, "discounts", campaign.discount_id))


_MOVES: dict[str, tuple[str, ...]] = {"draft": ("active", "scheduled", "archived"), "scheduled": ("active", "draft", "archived"), "active": ("paused", "completed"),
                                      "paused": ("active", "completed"), "completed": ("archived",), "archived": ()}


@endpoint("campaigns.status", "POST", "/dash/{store}/campaigns/{campaign_id}/status", area="marketing", permission="campaigns.manage",
          summary="Start, pause, resume or finish a campaign (the allowed moves are declared, not inferred)", fields=[{"name": "status", "type": "string", "required": True}],
          original="POST /marketing/campaigns/{id}/status")
async def campaign_status(c: Ctx):
    await c.dashboard("campaigns.manage")
    db = await c.db()
    cm = await q.first(db, "campaigns", {"id": c.int_arg("campaign_id", required=True), "store_id": c.store.pk})
    if cm is None:
        raise not_found("That campaign")
    target = c.input.get("status")
    if target not in _MOVES.get(cm.status, ()):  # "completed -> active" is refused, not quietly resurrected with its attribution window
        raise unprocessable(f"A {cm.status} campaign can't become {target}.", "campaign_state")
    await q.update(db, "campaigns", cm.pk, {"status": target})
    await record_from(c, action="campaign.status", resource_type="campaign", resource_id=cm.pk, summary=f"Set the campaign {cm.name} to {target}")
    return {"id": cm.pk, "status": target, "message": f"{cm.name} is now {target}."}
