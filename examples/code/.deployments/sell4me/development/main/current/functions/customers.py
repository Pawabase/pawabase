"""Customers, segments and abandoned carts.

The customer record is a merchant's asset and the platform's biggest privacy surface: every query is scoped to the store and nothing
exposes a customer of one store to another. A customer is unique on ``(store, email)``: the same person shopping at two merchants is two
records, which is the correct model, not a limitation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import audit, q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import forbidden, not_found, unprocessable
from sell4me_kit.money import Money
from sell4me_kit.services import segments as segment_service

PAGE_SIZE = 25
_SORTS = {"-total_spent_minor": "total_spent_minor DESC", "total_spent_minor": "total_spent_minor", "-orders_count": "orders_count DESC", "orders_count": "orders_count",
          "-last_order_at": "last_order_at DESC", "-created_at": "created_at DESC", "created_at": "created_at"}


def _row(customer: q.Row, currency: str) -> dict[str, Any]:
    return {"id": customer.pk, "email": customer.email, "name": customer.name, "phone": customer.phone, "orders_count": customer.orders_count,
            "total_spent": Money(customer.total_spent_minor or 0, currency).as_prop(), "average_order": Money(customer.average_order_minor, currency).as_prop(),
            "first_order_at": customer.first_order_at, "last_order_at": customer.last_order_at, "accepts_marketing": customer.accepts_marketing,
            "tags": customer.tags or [], "risk_score": customer.risk_score, "created_at": customer.created_at}


def _tags(value: Any) -> list[str]:
    return [str(t).strip() for t in value if str(t).strip()] if isinstance(value, list) else [t.strip() for t in str(value or "").split(",") if t.strip()]


async def _segment_options(db: Any, store_id: int) -> list[dict[str, Any]]:
    return [{"id": s.pk, "name": s.name} for s in await q.find(db, "segments", {"store_id": store_id}, order="name")]


@endpoint("customers.list", "GET", "/dash/{store}/customers", area="customers", permission="customers.read", summary="List customers: search, segment, sort, paging", original="GET /customers")
async def customers_list(c: Ctx):
    await c.dashboard("customers.read")
    db, store = await c.db(), c.store
    search, segment_id, sort = (c.arg("q") or "").strip(), c.arg("segment"), c.arg("sort", "-total_spent_minor")
    page, per_page = c.page_params(PAGE_SIZE)
    where, params = ["store_id = ?", "deleted_at IS NULL"], [store.pk]
    if segment_id:
        segment = await q.first(db, "segments", {"id": int(segment_id), "store_id": store.pk})
        if segment is not None:
            clause, seg_params = segment_service.compile_rules(segment.rules)
            where.append(clause)
            params += seg_params
    if search:
        where.append("(LOWER(email) LIKE ? OR LOWER(COALESCE(first_name, '')) LIKE ? OR LOWER(COALESCE(last_name, '')) LIKE ?)")
        params += [f"%{search.lower()}%"] * 3
    order = _SORTS.get(sort, _SORTS["-total_spent_minor"])
    clause = " AND ".join(where)
    total = int(await db.scalar(f"SELECT COUNT(*) FROM customers WHERE {clause}", params, default=0))
    rows = [q.Row(r) for r in await db.fetch(f"SELECT * FROM customers WHERE {clause} ORDER BY {order} LIMIT {per_page} OFFSET {(page - 1) * per_page}", params)]
    return {"data": [_row(r, store.currency) for r in rows], "search": search, "sort": sort if sort in _SORTS else "-total_spent_minor",
            "segment_id": int(segment_id) if segment_id else None, "segments": await _segment_options(db, store.pk),
            "pagination": {"page": page, "per_page": per_page, "total": total, "pages": max(1, -(-total // per_page))}}


async def _customer(c: Ctx, customer_id: int) -> q.Row:
    customer = await q.first(await c.db(), "customers", {"id": customer_id, "store_id": c.store.pk})
    if customer is None:
        raise not_found("That customer")
    return customer


@endpoint("customers.show", "GET", "/dash/{store}/customers/{customer_id}", area="customers", permission="customers.read",
          summary="One customer: profile, addresses, the segments they are in, order and abandoned-cart history", original="GET /customers/{id}")
async def customers_show(c: Ctx):
    await c.dashboard("customers.read")
    db, store = await c.db(), c.store
    customer = await _customer(c, c.int_arg("customer_id", required=True))
    addresses = await q.find(db, "customer_addresses", {"customer_id": customer.pk})
    # Which segments this customer currently falls into, resolved live: segments are queries, not stored lists.
    segments = [{"id": s.pk, "name": s.name} for s in await q.find(db, "segments", {"store_id": store.pk}, order="name") if await segment_service.contains(db, s.pk, customer)]
    orders = [{"id": o.pk, "number": o.number, "status": o.status, "payment_status": o.payment_status, "fulfilment_status": o.fulfilment_status,
               "total": Money(o.total_minor, store.currency).as_prop(), "created_at": o.created_at}
              for o in await q.find(db, "orders", {"store_id": store.pk, "customer_id": customer.pk}, order="id DESC", limit=50)]
    abandoned = [{"id": r.pk, "value": Money(r.value_minor, r.currency).as_prop(), "item_count": r.item_count, "recovery_status": r.recovery_status, "created_at": r.created_at}
                 for r in await q.find(db, "abandoned_carts", {"store_id": store.pk, "customer_id": customer.pk}, order="id DESC", limit=20)]
    return {"customer": {**_row(customer, store.currency), "note": customer.note,
                         "addresses": [{"id": a.pk, "label": a.label, "line1": a.line1, "line2": a.line2, "city": a.city, "province": a.province,
                                        "postal_code": a.postal_code, "country": a.country, "phone": a.phone, "is_default": a.is_default} for a in addresses]},
            "segments": segments, "orders": orders, "abandoned": abandoned}


@endpoint("customers.update", "PATCH", "/dash/{store}/customers/{customer_id}", area="customers", permission="customers.update", summary="Edit a customer",
          fields=[{"name": "first_name", "type": "string"}, {"name": "last_name", "type": "string"}, {"name": "phone", "type": "string"}, {"name": "note", "type": "text"},
                  {"name": "accepts_marketing", "type": "boolean"}, {"name": "tags", "type": "json"}], original="POST /customers/{id}")
async def customers_update(c: Ctx):
    await c.dashboard("customers.update")
    db = await c.db()
    customer = await _customer(c, c.int_arg("customer_id", required=True))
    before = {k: customer.get(k) for k in ("first_name", "last_name", "phone", "accepts_marketing", "tags")}
    changes: dict[str, Any] = {}
    for field in ("first_name", "last_name", "phone", "note"):
        if field in c.input:
            changes[field] = (c.input[field] or "").strip() or None
    if "accepts_marketing" in c.input:
        changes["accepts_marketing"] = bool(c.input["accepts_marketing"])
    if "tags" in c.input:
        changes["tags"] = _tags(c.input["tags"])
    if changes:
        await q.update(db, "customers", customer.pk, changes)
    after = await q.get(db, "customers", customer.pk)
    await audit.record_from(c, action="customer.updated", resource_type="customer", resource_id=customer.pk, summary=f"Updated the customer {customer.email}",
                            changes=audit.changes_between(before, {k: after.get(k) for k in before}))
    return _row(after, c.store.currency)


# ── segments ─────────────────────────────────────────────────────────────

@endpoint("segments.list", "GET", "/dash/{store}/segments", area="customers", permission="customers.read", summary="Segments with live counts, and the rule builder's fields and operators",
          original="GET /customers/segments")
async def segments_list(c: Ctx):
    await c.dashboard("customers.read")
    db, store = await c.db(), c.store
    out = [{"id": s.pk, "name": s.name, "description": s.description, "rules": s.rules or [], "is_system": s.is_system,
            "count": await segment_service.count(db, store.pk, s), "counted_at": s.counted_at} for s in await q.find(db, "segments", {"store_id": store.pk}, order="name")]
    return {"data": out, "fields": [{"key": k, "label": label, "kind": kind} for k, (label, kind) in segment_service.FIELDS.items()],
            "operators": [{"key": k, "label": label} for k, (label, _) in segment_service.OPERATORS.items()]}


@endpoint("segments.members", "GET", "/dash/{store}/segments/{segment_id}/customers", area="customers", permission="customers.read", summary="The customers in one segment (resolved live)",
          original="GET /customers?segment=")
async def segment_members(c: Ctx):
    await c.dashboard("customers.read")
    db, store = await c.db(), c.store
    segment = await q.first(db, "segments", {"id": c.int_arg("segment_id", required=True), "store_id": store.pk})
    if segment is None:
        raise not_found("That segment")
    page, per_page = c.page_params(PAGE_SIZE)
    return {"data": [_row(r, store.currency) for r in await segment_service.members(db, store.pk, segment, limit=per_page, offset=(page - 1) * per_page)],
            "total": await segment_service.count(db, store.pk, segment), "page": page, "per_page": per_page}


@endpoint("segments.save", "POST", "/dash/{store}/segments", area="customers", permission="customers.update", summary="Create or update a segment (built-in segments are read-only)",
          fields=[{"name": "id", "type": "integer"}, {"name": "name", "type": "string", "required": True}, {"name": "description", "type": "string"}, {"name": "rules", "type": "json"}],
          original="POST /customers/segments")
async def segment_save(c: Ctx):
    await c.dashboard("customers.update")
    db, shop = await c.db(), c.store
    name = (c.input.get("name") or "").strip()
    if not name:
        raise unprocessable("A segment needs a name.", "validation_failed", {"name": "A segment needs a name."})
    segment = await q.first(db, "segments", {"id": int(c.input["id"]), "store_id": shop.pk}) if c.input.get("id") else None
    if segment is not None and segment.is_system:  # a campaign that says "New customers" must always mean the same thing
        raise forbidden("Built-in segments can't be edited.", "system_segment")
    values = {"name": name, "description": (c.input.get("description") or "").strip() or None, "rules": c.input.get("rules") if isinstance(c.input.get("rules"), list) else []}
    if segment is None:
        segment = await q.insert(db, "segments", {"store_id": shop.pk, "is_system": False, "cached_count": 0, **values})
    else:
        await q.update(db, "segments", segment.pk, values)
        segment = await q.get(db, "segments", segment.pk)
    return dict(segment)


@endpoint("segments.delete", "DELETE", "/dash/{store}/segments/{segment_id}", area="customers", permission="customers.update", summary="Delete a segment (built-in segments cannot be deleted)",
          original="POST /customers/segments/{id}/delete")
async def segment_delete(c: Ctx):
    await c.dashboard("customers.update")
    db = await c.db()
    segment = await q.first(db, "segments", {"id": c.int_arg("segment_id", required=True), "store_id": c.store.pk})
    if segment is None:
        raise not_found("That segment")
    if segment.is_system:
        raise forbidden("Built-in segments can't be deleted.", "system_segment")
    await q.soft_delete(db, "segments", segment.pk)
    return {"deleted": True}


# ── abandoned carts ──────────────────────────────────────────────────────

@endpoint("abandoned.list", "GET", "/dash/{store}/abandoned-carts", area="customers", permission="customers.read", summary="Abandoned carts with recovery links and status counts",
          original="GET /customers/abandoned")
async def abandoned_list(c: Ctx):
    await c.dashboard("customers.read")
    db, store = await c.db(), c.store
    status = c.arg("status", "all")
    where: dict[str, Any] = {"store_id": store.pk}
    if status != "all":
        where["recovery_status"] = status
    carts = []
    for record in await q.find(db, "abandoned_carts", where, order="id DESC", limit=200):
        items = await db.fetch("SELECT p.title AS title, v.title AS variant, ci.quantity AS quantity FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id "
                               "JOIN products p ON p.id = v.product_id WHERE ci.cart_id = ? ORDER BY ci.id LIMIT 5", [record.cart_id])
        customer = await q.get(db, "customers", record.customer_id)
        carts.append({"id": record.pk, "email": record.email, "customer_id": record.customer_id, "customer": customer.name if customer else None,
                      "value": Money(record.value_minor, record.currency).as_prop(), "item_count": record.item_count, "recovery_status": record.recovery_status,
                      "recovery_url": f"/cart/recover/{record.recovery_token}", "recovery_token": record.recovery_token, "items": [dict(i) for i in items],
                      "notified_at": record.notified_at, "created_at": record.created_at})
    counts = {key: await q.count(db, "abandoned_carts", {"store_id": store.pk} | ({} if key == "all" else {"recovery_status": key})) for key in ("all", "pending", "notified", "recovered", "expired")}
    recovered = await q.total(db, "abandoned_carts", "value_minor", {"store_id": store.pk, "recovery_status": "recovered"})
    return {"data": carts, "status": status, "counts": counts, "recovered_value": Money(recovered, store.currency).as_prop()}


@endpoint("abandoned.notify", "POST", "/dash/{store}/abandoned-carts/{record_id}/notify", area="customers", permission="customers.update",
          summary="Send the recovery email now (and mark the cart followed up)", original="POST /customers/abandoned/{id}/notify")
async def abandoned_notify(c: Ctx):
    """The original only recorded the intent (it had no mail transport wired in); here the same recovery email the abandonment job sends goes out
    immediately, and the cart is marked ``notified``."""
    await c.dashboard("customers.update")
    db, store = await c.db(), c.store
    record = await q.first(db, "abandoned_carts", {"id": c.int_arg("record_id", required=True), "store_id": store.pk})
    if record is None:
        raise not_found("That abandoned cart")
    if record.email and record.recovery_status == "pending":
        await c.dispatch("carts.recover_email", {"record_id": record.pk})
    else:
        await q.update(db, "abandoned_carts", record.pk, {"recovery_status": "notified", "notified_at": datetime.now(UTC)})
    return {"id": record.pk, "recovery_status": "notified", "recovery_url": f"/cart/recover/{record.recovery_token}"}
