"""The book. Every movement of money, recorded once and never edited.

Nothing outside this module writes a ``ledger_entries`` row. The sign convention is the
merchant's: a balance for any window is ``SUM(amount_minor)``, with no per-kind sign table.

    charge +  provider_fee -  platform_fee -  refund -  payout -  adjustment +/-

The platform fee is a flat amount per **successful order** (not per payment), except for Paystack
orders, where the platform's cut is the percentage taken out of the payout (see ``payouts``);
booking both would charge the merchant twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .. import q
from ..money import Money


def platform_fee_for(settings: Any, store: q.Row, order: q.Row) -> int:
    """The platform's flat cut for one order, in minor units (the numeral is used as-is across currencies: no FX guessing)."""
    return settings.platform_fee_minor


async def book_charge(c: Any, *, store: q.Row, order: q.Row, payment: q.Row, occurred_at: datetime | None = None) -> list[q.Row]:
    """Record a successful payment: gross in, fees out. Idempotent on the payment."""
    db = await c.db()
    existing = await q.find(db, "ledger_entries", {"payment_id": payment.pk, "kind": "charge"})
    if existing:
        return existing
    settings = await c.settings()
    when = occurred_at or q.parse_dt(payment.captured_at) or datetime.now(UTC)
    currency = payment.currency
    entries = [await q.insert(db, "ledger_entries", {
        "store_id": store.pk, "kind": "charge", "amount_minor": payment.amount_minor, "currency": currency, "order_id": order.pk,
        "payment_id": payment.pk, "description": f"Payment for order #{order.number}",
        "metadata": {"provider": payment.provider, "provider_reference": payment.provider_reference, "method": payment.method},
        "occurred_at": when})]
    if payment.provider_fee_minor:
        entries.append(await q.insert(db, "ledger_entries", {
            "store_id": store.pk, "kind": "provider_fee", "amount_minor": -abs(payment.provider_fee_minor), "currency": currency,
            "order_id": order.pk, "payment_id": payment.pk, "description": f"{payment.provider.title()} processing fee",
            "metadata": {"provider": payment.provider}, "occurred_at": when}))
    already = await q.exists(db, "ledger_entries", {"order_id": order.pk, "kind": "platform_fee"})
    if not already and payment.provider != "paystack":
        fee = platform_fee_for(settings, store, order)
        if fee:
            await q.update(db, "payments", payment.pk, {"platform_fee_minor": fee})
            entries.append(await q.insert(db, "ledger_entries", {
                "store_id": store.pk, "kind": "platform_fee", "amount_minor": -abs(fee), "currency": currency, "order_id": order.pk,
                "payment_id": payment.pk, "description": "Platform transaction fee",
                "metadata": {"rate": "flat", "configured_minor": settings.platform_fee_minor, "configured_currency": settings.platform_fee_currency},
                "occurred_at": when}))
    return entries


async def book_refund(c: Any, *, store: q.Row, order: q.Row, refund: q.Row, occurred_at: datetime | None = None) -> list[q.Row]:
    """Record money returned; the platform fee comes back only on a full refund. Idempotent on the refund."""
    db = await c.db()
    existing = await q.find(db, "ledger_entries", {"refund_id": refund.pk, "kind": "refund"})
    if existing:
        return existing
    when = occurred_at or q.parse_dt(refund.processed_at) or datetime.now(UTC)
    entries = [await q.insert(db, "ledger_entries", {
        "store_id": store.pk, "kind": "refund", "amount_minor": -abs(refund.amount_minor), "currency": refund.currency,
        "order_id": order.pk, "payment_id": refund.payment_id, "refund_id": refund.pk, "description": f"Refund on order #{order.number}",
        "metadata": {"reason": refund.reason, "provider": refund.provider}, "occurred_at": when})]
    fresh = await q.get(db, "orders", order.pk)
    if (fresh.refunded_minor or 0) >= fresh.total_minor and not refund.platform_fee_reversed:
        reversal = await reverse_platform_fee(c, store=store, order=fresh, refund=refund, when=when)
        if reversal is not None:
            entries.append(reversal)
    return entries


async def reverse_platform_fee(c: Any, *, store: q.Row, order: q.Row, refund: q.Row, when: datetime) -> q.Row | None:
    db = await c.db()
    booked = int(await db.scalar("SELECT COALESCE(SUM(amount_minor), 0) FROM ledger_entries WHERE order_id = ? AND kind = 'platform_fee' AND deleted_at IS NULL", [order.pk], default=0))
    if booked >= 0:
        return None
    entry = await q.insert(db, "ledger_entries", {
        "store_id": store.pk, "kind": "platform_fee", "amount_minor": -booked, "currency": order.currency, "order_id": order.pk,
        "refund_id": refund.pk, "description": "Platform fee reversed — order fully refunded", "metadata": {"reversal_of_minor": booked},
        "occurred_at": when})
    await q.update(db, "refunds", refund.pk, {"platform_fee_reversed": True})
    return entry


async def book_payout(c: Any, *, store: q.Row, payout: q.Row) -> q.Row | None:
    """Record a settlement leaving for the merchant's bank, and the platform's percentage cut on the same order, together."""
    db = await c.db()
    if await q.exists(db, "ledger_entries", {"payout_id": payout.pk}):
        return None
    settings = await c.settings()
    when = q.parse_dt(payout.paid_at) or q.parse_dt(payout.created_at) or datetime.now(UTC)
    entry = await q.insert(db, "ledger_entries", {
        "store_id": store.pk, "kind": "payout", "amount_minor": -abs(payout.amount_minor), "currency": payout.currency,
        "order_id": payout.order_id, "payout_id": payout.pk, "description": f"Payout to {payout.destination or payout.provider}",
        "metadata": {"provider": payout.provider, "reference": payout.provider_reference}, "occurred_at": when})
    if payout.platform_fee_minor:
        await q.insert(db, "ledger_entries", {
            "store_id": store.pk, "kind": "platform_fee", "amount_minor": -abs(payout.platform_fee_minor), "currency": payout.currency,
            "order_id": payout.order_id, "payout_id": payout.pk, "description": "Platform fee",
            "metadata": {"rate": "percent", "configured_percent": settings.platform_split_percent}, "occurred_at": when})
    return entry


async def book_adjustment(c: Any, *, store: q.Row, amount_minor: int, description: str, actor_id: str | None = None,
                          order: q.Row | None = None, metadata: dict[str, Any] | None = None) -> q.Row:
    """A manual correction: the only signed write a human can cause, always with a reason."""
    db = await c.db()
    return await q.insert(db, "ledger_entries", {
        "store_id": store.pk, "kind": "adjustment", "amount_minor": amount_minor, "currency": store.currency,
        "order_id": order.pk if order else None, "description": description, "metadata": {**(metadata or {}), "actor_id": actor_id},
        "occurred_at": datetime.now(UTC)})


async def balance(db: Any, store: q.Row, *, since: datetime | None = None, until: datetime | None = None) -> Money:
    where, params = ["store_id = ?", "deleted_at IS NULL"], [store.pk]
    if since:
        where.append("occurred_at >= ?")
        params.append(since)
    if until:
        where.append("occurred_at < ?")
        params.append(until)
    total = int(await db.scalar(f"SELECT COALESCE(SUM(amount_minor), 0) FROM ledger_entries WHERE {' AND '.join(where)}", params, default=0))
    return Money(total, store.currency)


async def financial_summary(db: Any, store: q.Row, *, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    """The finance dashboard's numbers, in one grouped query, as formatted props."""
    where, params = ["store_id = ?", "deleted_at IS NULL"], [store.pk]
    if since:
        where.append("occurred_at >= ?")
        params.append(since)
    if until:
        where.append("occurred_at < ?")
        params.append(until)
    rows = await db.fetch(f"SELECT kind, COALESCE(SUM(amount_minor), 0) AS total FROM ledger_entries WHERE {' AND '.join(where)} GROUP BY kind", params)
    totals = {r["kind"]: int(r["total"] or 0) for r in rows}
    gross, provider_fees, platform_fees = totals.get("charge", 0), -totals.get("provider_fee", 0), -totals.get("platform_fee", 0)
    refunds, payouts, adjustments = -totals.get("refund", 0), -totals.get("payout", 0), totals.get("adjustment", 0)
    net = gross - provider_fees - platform_fees - refunds + adjustments
    cur = store.currency
    return {"gross": Money(gross, cur).as_prop(), "refunds": Money(refunds, cur).as_prop(), "provider_fees": Money(provider_fees, cur).as_prop(),
            "platform_fees": Money(platform_fees, cur).as_prop(), "adjustments": Money(adjustments, cur).as_prop(), "net": Money(net, cur).as_prop(),
            "paid_out": Money(payouts, cur).as_prop(), "awaiting_payout": Money(net - payouts, cur).as_prop()}
