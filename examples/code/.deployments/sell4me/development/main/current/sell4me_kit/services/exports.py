"""CSV exports, produced in the background.

A store with fifty thousand orders cannot render a CSV inside a request, so an export is a job: the row is created immediately,
the file appears when it is ready (in the private ``exports`` bucket, because it holds customer names, emails and addresses and must
be served only after a permission check), and the dashboard polls the row it already knows about. Every export writes the *filtered*
set the merchant was looking at. Money is a decimal string in the store's currency: never a float, never minor units.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

from .. import q
from . import analytics, storefront
from ..money import Money, to_major

RETENTION_HOURS = 48
EXPORT_BUCKET = "exports"

#: What can be exported, and the permission each needs.
RESOURCES: dict[str, tuple[str, str]] = {
    "orders": ("Orders", "orders.read"), "customers": ("Customers", "customers.read"), "products": ("Products", "products.read"),
    "inventory": ("Inventory", "inventory.read"), "transactions": ("Transactions", "payments.read"), "ledger": ("Ledger entries", "payments.read"),
    "discounts": ("Discounts", "discounts.read"), "collections": ("Collections", "products.read"), "team": ("Team", "staff.read"),
    "shipping": ("Shipping", "settings.read"), "payouts": ("Payouts", "payments.read"), "refunds": ("Refunds", "payments.read"),
    "segments": ("Segments", "customers.read"), "campaigns": ("Campaigns", "campaigns.read"),
    "full_report": ("Complete store export (Excel)", "reports.export"),
}


async def request_export(db: Any, *, store: q.Row, resource: str, filters: dict[str, Any], user_id: str | None) -> q.Row:
    """Queue an export. Returns immediately."""
    if resource not in RESOURCES:
        raise ValueError(f"Cannot export {resource!r}.")
    return await q.insert(db, "export_jobs", {"store_id": store.pk, "requested_by_id": user_id, "resource": resource, "filters": filters, "status": "queued", "row_count": 0})


async def run(c: Any, job: q.Row) -> q.Row:
    """Produce one export's file. Claims the job first, so two workers picking up the same row produce one file, not two half-written ones."""
    db = await c.db()
    if not await db.execute("UPDATE export_jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'", [datetime.now(UTC), job.pk]):
        return job
    store = await q.get(db, "stores", job.store_id)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if job.resource == "full_report":
        workbook = await _build_full_report(c, db, store, job.filters or {})
        buffer = io.BytesIO()
        workbook.save(buffer)
        data, extension = buffer.getvalue(), "xlsx"
        row_count = sum(sheet.max_row - 1 for sheet in workbook.worksheets if sheet.max_row > 1)
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        rows, header = await _BUILDERS[job.resource](db, store, job.filters or {})
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(header)
        writer.writerows(rows)
        # utf-8-sig: Excel on Windows reads a plain UTF-8 CSV as latin-1 and turns every accented name into mojibake.
        data, extension, row_count, content_type = text.getvalue().encode("utf-8-sig"), "csv", len(rows), "text/csv"
    key = f"{store.slug}/{store.slug}-{job.resource}-{stamp}-{job.pk}.{extension}"
    await c.runtime.storage_put(EXPORT_BUCKET, key, data, content_type)
    completed = datetime.now(UTC)
    await q.update(db, "export_jobs", job.pk, {"status": "complete", "row_count": row_count, "format": extension, "file_path": key, "file_size": len(data),
                                               "completed_at": completed, "expires_at": completed + timedelta(hours=RETENTION_HOURS)})
    return await q.get(db, "export_jobs", job.pk)


async def download_url(c: Any, job: q.Row, expires_in: int = 300) -> str:
    return await c.runtime.storage_signed_url(EXPORT_BUCKET, job.file_path, "GET", expires_in)


async def discard(c: Any, job: q.Row) -> None:
    """Delete a finished export's file and mark the row expired."""
    db = await c.db()
    if job.file_path:
        try:
            await c.runtime.storage_delete(EXPORT_BUCKET, job.file_path)
        except Exception:  # noqa: BLE001
            pass
    await q.update(db, "export_jobs", job.pk, {"status": "expired", "file_path": None})


def _money(minor: int | None, currency: str) -> str:
    return f"{to_major(minor or 0, currency):f}"


def _when(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else (value or "")


def _filtered(where: dict[str, Any], filters: dict[str, Any], column: str) -> dict[str, Any]:
    if since := filters.get("since"):
        where[column] = q.gte(since)
    return where


async def _orders(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    clauses, params = ["o.store_id = ?", "o.deleted_at IS NULL"], [store.pk]
    if filters.get("status"):
        clauses.append("o.status = ?")
        params.append(filters["status"])
    if filters.get("since"):
        clauses.append("o.created_at >= ?")
        params.append(datetime.fromisoformat(filters["since"]))
    if filters.get("until"):
        clauses.append("o.created_at < ?")
        params.append(datetime.fromisoformat(filters["until"]))
    header = ["Order", "Placed at", "Paid at", "Status", "Payment status", "Fulfilment", "Customer email", "Currency", "Subtotal", "Discount", "Shipping", "Tax",
              "Total", "Refunded", "Discount code", "Shipping method", "Items"]
    rows = []
    for o in await db.fetch(f"SELECT o.*, (SELECT COALESCE(SUM(quantity), 0) FROM order_items i WHERE i.order_id = o.id AND i.deleted_at IS NULL) AS item_count "
                            f"FROM orders o WHERE {' AND '.join(clauses)} ORDER BY o.id DESC LIMIT 50000", params):
        o = q.Row(o)
        c = o.currency
        rows.append([f"#{o.number}", _when(o.placed_at or o.created_at), _when(o.paid_at), o.status, o.payment_status, o.fulfilment_status, o.email, c,
                     _money(o.subtotal_minor, c), _money(o.discount_minor, c), _money(o.shipping_minor, c), _money(o.tax_minor, c), _money(o.total_minor, c),
                     _money(o.refunded_minor, c), o.discount_code or "", o.shipping_method or "", o["item_count"]])
    return rows, header


async def _customers(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    where: dict[str, Any] = {"store_id": store.pk}
    if filters.get("accepts_marketing"):
        where["accepts_marketing"] = True
    header = ["Email", "First name", "Last name", "Phone", "Orders", "Total spent", "Average order", "First order", "Last order", "Accepts marketing", "Tags", "Created at"]
    rows = [[c.email, c.first_name or "", c.last_name or "", c.phone or "", c.orders_count, _money(c.total_spent_minor, store.currency),
             _money(c.average_order_minor, store.currency), _when(c.first_order_at), _when(c.last_order_at), "yes" if c.accepts_marketing else "no",
             ", ".join(c.tags or []), _when(c.created_at)] for c in await q.find(db, "customers", where, order="total_spent_minor DESC", limit=50000)]
    return rows, header


async def _products(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    where: dict[str, Any] = {"store_id": store.pk}
    if filters.get("status"):
        where["status"] = filters["status"]
    header = ["Product", "Slug", "Status", "Variant", "SKU", "Barcode", "Price", "Compare at", "Cost", "Stock", "Tracked", "Weight (g)", "Vendor", "Type", "Tags"]
    rows = []
    for product in await q.find(db, "products", where, order="title", limit=20000):
        for v in await q.find(db, "product_variants", {"product_id": product.pk}, order="position, id"):
            rows.append([product.title, product.slug, product.status, v.title, v.sku or "", v.barcode or "", _money(v.price_minor, store.currency),
                         _money(v.compare_at_minor, store.currency) if v.compare_at_minor else "", _money(v.cost_minor, store.currency) if v.cost_minor else "",
                         v.stock, "yes" if v.track_inventory else "no", v.weight_grams, product.vendor or "", product.product_type or "", ", ".join(product.tags or [])])
    return rows, header


async def _inventory(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["When", "SKU", "Variant", "Change", "Balance", "Reason", "Note", "Reference"]
    rows = [[_when(m["created_at"]), m["sku"] or "", m["variant_title"], m["delta"], m["balance_after"], m["reason"], m["note"] or "",
             f"{m['reference_type'] or ''} {m['reference_id'] or ''}".strip()]
            for m in await db.fetch("SELECT m.*, v.sku AS sku, v.title AS variant_title FROM inventory_movements m JOIN product_variants v ON v.id = m.variant_id "
                                    "WHERE m.store_id = ? AND m.deleted_at IS NULL ORDER BY m.id DESC LIMIT 50000", [store.pk])]
    return rows, header


async def _transactions(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Reference", "Provider", "Provider reference", "Order", "Status", "Currency", "Amount", "Provider fee", "Platform fee", "Refunded", "Net to you",
              "Method", "Card", "Created at", "Captured at"]
    rows = []
    for p in await db.fetch("SELECT p.*, o.number AS order_number FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE p.store_id = ? AND p.deleted_at IS NULL "
                            "ORDER BY p.id DESC LIMIT 50000", [store.pk]):
        c = p["currency"]
        net = (p["amount_minor"] or 0) - (p["provider_fee_minor"] or 0) - (p["platform_fee_minor"] or 0) - (p["refunded_minor"] or 0)
        rows.append([p["reference"], p["provider"], p["provider_reference"], f"#{p['order_number']}" if p["order_number"] else "", p["status"], c,
                     _money(p["amount_minor"], c), _money(p["provider_fee_minor"], c) if p["provider_fee_minor"] is not None else "", _money(p["platform_fee_minor"], c),
                     _money(p["refunded_minor"], c), _money(net, c), p["method"] or "", f"{p['card_brand'] or ''} {p['card_last4'] or ''}".strip(),
                     _when(p["created_at"]), _when(p["captured_at"])])
    return rows, header


async def _ledger(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    clauses, params = ["e.store_id = ?", "e.deleted_at IS NULL"], [store.pk]
    if filters.get("since"):
        clauses.append("e.occurred_at >= ?")
        params.append(datetime.fromisoformat(filters["since"]))
    if filters.get("until"):
        clauses.append("e.occurred_at < ?")
        params.append(datetime.fromisoformat(filters["until"]))
    header = ["Occurred at", "Kind", "Amount", "Currency", "Order", "Description"]
    rows = [[_when(e["occurred_at"]), e["kind"], _money(e["amount_minor"], e["currency"]), e["currency"], f"#{e['order_number']}" if e["order_number"] else "", e["description"]]
            for e in await db.fetch(f"SELECT e.*, o.number AS order_number FROM ledger_entries e LEFT JOIN orders o ON o.id = e.order_id WHERE {' AND '.join(clauses)} "
                                    f"ORDER BY e.id DESC LIMIT 50000", params)]
    return rows, header


async def _discounts(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Code", "Title", "Kind", "Value", "State", "Uses", "Use limit", "Per-customer limit", "Minimum order", "Starts", "Ends", "Automatic"]
    rows = []
    for d in await q.find(db, "discounts", {"store_id": store.pk}, order="id DESC", limit=20000):
        value = f"{d.value}%" if d.kind == "percentage" else _money(d.value, store.currency) if d.kind == "fixed" else "—"
        rows.append([d.code, d.title or "", d.kind, value, discount_state(d), d.usage_count, d.usage_limit if d.usage_limit is not None else "",
                     d.per_customer_limit if d.per_customer_limit is not None else "", _money(d.minimum_order_minor, store.currency) if d.minimum_order_minor else "",
                     _when(d.starts_at), _when(d.ends_at), "yes" if d.is_automatic else "no"])
    return rows, header


def discount_state(d: q.Row) -> str:
    if not d.is_active:
        return "disabled"
    if d.is_expired:
        return "expired"
    if d.is_exhausted:
        return "exhausted"
    starts = q.parse_dt(d.starts_at)
    return "scheduled" if starts and starts > datetime.now(UTC) else "active"


async def _collections(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Title", "Slug", "Kind", "Published", "Products", "Position", "Created at"]
    rows = []
    for col in await q.find(db, "collections", {"store_id": store.pk}, order="position", limit=20000):
        products = len(col.rules or []) if col.kind == "automatic" else await q.count(db, "collection_products", {"collection_id": col.pk})
        rows.append([col.title, col.slug, col.kind, "yes" if col.is_published else "no", products, col.position, _when(col.created_at)])
    return rows, header


async def _team(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Name", "Email", "Role", "Status", "Last seen", "Joined"]
    rows = []
    for m in await q.find(db, "store_members", {"store_id": store.pk}, order="id"):
        profile = await q.first(db, "profiles", {"user_id": m.user_id})
        rows.append([(profile.full_name if profile else "") or "", (profile.email if profile else "") or "", m.role, m.status, _when(m.last_seen_at), _when(m.created_at)])
    for invite in await q.find(db, "invitations", {"store_id": store.pk, "accepted_at": None, "revoked_at": None}):
        rows.append(["", invite.email, invite.role, "invited", "", _when(invite.created_at)])
    return rows, header


async def _shipping(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Zone", "Countries", "Rate", "Kind", "Price", "Min weight (g)", "Max weight (g)", "Min order", "Max order", "Delivery estimate", "Active"]
    rows = []
    for zone in await q.find(db, "shipping_zones", {"store_id": store.pk}, order="position"):
        countries = "Everywhere" if "*" in (zone.countries or []) else ", ".join(zone.countries or [])
        rates = await q.find(db, "shipping_rates", {"zone_id": zone.pk}, order="position")
        if not rates:
            rows.append([zone.name, countries, "", "", "", "", "", "", "", "", ""])
        for r in rates:
            rows.append([zone.name, countries, r.name, r.kind, _money(r.price_minor, store.currency), r.min_weight_grams if r.min_weight_grams is not None else "",
                         r.max_weight_grams if r.max_weight_grams is not None else "", _money(r.min_subtotal_minor, store.currency) if r.min_subtotal_minor else "",
                         _money(r.max_subtotal_minor, store.currency) if r.max_subtotal_minor else "", r.delivery_estimate or "", "yes" if r.is_active else "no"])
    return rows, header


async def _payouts(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Reference", "Provider", "Order", "Status", "Amount", "Platform fee", "Destination", "Created at"]
    rows = [[p["provider_reference"], p["provider"], f"#{p['order_number']}" if p["order_number"] else "", p["status"], _money(p["amount_minor"], p["currency"]),
             _money(p["platform_fee_minor"], p["currency"]), p["destination"] or "", _when(p["created_at"])]
            for p in await db.fetch("SELECT p.*, o.number AS order_number FROM payouts p LEFT JOIN orders o ON o.id = p.order_id WHERE p.store_id = ? AND p.deleted_at IS NULL "
                                    "ORDER BY p.id DESC LIMIT 50000", [store.pk])]
    return rows, header


async def _refunds(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Reference", "Order", "Status", "Amount", "Reason", "Restocked", "Processed at"]
    rows = [[r["reference"], f"#{r['order_number']}" if r["order_number"] else "", r["status"], _money(r["amount_minor"], r["currency"]), r["reason"] or "",
             "yes" if r["restock"] else "no", _when(r["processed_at"])]
            for r in await db.fetch("SELECT r.*, o.number AS order_number FROM refunds r LEFT JOIN orders o ON o.id = r.order_id WHERE r.store_id = ? AND r.deleted_at IS NULL "
                                    "ORDER BY r.id DESC LIMIT 50000", [store.pk])]
    return rows, header


async def _segments(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Name", "Description", "System", "Customer count", "Refreshed at"]
    rows = [[s.name, s.description or "", "yes" if s.is_system else "no", s.cached_count, _when(s.counted_at)] for s in await q.find(db, "segments", {"store_id": store.pk}, order="id")]
    return rows, header


async def _campaigns(db: Any, store: q.Row, filters: dict[str, Any]) -> tuple[list[list[Any]], list[str]]:
    header = ["Name", "Kind", "Status", "Segment", "Discount code", "Created at"]
    rows = []
    for cm in await q.find(db, "campaigns", {"store_id": store.pk}, order="id DESC"):
        segment, discount = await q.get(db, "segments", cm.segment_id), await q.get(db, "discounts", cm.discount_id)
        rows.append([cm.name, cm.kind, cm.status, segment.name if segment else "", discount.code if discount else "", _when(cm.created_at)])
    return rows, header


_BUILDERS = {"orders": _orders, "customers": _customers, "products": _products, "inventory": _inventory, "transactions": _transactions, "ledger": _ledger,
             "discounts": _discounts, "collections": _collections, "team": _team, "shipping": _shipping, "payouts": _payouts, "refunds": _refunds,
             "segments": _segments, "campaigns": _campaigns}


async def _build_full_report(c: Any, db: Any, store: q.Row, filters: dict[str, Any]) -> Any:
    """A branded workbook of the whole store, not a summary of it: a cover page with the headline numbers and a 30-day trend chart, then a
    full styled sheet of *every row* of every part of the store. Each sheet reuses the exact query the matching CSV export uses, so the two can
    never disagree about what "products" contains."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    theme = await storefront.ensure_theme(db, store)
    accent = _hex(theme.color_primary) or "1F2430"
    now = datetime.now(UTC)
    overview_30 = await analytics.overview(db, store=store, since=now - timedelta(days=30), until=now)
    overview_90 = await analytics.overview(db, store=store, since=now - timedelta(days=90), until=now)
    trend = await analytics.series(db, store=store, since=now - timedelta(days=30), until=now)
    counts = {"products": await q.count(db, "products", {"store_id": store.pk}), "customers": await q.count(db, "customers", {"store_id": store.pk}),
              "orders": await q.count(db, "orders", {"store_id": store.pk}), "discounts": await q.count(db, "discounts", {"store_id": store.pk})}
    _cover_sheet(wb, store, accent, overview_30, overview_90, trend, counts)
    for title, key in (("Orders", "orders"), ("Products", "products"), ("Collections", "collections"), ("Customers", "customers"), ("Segments", "segments"),
                       ("Discounts", "discounts"), ("Campaigns", "campaigns"), ("Inventory", "inventory"), ("Shipping", "shipping"), ("Transactions", "transactions"),
                       ("Payouts", "payouts"), ("Refunds", "refunds"), ("Ledger", "ledger"), ("Team", "team")):
        rows, header = await _BUILDERS[key](db, store, filters)
        _data_sheet(wb, title, header, rows, accent)
    return wb


def _hex(value: str | None) -> str | None:
    """A theme colour as the 6-digit hex `openpyxl` wants — no `#`, no alpha."""
    if not value:
        return None
    text = value.strip().lstrip("#")
    return text.upper() if len(text) == 6 else None


def _cover_sheet(
    wb: Any, store: q.Row, accent: str, overview_30: dict, overview_90: dict,
    trend: list[dict], counts: dict[str, int],
) -> None:
    from openpyxl.chart import LineChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill

    ws = wb.create_sheet("Overview", 0)
    ws.sheet_view.showGridLines = False

    dark = Font(color="FFFFFF", bold=True)
    fill = PatternFill("solid", fgColor=accent)

    ws.merge_cells("B2:H3")
    title_cell = ws["B2"]
    title_cell.value = store.name
    title_cell.font = Font(size=26, bold=True, color=accent)
    for row in ws["B2:H3"]:
        for cell in row:
            cell.fill = PatternFill("solid", fgColor="F4F1EC")
    title_cell.alignment = Alignment(vertical="center")

    ws["B4"] = f"Complete store export · generated {datetime.now(UTC).strftime('%d %B %Y, %H:%M UTC')}"
    ws["B4"].font = Font(italic=True, color="6B6459", size=11)
    ws["B5"] = f"Currency: {store.currency} · {store.slug}"
    ws["B5"].font = Font(color="6B6459", size=10)

    # -- KPI cards -----------------------------------------------------------
    cards = [
        ("Gross revenue (30d)", Money(overview_30["gross"]["minor"], store.currency).format()),
        ("Net revenue (30d)", Money(overview_30["net"]["minor"], store.currency).format()),
        ("Orders (30d)", overview_30["orders"]),
        ("Avg order value", Money(overview_30["average_order"]["minor"], store.currency).format()),
        ("New customers (30d)", overview_30["new_customers"]),
        ("Gross revenue (90d)", Money(overview_90["gross"]["minor"], store.currency).format()),
        ("Total products", counts["products"]),
        ("Total customers", counts["customers"]),
        ("Total orders", counts["orders"]),
        ("Active discount codes", counts["discounts"]),
    ]
    start_row = 7
    for index, (label, value) in enumerate(cards):
        col = 2 + (index % 2) * 4
        row = start_row + (index // 2) * 3
        label_cell = ws.cell(row=row, column=col, value=label)
        label_cell.font = Font(size=10, color="6B6459")
        value_cell = ws.cell(row=row + 1, column=col, value=value)
        value_cell.font = Font(size=18, bold=True, color="141414")
        for r in (row, row + 1):
            for c in range(col, col + 3):
                ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor="FBFAF8")

    # -- the trend table + chart ----------------------------------------------
    chart_row = start_row + (len(cards) // 2) * 3 + 3
    ws.cell(row=chart_row, column=2, value="Last 30 days").font = Font(bold=True, size=13, color="141414")

    header_row = chart_row + 1
    for offset, label in enumerate(("Date", "Orders", "Revenue")):
        cell = ws.cell(row=header_row, column=2 + offset, value=label)
        cell.font = dark
        cell.fill = fill
    for i, point in enumerate(trend):
        r = header_row + 1 + i
        ws.cell(row=r, column=2, value=point["date"])
        ws.cell(row=r, column=3, value=point["orders"])
        cell = ws.cell(row=r, column=4, value=point["revenue_minor"] / 100)
        cell.number_format = "#,##0.00"

    last_row = header_row + len(trend)
    chart = LineChart()
    chart.title = "Revenue, last 30 days"
    chart.style = 2
    chart.y_axis.title = f"Revenue ({store.currency})"
    chart.x_axis.title = "Date"
    chart.height, chart.width = 9, 20
    data = Reference(ws, min_col=4, min_row=header_row, max_row=last_row)
    cats = Reference(ws, min_col=2, min_row=header_row + 1, max_row=last_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    series = chart.series[0]
    series.graphicalProperties.line.solidFill = accent
    series.graphicalProperties.line.width = 22000
    series.smooth = False
    ws.add_chart(chart, f"F{header_row}")

    for col, width in zip("ABCDEFGH", (2, 20, 16, 16, 4, 4, 4, 4)):
        ws.column_dimensions[col].width = width


def _data_sheet(wb: Any, title: str, header: list[str], rows: list[list[Any]], accent: str) -> None:
    """One resource, as a proper Excel table: a coloured frozen header,
    banded rows, an autofilter, sane column widths, and — for anything with a
    Stock column — a conditional highlight on the rows running low."""
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(title[:31])
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=accent)
    ws.freeze_panes = "A2"

    band = PatternFill("solid", fgColor="F7F5F1")
    for i, row in enumerate(rows):
        ws.append(row)
        if i % 2 == 1:
            for cell in ws[ws.max_row]:
                cell.fill = band

    if rows:
        ws.auto_filter.ref = ws.dimensions

    widths = [len(str(h)) for h in header]
    for row in rows[:2000]:
        for i, value in enumerate(row):
            widths[i] = min(48, max(widths[i], len(str(value))))
    for i, width in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = width + 2

    if "Stock" in header:
        col = get_column_letter(header.index("Stock") + 1)
        last = len(rows) + 1
        if last > 1:
            ws.conditional_formatting.add(
                f"{col}2:{col}{last}",
                CellIsRule(operator="lessThan", formula=["5"], fill=PatternFill("solid", fgColor="FCE4E4")),
            )
    if "Status" in header:
        col = get_column_letter(header.index("Status") + 1)
        last = len(rows) + 1
        if last > 1:
            ws.conditional_formatting.add(
                f"{col}2:{col}{last}",
                CellIsRule(operator="equal", formula=['"cancelled"'], fill=PatternFill("solid", fgColor="FCE4E4")),
            )
