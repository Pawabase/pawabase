"""Campaign attribution and scheduling.

An order counts toward a campaign when it used that campaign's discount code, or names the
campaign directly. Nothing wider is claimed: a number invented to fill a column is worse than
an empty column.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .. import q

log = logging.getLogger("sell4me.campaigns")


async def refresh_results(db: Any, campaign: q.Row) -> None:
    where = ["store_id = ?", "deleted_at IS NULL", "payment_status IN ('paid', 'partially_refunded')"]
    params: list[Any] = [campaign.store_id]
    if campaign.discount_id is None:
        where.append("campaign_id = ?")
        params.append(campaign.pk)
    else:
        where.append("(discount_id = ? OR campaign_id = ?)")
        params += [campaign.discount_id, campaign.pk]
    if campaign.starts_at:
        where.append("paid_at >= ?")
        params.append(campaign.starts_at)
    if campaign.ends_at:
        where.append("paid_at <= ?")
        params.append(campaign.ends_at)
    row = await db.one(f"SELECT COUNT(*) AS n, COALESCE(SUM(total_minor), 0) AS revenue FROM orders WHERE {' AND '.join(where)}", params) or {}
    update: dict[str, Any] = {"orders_count": int(row.get("n") or 0), "revenue_minor": int(row.get("revenue") or 0), "last_run_at": datetime.now(UTC)}
    if campaign.kind == "abandoned_cart":
        update["recovered_count"] = await q.count(db, "abandoned_carts", {"store_id": campaign.store_id, "recovery_status": "recovered"})
    await q.update(db, "campaigns", campaign.pk, update)


async def advance_scheduled(db: Any) -> tuple[int, int]:
    """Start campaigns whose window opened and finish those whose window closed. Returns (started, completed)."""
    now = datetime.now(UTC)
    started = completed = 0
    for campaign in await q.find(db, "campaigns", {"status": "scheduled", "starts_at": q.lte(now)}):
        await q.update(db, "campaigns", campaign.pk, {"status": "active", "last_run_at": now})
        started += 1
    for campaign in await q.find(db, "campaigns", {"status": "active", "ends_at": q.lt(now)}):
        await q.update(db, "campaigns", campaign.pk, {"status": "completed"})
        completed += 1
    for campaign in await q.find(db, "campaigns", {"status": q.in_(["active", "completed"])}, limit=200):
        try:
            await refresh_results(db, campaign)
        except Exception:  # noqa: BLE001
            log.exception("refreshing campaign %s failed", campaign.pk)
    return started, completed
