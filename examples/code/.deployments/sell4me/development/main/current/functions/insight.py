"""The overview, analytics, notifications, global search and exports: five sections that are all about *reading*.

The original deferred the heavy aggregates so the shell could paint first. Over an API each aggregate is its own endpoint instead, so a client
fetches the headline numbers and the series in parallel and draws whichever arrives first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import forbidden, not_found, unprocessable
from sell4me_kit.money import SUPPORTED_CURRENCIES, Money
from sell4me_kit.services import analytics, exports, onboarding


def _window(c: Ctx) -> tuple[datetime, datetime, dict[str, str]]:
    key = c.arg("range", "30d")
    since, until, label = analytics.range_bounds(key, start=c.arg("start"), end=c.arg("end"))
    return since, until, {"key": key, "label": label}


# ── overview ─────────────────────────────────────────────────────────────

@endpoint("dashboard.overview", "GET", "/dash/{store}/overview", area="dashboard", permission="analytics.read",
          summary="The morning screen: store, period, what needs attention, onboarding, recent orders, headline numbers, series, top products", original="GET /")
async def overview(c: Ctx):
    await c.dashboard("analytics.read")
    db, store = await c.db(), c.store
    since, until, window = _window(c)
    attention = {
        "unfulfilled": await q.count(db, "orders", {"store_id": store.pk, "payment_status": "paid", "fulfilment_status": "unfulfilled"}),
        "pending_payment": await q.count(db, "orders", {"store_id": store.pk, "status": "pending"}),
        "failed_payments": await q.count(db, "payments", {"store_id": store.pk, "status": "failed"}),
        "low_stock": await q.count(db, "product_variants", {"store_id": store.pk, "track_inventory": True, "stock": q.lte(5)}),
        "flagged": int(await db.scalar("SELECT COUNT(*) FROM orders WHERE store_id = ? AND deleted_at IS NULL AND risk_score >= 40 AND status NOT IN ('cancelled','refunded')", [store.pk], default=0)),
    }
    recent = await db.fetch("SELECT o.*, c.first_name AS cf, c.last_name AS cl FROM orders o LEFT JOIN customers c ON c.id = o.customer_id "
                            "WHERE o.store_id = ? AND o.deleted_at IS NULL ORDER BY o.id DESC LIMIT 8", [store.pk])
    return {"range": window, "onboarding": await onboarding.progress(c, store), "currency": store.currency, "currency_unsupported": store.currency not in SUPPORTED_CURRENCIES,
            "attention": attention,
            "recent_orders": [{"id": o["id"], "number": o["number"], "email": o["email"], "customer": " ".join(p for p in (o["cf"], o["cl"]) if p) or None, "status": o["status"],
                               "payment_status": o["payment_status"], "fulfilment_status": o["fulfilment_status"], "total": Money(o["total_minor"], store.currency).as_prop(),
                               "risk_score": o["risk_score"], "created_at": o["created_at"]} for o in recent],
            "summary": await analytics.overview(db, store=store, since=since, until=until),
            "series": await analytics.series(db, store=store, since=since, until=until),
            "top_products": await analytics.product_performance(db, store=store, since=since, until=until, limit=5)}


# ── analytics ────────────────────────────────────────────────────────────

@endpoint("analytics.overview", "GET", "/dash/{store}/analytics", area="analytics", permission="analytics.read", summary="Headline numbers, the series and top products for a window",
          original="GET /analytics")
async def analytics_overview(c: Ctx):
    await c.dashboard("analytics.read")
    db, store = await c.db(), c.store
    since, until, window = _window(c)
    return {"range": window, "summary": await analytics.overview(db, store=store, since=since, until=until), "series": await analytics.series(db, store=store, since=since, until=until),
            "top_products": await analytics.product_performance(db, store=store, since=since, until=until, limit=10)}


@endpoint("analytics.series", "GET", "/dash/{store}/analytics/series", area="analytics", permission="analytics.read", summary="One metric's daily series (revenue, orders, ...)",
          original="(the deferred `series` group)")
async def analytics_series(c: Ctx):
    await c.dashboard("analytics.read")
    since, until, window = _window(c)
    return {"range": window, "series": await analytics.series(await c.db(), store=c.store, since=since, until=until, metric=c.arg("metric", "revenue"))}


@endpoint("analytics.products", "GET", "/dash/{store}/analytics/products", area="analytics", permission="analytics.read", summary="Product performance for a window", original="GET /analytics/products")
async def analytics_products(c: Ctx):
    await c.dashboard("analytics.read")
    since, until, window = _window(c)
    return {"range": window, "products": await analytics.product_performance(await c.db(), store=c.store, since=since, until=until, limit=100)}


@endpoint("analytics.customers", "GET", "/dash/{store}/analytics/customers", area="analytics", permission="analytics.read",
          summary="Who is buying and whether they come back. Repeat rate is computed over the *window*: over all time it would flatter every store", original="GET /analytics/customers")
async def analytics_customers(c: Ctx):
    await c.dashboard("analytics.read")
    db, store = await c.db(), c.store
    since, until, window = _window(c)
    paid = await db.fetch("SELECT customer_id FROM orders WHERE store_id = ? AND deleted_at IS NULL AND payment_status IN ('paid','partially_refunded') AND paid_at >= ? AND paid_at < ?",
                          [store.pk, since, until])
    ids = [r["customer_id"] for r in paid if r["customer_id"]]
    unique = set(ids)
    repeat = {cid for cid in unique if ids.count(cid) > 1}
    top = await db.fetch("SELECT id, email, first_name, last_name, orders_count, total_spent_minor FROM customers WHERE store_id = ? AND deleted_at IS NULL "
                         "ORDER BY total_spent_minor DESC LIMIT 10", [store.pk])
    return {"range": window, "summary": await analytics.overview(db, store=store, since=since, until=until), "series": await analytics.series(db, store=store, since=since, until=until),
            "cohort": {"buyers": len(unique), "repeat_buyers": len(repeat), "repeat_rate": round(len(repeat) / len(unique), 4) if unique else None,
                       "new_customers": int(await db.scalar("SELECT COUNT(*) FROM customers WHERE store_id = ? AND deleted_at IS NULL AND created_at >= ? AND created_at < ?",
                                                            [store.pk, since, until], default=0)),
                       "guest_orders": sum(1 for r in paid if not r["customer_id"]),
                       "top": [{"id": r["id"], "email": r["email"], "name": " ".join(p for p in (r["first_name"], r["last_name"]) if p) or r["email"], "orders_count": r["orders_count"],
                                "total_spent": Money(r["total_spent_minor"] or 0, store.currency).as_prop()} for r in top]}}


@endpoint("analytics.sales", "GET", "/dash/{store}/analytics/sales", area="analytics", permission="analytics.read", summary="Sales broken down by source, status and payment provider",
          original="GET /analytics/sales")
async def analytics_sales(c: Ctx):
    await c.dashboard("analytics.read")
    db, store = await c.db(), c.store
    since, until, window = _window(c)
    by_source = await db.fetch("SELECT source, COUNT(*) AS n, COALESCE(SUM(total_minor), 0) AS revenue FROM orders WHERE store_id = ? AND deleted_at IS NULL "
                               "AND payment_status IN ('paid','partially_refunded') AND paid_at >= ? AND paid_at < ? GROUP BY source", [store.pk, since, until])
    by_status = await db.fetch("SELECT status, COUNT(*) AS n FROM orders WHERE store_id = ? AND deleted_at IS NULL AND created_at >= ? AND created_at < ? GROUP BY status", [store.pk, since, until])
    by_provider = await db.fetch("SELECT provider, COUNT(*) AS n, COALESCE(SUM(amount_minor), 0) AS revenue FROM payments WHERE store_id = ? AND deleted_at IS NULL AND status = 'succeeded' "
                                 "AND created_at >= ? AND created_at < ? GROUP BY provider", [store.pk, since, until])
    return {"range": window, "summary": await analytics.overview(db, store=store, since=since, until=until), "series": await analytics.series(db, store=store, since=since, until=until),
            "breakdown": {"by_source": [{"key": r["source"], "orders": r["n"], "revenue": Money(r["revenue"] or 0, store.currency).as_prop()} for r in by_source],
                          "by_status": [{"key": r["status"], "orders": r["n"]} for r in by_status],
                          "by_provider": [{"key": r["provider"], "payments": r["n"], "revenue": Money(r["revenue"] or 0, store.currency).as_prop()} for r in by_provider]}}


# ── notifications ────────────────────────────────────────────────────────

@endpoint("notifications.list", "GET", "/dash/{store}/notifications", area="notifications", permission=None,
          summary="The notification centre, filtered *in the query path* by permission (a payment failure is not for the inventory clerk), so an unauthorised one never leaves the server",
          original="GET /notifications")
async def notifications(c: Ctx):
    await c.dashboard()
    db, store = await c.db(), c.store
    rows = await db.fetch("SELECT * FROM notifications WHERE store_id = ? AND deleted_at IS NULL AND (user_id IS NULL OR user_id = ?) ORDER BY id DESC LIMIT 100", [store.pk, c.user_id])
    visible = [q.Row(n) for n in rows if not n["required_permission"] or c.can(n["required_permission"])]
    return {"data": [{"id": n.pk, "kind": n.kind, "title": n.title, "body": n.body, "url": n.url, "level": n.level, "read_at": n.read_at, "created_at": n.created_at} for n in visible],
            "unread": sum(1 for n in visible if not n.read_at)}


@endpoint("notifications.read", "POST", "/dash/{store}/notifications/read", area="notifications", permission=None, summary="Mark one notification (id) or every one read",
          fields=[{"name": "id", "type": "integer"}], original="POST /notifications/read")
async def notifications_read(c: Ctx):
    await c.dashboard()
    db, store = await c.db(), c.store
    now = datetime.now(UTC)
    if c.input.get("id"):
        n = await db.execute("UPDATE notifications SET read_at = ? WHERE id = ? AND store_id = ? AND read_at IS NULL", [now, int(c.input["id"]), store.pk])
    else:
        n = await db.execute("UPDATE notifications SET read_at = ? WHERE store_id = ? AND read_at IS NULL AND (user_id IS NULL OR user_id = ?)", [now, store.pk, c.user_id])
    return {"updated": n}


# ── global search ────────────────────────────────────────────────────────

@endpoint("search", "GET", "/dash/{store}/search", area="search", permission=None,
          summary="The command palette's query: one endpoint across five resources, five rows each. The cap is the design: a hundred results means the merchant belongs on a list page",
          original="GET /search")
async def search(c: Ctx):
    await c.dashboard()
    db, store = await c.db(), c.store
    term = (c.arg("q") or "").strip()
    if len(term) < 2:
        return {"query": term, "results": []}
    like, results = f"%{term.lower()}%", []
    if c.can("orders.read"):
        number = int(term.lstrip("#")) if term.lstrip("#").isdigit() else -1
        for o in await db.fetch("SELECT * FROM orders WHERE store_id = ? AND deleted_at IS NULL AND (LOWER(email) LIKE ? OR number = ?) ORDER BY id DESC LIMIT 5", [store.pk, like, number]):
            results.append({"type": "order", "id": o["id"], "title": f"Order #{o['number']}", "subtitle": f"{o['email']} · {Money(o['total_minor'], store.currency).format()}",
                            "url": f"/orders/{o['id']}", "badge": o["status"]})
    if c.can("products.read"):
        for p in await db.fetch("SELECT * FROM products WHERE store_id = ? AND deleted_at IS NULL AND (LOWER(title) LIKE ? OR LOWER(slug) LIKE ?) ORDER BY id DESC LIMIT 5", [store.pk, like, like]):
            results.append({"type": "product", "id": p["id"], "title": p["title"], "subtitle": p["product_type"] or "Product", "url": f"/products/{p['id']}", "badge": p["status"]})
    if c.can("customers.read"):
        for u in await db.fetch("SELECT * FROM customers WHERE store_id = ? AND deleted_at IS NULL AND (LOWER(email) LIKE ? OR LOWER(COALESCE(first_name,'')) LIKE ? "
                                "OR LOWER(COALESCE(last_name,'')) LIKE ?) ORDER BY id DESC LIMIT 5", [store.pk, like, like, like]):
            row = q.Row(u)
            results.append({"type": "customer", "id": row.pk, "title": row.name, "subtitle": f"{row.orders_count} orders · {Money(row.total_spent_minor or 0, store.currency).format()}",
                            "url": f"/customers/{row.pk}", "badge": None})
    if c.can("payments.read"):
        for p in await db.fetch("SELECT * FROM payments WHERE store_id = ? AND deleted_at IS NULL AND (LOWER(reference) LIKE ? OR LOWER(COALESCE(provider_reference,'')) LIKE ?) ORDER BY id DESC LIMIT 5",
                                [store.pk, like, like]):
            results.append({"type": "transaction", "id": p["id"], "title": p["reference"], "subtitle": f"{p['provider']} · {Money(p['amount_minor'], p['currency']).format()}",
                            "url": f"/orders/{p['order_id']}", "badge": p["status"]})
    if c.can("campaigns.read"):
        for k in await db.fetch("SELECT * FROM campaigns WHERE store_id = ? AND deleted_at IS NULL AND LOWER(name) LIKE ? ORDER BY id DESC LIMIT 5", [store.pk, like]):
            results.append({"type": "campaign", "id": k["id"], "title": k["name"], "subtitle": k["kind"].replace("_", " ").title(), "url": f"/marketing/campaigns/{k['id']}", "badge": k["status"]})
    return {"query": term, "results": results}


# ── exports ──────────────────────────────────────────────────────────────

def _job(j: q.Row) -> dict[str, Any]:
    return {"id": j.pk, "resource": j.resource, "status": j.status, "row_count": j.row_count, "file_size": j.file_size, "error": j.error, "requested_by": j.requested_by_id,
            "created_at": j.created_at, "completed_at": j.completed_at, "expires_at": j.expires_at, "downloadable": j.status == "complete"}


@endpoint("exports.list", "GET", "/dash/{store}/exports", area="exports", permission="reports.export", summary="Recent exports and what can be exported", original="GET /exports")
async def exports_list(c: Ctx):
    await c.dashboard("reports.export")
    rows = await q.find(await c.db(), "export_jobs", {"store_id": c.store.pk}, order="id DESC", limit=50)
    return {"data": [_job(j) for j in rows], "resources": [{"key": k, "label": label} for k, (label, _) in exports.RESOURCES.items()]}


@endpoint("exports.request", "POST", "/dash/{store}/exports", area="exports", permission="reports.export",
          summary="Queue an export. The permission for the *resource* is checked too: `reports.export` alone must not let a marketing member export the customer list by naming it",
          fields=[{"name": "resource", "type": "string", "required": True}, {"name": "filters", "type": "json"}], original="POST /exports")
async def export_request(c: Ctx):
    await c.dashboard("reports.export")
    db, store = await c.db(), c.store
    resource = c.input.get("resource")
    if resource not in exports.RESOURCES:
        raise unprocessable("That can't be exported.", "validation_failed", {"resource": "That can't be exported."})
    if not c.can(exports.RESOURCES[resource][1]):
        raise forbidden(f"You don't have permission to export {resource}.")
    filters = c.input.get("filters") if isinstance(c.input.get("filters"), dict) else {k: v for k, v in c.input.items() if k not in ("resource", "store", "filters")}
    job = await exports.request_export(db, store=store, resource=resource, filters=filters, user_id=c.user_id)
    # The row alone does nothing; this is what actually runs it. Without it a queued export would sit at "queued" until the sweeper noticed.
    await c.dispatch("exports.run", {"export_id": job.pk})
    return {**_job(job), "message": "Export queued. It will appear here when it's ready."}


@endpoint("exports.download", "GET", "/dash/{store}/exports/{export_id}/download", area="exports", permission="reports.export",
          summary="A short-lived signed URL for a finished export (the files hold customer names, emails and addresses, so the tenant scope and permission are checked first)",
          original="GET /exports/{id}/download")
async def export_download(c: Ctx):
    await c.dashboard("reports.export")
    job = await q.first(await c.db(), "export_jobs", {"id": c.int_arg("export_id", required=True), "store_id": c.store.pk})
    if job is None or job.status != "complete" or not job.file_path:
        raise not_found("That export")
    return {"url": await exports.download_url(c, job), "expires_in": 300, "filename": job.file_path.rsplit("/", 1)[-1]}
