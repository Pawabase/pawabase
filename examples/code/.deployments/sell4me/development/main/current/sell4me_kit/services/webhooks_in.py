"""Inbound provider webhooks, handled exactly once.

*A webhook delivered twice must never create two orders or charge a merchant twice.* Gates, in order:
the **signature** (Pawabase verifies the inbound hook's HMAC before this runs), **uniqueness**
(``webhook_events`` unique on ``(provider, event_id)``; the insert is the lock), and **idempotent handlers**
(``settle``/``mark_paid`` are safe to run twice). The endpoint answers 200 to anything it stored, including
events it ignores, because a provider that gets a 500 retries for days.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .. import q
from ..payments import get_provider
from ..payments.base import WebhookEvent
from . import ledger, payments


async def handle(c: Any, *, store: q.Row, provider_key: str, event: WebhookEvent) -> dict[str, Any]:
    db = await c.db()
    try:
        record = await q.insert(db, "webhook_events", {"provider": provider_key, "event_id": event.event_id, "event_type": event.kind,
                                                       "store_id": store.pk, "payload": event.payload, "status": "received", "attempts": 1})
    except Exception as error:  # the unique (provider, event_id) index refused a second delivery
        if "unique" not in str(error).lower() and "duplicate" not in str(error).lower():
            raise
        existing = await q.first(db, "webhook_events", {"provider": provider_key, "event_id": event.event_id})
        return {"received": True, "duplicate": True, "status": existing.status if existing else "processed"}
    handler = _HANDLERS.get(event.kind)
    if event.kind == "ignored" or handler is None:
        await q.update(db, "webhook_events", record.pk, {"status": "ignored", "processed_at": datetime.now(UTC)})
        return {"received": True, "handled": False}
    try:
        await handler(c, store, provider_key, event)
    except Exception as error:  # noqa: BLE001
        message = f"{type(error).__name__}: {error}"[:2000]
        await q.update(db, "webhook_events", record.pk, {"status": "failed", "error": message})
        return {"received": True, "handled": False, "error": message}  # 200 anyway: see the module docstring
    await q.update(db, "webhook_events", record.pk, {"status": "processed", "processed_at": datetime.now(UTC)})
    return {"received": True, "handled": True}


async def _payment_for(db: Any, store: q.Row, provider_key: str, reference: str | None) -> q.Row | None:
    """The payment a delivery is about. A provider names a charge by *our* reference (Paystack's ``data.reference``) or by its own id, and the id only
    replaces the access code on the payment row once the charge has been verified, so every one of them has to be tried."""
    if not reference:
        return None
    payment = await q.first(db, "payments", {"store_id": store.pk, "reference": reference})
    payment = payment or await q.first(db, "payments", {"provider": provider_key, "provider_reference": reference})
    return payment or await q.first(db, "payments", {"store_id": store.pk, "provider_reference": reference})


async def _on_payment(c: Any, store: q.Row, provider_key: str, event: WebhookEvent) -> None:
    """Confirm a charge. The payload is *not* trusted for the amount: it says which charge to look at, and the provider's API says what it is."""
    db = await c.db()
    provider, credentials, _ = await payments.credentials_for(c, store, provider_key)
    payment = await _payment_for(db, store, provider_key, event.provider_reference)
    if payment is None:
        return  # not ours: another integration on the same endpoint
    result = await provider.verify(credentials=credentials, reference=payment.reference)
    await payments.settle(c, store=store, provider_key=provider_key, result=result)


async def _on_refund_succeeded(c: Any, store: q.Row, provider_key: str, event: WebhookEvent) -> None:
    """Mark a refund settled and book it; a refund made from the provider's own dashboard is recorded so both dashboards agree."""
    db = await c.db()
    refund = await q.first(db, "refunds", {"store_id": store.pk, "provider_reference": str(event.provider_reference)})
    if refund is None:
        payment = await _payment_for(db, store, provider_key, event.provider_reference)
        if payment is None or not event.amount_minor:
            return
        order = await q.get(db, "orders", payment.order_id)
        if order is None:
            return
        refund = await q.insert(db, "refunds", {
            "store_id": store.pk, "order_id": order.pk, "payment_id": payment.pk, "reference": payments.make_reference("ref"), "provider": provider_key,
            "provider_reference": str(event.provider_reference), "amount_minor": event.amount_minor, "currency": event.currency or payment.currency,
            "reason": "Refunded from the provider's dashboard", "status": "succeeded", "restock": False, "line_items": [],
            "platform_fee_reversed": False, "processed_at": datetime.now(UTC)})
        await payments.apply_refund(c, store=store, order=order, payment=payment, refund=refund, restock=False, actor_id=None)
        return
    if refund.status == "succeeded":
        return
    await q.update(db, "refunds", refund.pk, {"status": "succeeded", "processed_at": datetime.now(UTC)})
    refund = await q.get(db, "refunds", refund.pk)
    order, payment = await q.get(db, "orders", refund.order_id), await q.get(db, "payments", refund.payment_id)
    if order and payment:
        await payments.apply_refund(c, store=store, order=order, payment=payment, refund=refund, restock=bool(refund.restock), actor_id=None)


async def _on_payout(c: Any, store: q.Row, provider_key: str, event: WebhookEvent) -> None:
    """Record a settlement the provider made to the merchant (one row and one ledger entry, however often it is delivered)."""
    db = await c.db()
    data = (event.payload.get("data") or {}).get("object") or event.payload.get("data") or {}
    paid = event.kind == "payout.paid"
    payout, created = await q.get_or_create(db, "payouts", {"provider": provider_key, "provider_reference": str(event.provider_reference)}, {
        "store_id": store.pk, "amount_minor": event.amount_minor or 0, "currency": event.currency or store.currency, "platform_fee_minor": 0,
        "status": "paid" if paid else "failed", "destination": str(data.get("destination") or "")[:200] or None,
        "paid_at": datetime.now(UTC) if paid else None, "raw_response": event.payload})
    if not created and payout.status != ("paid" if paid else "failed"):
        await q.update(db, "payouts", payout.pk, {"status": "paid" if paid else "failed", "paid_at": datetime.now(UTC) if paid else None})
        payout = await q.get(db, "payouts", payout.pk)
    if paid:
        await ledger.book_payout(c, store=store, payout=payout)
        from ..events import emit

        await emit(c, "payout.paid", store=store, payout=payout)


_HANDLERS = {"payment.succeeded": _on_payment, "payment.failed": _on_payment, "refund.succeeded": _on_refund_succeeded,
             "payout.paid": _on_payout, "payout.failed": _on_payout}
