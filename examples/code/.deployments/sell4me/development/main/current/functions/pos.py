"""Point of sale: devices, cashier sessions, sales (cash or card), product and customer lookup, end-of-day reports.

The terminal's cart lives in the browser; the order is built server-side from the line items it submits, and prices are always re-read from the
variants: a client is never trusted for a price. Cash bypasses the provider but goes through the same ``mark_paid`` / ledger path as a card.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.audit import record_from
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import not_found, unprocessable
from sell4me_kit.money import Money
from sell4me_kit.services import payments as payment_service
from sell4me_kit.services import pos as pos_service
from sell4me_kit.services.dns import lookup_txt
from sell4me_kit.urls import storefront_url

PAGE_SIZE = 30


def _device_prop(d: q.Row) -> dict[str, Any]:
    return {"id": d.pk, "label": d.label, "token": d.token, "pos_domain": d.pos_domain, "pos_domain_status": d.pos_domain_status or ("pending" if d.pos_domain else None),
            "pos_domain_verification_token": d.pos_domain_verification_token, "pos_domain_verified_at": d.pos_domain_verified_at,
            "pos_domain_last_checked_at": d.pos_domain_last_checked_at, "pos_domain_check_message": d.pos_domain_check_message, "receipt_header": d.receipt_header,
            "receipt_footer": d.receipt_footer, "print_receipt_auto": d.print_receipt_auto, "is_active": d.is_active}


def _session_prop(s: q.Row, currency: str, device: q.Row | None = None, opener: str | None = None) -> dict[str, Any]:
    return {"id": s.pk, "status": s.status, "device_id": s.device_id, "device_label": device.label if device else None, "opened_by": opener or s.opened_by_id,
            "opening_float": Money(s.opening_float_minor or 0, currency).as_prop(), "opened_at": s.opened_at, "closed_at": s.closed_at,
            "total_sales": Money(s.total_sales_minor or 0, currency).as_prop(), "order_count": s.order_count or 0, "variance_minor": s.variance_minor}


async def _sessions_out(db: Any, rows: list[q.Row], currency: str) -> list[dict[str, Any]]:
    devices = {d.pk: d for d in await q.find(db, "pos_devices", {"store_id": rows[0].store_id})} if rows else {}
    return [_session_prop(s, currency, devices.get(s.device_id)) for s in rows]


def _order_row(o: q.Row, currency: str, customer: q.Row | None) -> dict[str, Any]:
    return {"id": o.pk, "number": o.number, "status": o.status, "payment_status": o.payment_status, "total": Money(o.total_minor, currency).as_prop(),
            "customer_name": (customer.name if customer else "Walk-in"), "customer_email": customer.email if customer else None, "placed_at": o.placed_at}


async def _orders_out(db: Any, rows: list[q.Row], currency: str) -> list[dict[str, Any]]:
    ids = {o.customer_id for o in rows if o.customer_id}
    customers = {u.pk: u for u in await q.find(db, "customers", {"id": q.in_(list(ids))})} if ids else {}
    return [_order_row(o, currency, customers.get(o.customer_id)) for o in rows]


def _variant_prop(v: Any, currency: str, thumb: str | None) -> dict[str, Any]:
    return {"id": v["id"], "product_id": v["product_id"], "product_title": v["product_title"], "variant_title": v["title"], "sku": v["sku"], "barcode": v["barcode"],
            "price": Money(v["price_minor"], currency).as_prop(), "price_minor": v["price_minor"], "stock": v["stock"], "track_inventory": v["track_inventory"], "image_url": thumb}


async def _thumbs(db: Any, product_ids: list[int]) -> dict[int, str]:
    if not product_ids:
        return {}
    rows = await db.fetch(f"SELECT product_id, url FROM product_images WHERE deleted_at IS NULL AND product_id IN ({', '.join('?' for _ in product_ids)}) ORDER BY position DESC, id DESC", product_ids)
    return {int(r["product_id"]): r["url"] for r in rows}  # DESC order, so the lowest position wins the overwrite


# ── hub and terminal ─────────────────────────────────────────────────────

@endpoint("pos.index", "GET", "/dash/{store}/pos", area="pos", permission="pos.read", summary="The POS hub: the open session, recent POS sales, devices", original="GET /pos")
async def pos_index(c: Ctx):
    await c.dashboard("pos.read")
    db, store = await c.db(), c.store
    open_session = await q.first(db, "pos_sessions", {"store_id": store.pk, "status": "open"}, order="id DESC")
    recent = await q.find(db, "orders", {"store_id": store.pk, "source": "pos"}, order="id DESC", limit=PAGE_SIZE)
    devices = await q.find(db, "pos_devices", {"store_id": store.pk, "is_active": True}, order="id")
    return {"open_session": (await _sessions_out(db, [open_session], store.currency))[0] if open_session else None, "recent_orders": await _orders_out(db, recent, store.currency),
            "devices": [_device_prop(d) for d in devices]}


@endpoint("pos.terminal", "GET", "/dash/{store}/pos/terminal", area="pos", permission="pos.create",
          summary="Everything the full-screen terminal needs: the quick grid, manual discounts, the open session, the card public key, and (with ?device=) the device",
          original="GET /pos/terminal, GET /pos/terminal/{token}")
async def pos_terminal(c: Ctx):
    await c.dashboard("pos.create")
    db, store = await c.db(), c.store
    device = await pos_service.device_for_token(db, store, c.arg("device")) if c.arg("device") else None
    where: dict[str, Any] = {"store_id": store.pk, "status": "open"}
    if device:
        where["device_id"] = device.pk
    open_session = await q.first(db, "pos_sessions", where)
    variants = await db.fetch(
        "SELECT v.*, p.title AS product_title FROM product_variants v JOIN products p ON p.id = v.product_id WHERE v.store_id = ? AND v.deleted_at IS NULL AND p.deleted_at IS NULL "
        "AND p.status = 'active' ORDER BY p.view_count DESC, p.title LIMIT 400", [store.pk])
    thumbs = await _thumbs(db, sorted({int(v["product_id"]) for v in variants}))
    public_key = None
    try:
        _, creds, _ = await payment_service.credentials_for(c, store, "paystack")
        public_key = creds.public_key
    except Exception:  # noqa: BLE001 — no card provider connected: the terminal simply offers cash
        pass
    discounts = [d for d in await q.find(db, "discounts", {"store_id": store.pk, "is_active": True, "scope": "order", "kind": q.in_(["percentage", "fixed_amount"])}, order="title, code")
                 if d.state == "active"]
    return {"store": {"name": store.name, "currency": store.currency, "slug": store.slug}, "device": _device_prop(device) if device else None,
            "open_session": (await _sessions_out(db, [open_session], store.currency))[0] if open_session else None,
            "products": [_variant_prop(v, store.currency, thumbs.get(int(v["product_id"]))) for v in variants], "paystack_public_key": public_key,
            "discounts": [{"id": d.pk, "code": d.code, "title": d.title or d.code, "kind": d.kind, "value": d.value, "minimum_order_minor": d.minimum_order_minor,
                           "maximum_discount_minor": d.maximum_discount_minor} for d in discounts]}


@endpoint("pos.product_search", "GET", "/dash/{store}/pos/search", area="pos", permission="pos.create", summary="Terminal product search by title, SKU or barcode", original="GET /pos/search")
async def product_search(c: Ctx):
    await c.dashboard("pos.create")
    db, store = await c.db(), c.store
    term = (c.arg("q") or "").strip().lower()
    where, params = "v.store_id = ? AND v.deleted_at IS NULL AND p.deleted_at IS NULL AND p.status = 'active'", [store.pk]
    if term:
        where += " AND (LOWER(p.title) LIKE ? OR LOWER(COALESCE(v.sku,'')) LIKE ? OR LOWER(COALESCE(v.barcode,'')) LIKE ?)"
        params += [f"%{term}%"] * 3
    rows = await db.fetch(f"SELECT v.*, p.title AS product_title FROM product_variants v JOIN products p ON p.id = v.product_id WHERE {where} LIMIT 40", params)
    thumbs = await _thumbs(db, sorted({int(v["product_id"]) for v in rows}))
    return {"results": [_variant_prop(v, store.currency, thumbs.get(int(v["product_id"]))) for v in rows]}


@endpoint("pos.customer_search", "GET", "/dash/{store}/pos/customer-search", area="pos", permission="pos.create", summary="Terminal customer lookup by email", original="GET /pos/customer-search")
async def customer_search(c: Ctx):
    await c.dashboard("pos.create")
    term = (c.arg("q") or "").strip().lower()
    if len(term) < 2:
        return {"results": []}
    rows = await (await c.db()).fetch("SELECT * FROM customers WHERE store_id = ? AND deleted_at IS NULL AND LOWER(email) LIKE ? LIMIT 15", [c.store.pk, f"%{term}%"])
    return {"results": [{"id": r.pk, "name": r.name, "email": r.email, "phone": r.phone} for r in map(q.Row, rows)]}


# ── sales ────────────────────────────────────────────────────────────────

@endpoint("pos.sale", "POST", "/dash/{store}/pos/sale", area="pos", permission="pos.create",
          summary="Create a POS order and (for cash) record the payment in one step; for card, creates the pending order that /pos/sale/card/start then charges",
          fields=[{"name": "items", "type": "json", "required": True}, {"name": "payment_method", "type": "string"}, {"name": "amount_tendered", "type": "integer"},
                  {"name": "customer_id", "type": "integer"}, {"name": "customer_email", "type": "string"}, {"name": "discount_minor", "type": "integer"},
                  {"name": "note", "type": "string"}, {"name": "session_id", "type": "integer"}], original="POST /pos/sale")
async def pos_sale(c: Ctx):
    await c.dashboard("pos.create")
    db, store, data = await c.db(), c.store, c.input
    items = data.get("items") or []
    if not items:
        raise unprocessable("No items in the sale.", "validation_failed", {"items": "No items in the sale."})
    method = str(data.get("payment_method") or "cash").lower()
    session = await q.first(db, "pos_sessions", {"id": int(data["session_id"]), "store_id": store.pk, "status": "open"}) if data.get("session_id") else None
    try:
        order = await pos_service.create_pos_order(c, store=store, member=c.member, session=session, line_items=items, customer_id=int(data["customer_id"]) if data.get("customer_id") else None,
                                                   customer_email=(data.get("customer_email") or "").strip() or None, note=(data.get("note") or "").strip() or None,
                                                   discount_minor=int(data.get("discount_minor") or 0))
        change = 0
        if method == "cash":
            tendered = int(data.get("amount_tendered") or order.total_minor)
            await pos_service.record_cash_payment(c, store=store, order=order, amount_tendered_minor=tendered, actor_id=c.user_id)
            change = tendered - order.total_minor
    except pos_service.PosError as error:
        raise unprocessable(str(error), "pos_error") from None
    await record_from(c, action="pos.sale.created", resource_type="order", resource_id=order.pk, summary=f"POS sale #{order.number} · {Money(order.total_minor, store.currency).format()}")
    return {"ok": True, "order_id": order.pk, "order_number": order.number, "total_minor": order.total_minor, "change_minor": change, "currency": store.currency}


@endpoint("pos.card_start", "POST", "/dash/{store}/pos/sale/card/start", area="pos", permission="pos.create", summary="Start a card charge for a pending POS order (returns the provider's payment URL)",
          fields=[{"name": "order_id", "type": "integer", "required": True}, {"name": "return_url", "type": "string"}], original="POST /pos/sale/card/start")
async def card_start(c: Ctx):
    await c.dashboard("pos.create")
    db, store = await c.db(), c.store
    order = await q.first(db, "orders", {"id": int(c.input.get("order_id") or 0), "store_id": store.pk, "source": "pos"})
    if order is None or order.payment_status == "paid":
        raise unprocessable("Order not found or already paid.", "validation_failed")
    back = str(c.input.get("return_url") or "")
    return_url = back if back.startswith(("http://", "https://")) else storefront_url(await c.settings(), store, f"/pos/terminal?order_paid={order.pk}", preview_link=True)
    try:
        result = await payment_service.start_payment(c, store=store, order=order, provider_key="paystack", return_url=return_url, cancel_url=return_url,
                                                     client_ip=c.client_ip, user_agent=c.headers.get("user-agent"))
    except payment_service.PaymentError as error:
        raise unprocessable(str(error), "payment_error") from None
    return {"authorization_url": result.redirect_url, "reference": result.provider_reference}


# ── sessions ─────────────────────────────────────────────────────────────

@endpoint("pos.sessions", "GET", "/dash/{store}/pos/sessions", area="pos", permission="pos.read", summary="Cashier sessions, most recent first", original="GET /pos/sessions")
async def sessions_list(c: Ctx):
    await c.dashboard("pos.read")
    db, store = await c.db(), c.store
    page, per_page = c.page_params(PAGE_SIZE)
    total = await q.count(db, "pos_sessions", {"store_id": store.pk})
    rows = await q.find(db, "pos_sessions", {"store_id": store.pk}, order="id DESC", limit=per_page, offset=(page - 1) * per_page)
    return {"data": await _sessions_out(db, rows, store.currency), "page": page, "per_page": per_page, "total": total, "pages": max(1, -(-total // per_page))}


@endpoint("pos.session_open", "POST", "/dash/{store}/pos/sessions/open", area="pos", permission="pos.create", summary="Open a cashier session (a device cannot have two open)",
          fields=[{"name": "device_id", "type": "integer"}, {"name": "opening_float", "type": "integer"}], original="POST /pos/sessions/open")
async def session_open(c: Ctx):
    await c.dashboard("pos.create")
    db, store = await c.db(), c.store
    device = await q.first(db, "pos_devices", {"id": int(c.input["device_id"]), "store_id": store.pk}) if c.input.get("device_id") else None
    float_minor = int(c.input.get("opening_float") or 0)
    try:
        session = await pos_service.open_session(c, store=store, member=c.member, device=device, opening_float_minor=float_minor)
    except pos_service.PosError as error:
        raise unprocessable(str(error), "pos_error") from None
    await record_from(c, action="pos.session.opened", resource_type="pos_session", resource_id=session.pk, summary=f"POS session opened · float {Money(float_minor, store.currency).format()}")
    return (await _sessions_out(db, [session], store.currency))[0]


async def _session(c: Ctx) -> q.Row:
    session = await q.first(await c.db(), "pos_sessions", {"id": c.int_arg("session_id", required=True), "store_id": c.store.pk})
    if session is None:
        raise not_found("That session")
    return session


@endpoint("pos.session_close", "POST", "/dash/{store}/pos/sessions/{session_id}/close", area="pos", permission="pos.create",
          summary="Close a session: expected cash, variance and per-variant totals are computed from every order in it", fields=[{"name": "closing_count", "type": "integer"}, {"name": "note", "type": "string"}],
          original="POST /pos/sessions/{id}/close")
async def session_close(c: Ctx):
    await c.dashboard("pos.create")
    db, store = await c.db(), c.store
    session = await _session(c)
    try:
        session = await pos_service.close_session(c, session=session, member=c.member, closing_count_minor=int(c.input.get("closing_count") or 0), note=(c.input.get("note") or "").strip() or None)
    except pos_service.PosError as error:
        raise unprocessable(str(error), "pos_error") from None
    await record_from(c, action="pos.session.closed", resource_type="pos_session", resource_id=session.pk,
                      summary=f"POS session closed · variance {Money(session.variance_minor or 0, store.currency).format()}")
    return pos_service.session_summary(session, store.currency)


@endpoint("pos.session_show", "GET", "/dash/{store}/pos/sessions/{session_id}", area="pos", permission="pos.read", summary="End-of-day report for one session", original="GET /pos/sessions/{id}")
async def session_show(c: Ctx):
    await c.dashboard("pos.read")
    db, store = await c.db(), c.store
    session = await _session(c)
    items = await q.find(db, "pos_session_items", {"session_id": session.pk})
    orders = await q.find(db, "orders", {"pos_session_id": session.pk})
    cur = store.currency
    return {"session": pos_service.session_summary(session, cur),
            "items": [{"variant_id": i.variant_id, "product_title": i.product_title, "variant_title": i.variant_title, "sku": i.sku, "quantity_sold": i.quantity_sold,
                       "quantity_refunded": i.quantity_refunded, "gross": Money(i.gross_minor, cur).as_prop(), "refunded": Money(i.refunded_minor, cur).as_prop()} for i in items],
            "orders": await _orders_out(db, orders, cur)}


# ── devices and config ───────────────────────────────────────────────────

async def _domain_conflict(c: Ctx, domain: str, *, exclude_pk: int | None = None) -> bool:
    """Whether a hostname is already claimed platform-wide: a hostname can serve only one surface, so this is global, not store-scoped.
    Refused: anything shaped like a storefront subdomain, another active device's domain, and any storefront domain row."""
    db = await c.db()
    hostname = domain.split(":", 1)[0]
    suffix = (await c.settings()).storefront_suffix.lower()
    if hostname.endswith(f".{suffix}") or hostname.endswith(f".{suffix.split(':', 1)[0]}"):
        return True
    where: dict[str, Any] = {"pos_domain": domain, "is_active": True}
    if exclude_pk is not None:
        where["id"] = q.ne(exclude_pk)
    return await q.exists(db, "pos_devices", where) or await q.exists(db, "domains", {"hostname": domain})


@endpoint("pos.config", "GET", "/dash/{store}/pos/config", area="pos", permission="pos.manage", summary="POS configuration: devices, receipt settings, custom domains", original="GET /pos/config")
async def pos_config(c: Ctx):
    await c.dashboard("pos.manage")
    devices = await q.find(await c.db(), "pos_devices", {"store_id": c.store.pk}, order="id")
    return {"devices": [_device_prop(d) for d in devices], "store_slug": c.store.slug, "instructions": {"record_type": "TXT", "host": "_commerce-verify"}}


@endpoint("pos.device_save", "POST", "/dash/{store}/pos/config/device", area="pos", permission="pos.manage", summary="Create or update a POS device",
          fields=[{"name": "device_id", "type": "integer"}, {"name": "label", "type": "string", "required": True}, {"name": "pos_domain", "type": "string"},
                  {"name": "receipt_header", "type": "string"}, {"name": "receipt_footer", "type": "string"}, {"name": "print_receipt_auto", "type": "boolean"}],
          original="POST /pos/config/device")
async def device_save(c: Ctx):
    await c.dashboard("pos.manage")
    db, store, data = await c.db(), c.store, c.input
    label = (data.get("label") or "").strip()
    if not label:
        raise unprocessable("A label is required.", "validation_failed", {"label": "A label is required."})
    device = None
    if data.get("device_id"):
        device = await q.first(db, "pos_devices", {"id": int(data["device_id"]), "store_id": store.pk})
        if device is None:
            raise not_found("That device")
    # The stored value is exactly what the browser will send as Host, because dispatch matches on it.
    domain = pos_service.normalize_pos_domain(data.get("pos_domain")) or None
    if domain and await _domain_conflict(c, domain, exclude_pk=device.pk if device else None):
        raise unprocessable(f"'{domain}' is already in use on this platform.", "validation_failed", {"pos_domain": f"'{domain}' is already in use on this platform."})
    if device is None:
        device = await pos_service.create_device(db, store, label, pos_domain=domain)
        extra = {k: (data.get(k) or "").strip() or None for k in ("receipt_header", "receipt_footer")} | {"print_receipt_auto": bool(data.get("print_receipt_auto"))}
        await q.update(db, "pos_devices", device.pk, extra)
    else:
        changes: dict[str, Any] = {"label": label, "pos_domain": domain, "receipt_header": (data.get("receipt_header") or "").strip() or None,
                                   "receipt_footer": (data.get("receipt_footer") or "").strip() or None, "print_receipt_auto": bool(data.get("print_receipt_auto"))}
        if device.pos_domain != domain:  # a changed domain is unverified again, with a fresh token
            changes |= {"pos_domain_status": "pending" if domain else None, "pos_domain_verification_token": secrets.token_urlsafe(24) if domain else None,
                        "pos_domain_verified_at": None, "pos_domain_check_message": None, "pos_domain_last_checked_at": None}
        await q.update(db, "pos_devices", device.pk, changes)
    await record_from(c, action="pos.device.saved", resource_type="pos_device", resource_id=device.pk, summary=f"POS device '{label}' saved.")
    return _device_prop(await q.get(db, "pos_devices", device.pk))


@endpoint("pos.device_verify", "POST", "/dash/{store}/pos/config/device/{device_id}/verify", area="pos", permission="pos.manage",
          summary="Check a POS device domain's ownership TXT record", original="POST /pos/config/device/{id}/verify")
async def device_verify(c: Ctx):
    await c.dashboard("pos.manage")
    db = await c.db()
    device = await q.first(db, "pos_devices", {"id": c.int_arg("device_id", required=True), "store_id": c.store.pk})
    if device is None or not device.pos_domain:
        raise not_found("That device domain")
    now = datetime.now(UTC)
    records = await lookup_txt(f"_commerce-verify.{device.pos_domain}")
    if records is None:
        message, changes = "Could not query DNS from this server. Check again in a moment.", {"pos_domain_status": "pending"}
    elif device.pos_domain_verification_token and device.pos_domain_verification_token in records:
        message, changes = None, {"pos_domain_status": "verified", "pos_domain_verified_at": now}
        await record_from(c, action="pos.device.verified", resource_type="pos_device", resource_id=device.pk, summary=f"Verified the POS domain {device.pos_domain}")
    else:
        message, changes = "The TXT record wasn't found. DNS can take a while to propagate.", {"pos_domain_status": "pending"}
    await q.update(db, "pos_devices", device.pk, {**changes, "pos_domain_last_checked_at": now, "pos_domain_check_message": message})
    return {"verified": changes["pos_domain_status"] == "verified", "message": message or f"{device.pos_domain} is verified."}


@endpoint("pos.device_delete", "DELETE", "/dash/{store}/pos/config/device/{device_id}", area="pos", permission="pos.manage", summary="Remove a device (marks it inactive)",
          original="POST /pos/config/device/{id}/delete")
async def device_delete(c: Ctx):
    await c.dashboard("pos.manage")
    db = await c.db()
    device = await q.first(db, "pos_devices", {"id": c.int_arg("device_id", required=True), "store_id": c.store.pk})
    if device is None:
        raise not_found("That device")
    await q.update(db, "pos_devices", device.pk, {"is_active": False})
    return {"deleted": True}
