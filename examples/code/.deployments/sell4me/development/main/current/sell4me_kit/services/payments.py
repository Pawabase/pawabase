"""Driving a payment through a provider, and recording what happened.

The only module that talks to the provider registry. The invariant everything rests on:
:func:`settle` is **idempotent**. The customer returning from the provider, the provider's webhook and
the reconciliation job all call it for the same charge, and any may arrive twice; N calls have the
effect of one. The gate is an atomic claim on the payment row, so concurrent callers produce one winner.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from .. import q
from ..events import emit
from ..money import Money
from ..payments import ChargeResult, PaymentProvider, ProviderCredentials, ProviderError, get_provider
from ..secrets_box import CredentialError, decrypt_secret
from . import inventory, ledger, orders, risk


class PaymentError(Exception):
    """A payment could not be started, verified or refunded."""


def make_reference(prefix: str = "pay") -> str:
    """Our own reference: random, because it appears in the provider's dashboard and a guessable one would let someone probe."""
    return f"{prefix}_{secrets.token_urlsafe(16)}"


async def credentials_for(c: Any, store: q.Row, provider_key: str) -> tuple[PaymentProvider, ProviderCredentials, q.Row | None]:
    """The provider, its keys for this store, and the account row."""
    db = await c.db()
    settings = await c.settings()
    provider = get_provider(provider_key)
    account = await q.first(db, "payment_provider_accounts", {"store_id": store.pk, "provider": provider_key})
    if not provider.requires_credentials:
        if provider_key == "sandbox" and not settings.sandbox_enabled:
            raise PaymentError("The sandbox provider is disabled in this environment.")
        return provider, ProviderCredentials(test_mode=True), account
    if provider_key == "paystack":
        if not settings.paystack_secret_key or not settings.paystack_public_key:
            raise PaymentError("Paystack is not configured for this platform. Set PAYSTACK_SECRET_KEY and PAYSTACK_PUBLIC_KEY.")
        return provider, ProviderCredentials(public_key=settings.paystack_public_key, secret_key=settings.paystack_secret_key,
                                             account_id=account.provider_account_id if account else None,
                                             test_mode=not settings.app_url.startswith("https://"), base_url=settings.paystack_base_url), account
    if account is None:
        raise PaymentError(f"{provider.label} is not connected for this store.")
    try:
        credentials = ProviderCredentials(public_key=account.public_key, secret_key=decrypt_secret(account.secret_key_encrypted, settings.secret_key),
                                          webhook_secret=decrypt_secret(account.webhook_secret_encrypted, settings.secret_key),
                                          account_id=account.provider_account_id, test_mode=bool(account.is_test_mode))
    except CredentialError as error:
        raise PaymentError(f"{provider.label}'s stored keys could not be read. Reconnect the provider from Payments → Providers.") from error
    return provider, credentials, account


async def start_payment(c: Any, *, store: q.Row, order: q.Row, provider_key: str, return_url: str, cancel_url: str,
                        client_ip: str | None = None, user_agent: str | None = None) -> ChargeResult:
    """Begin a charge and record the attempt (written *before* the provider is called, so a timeout still leaves a trace)."""
    db = await c.db()
    provider, credentials, account = await credentials_for(c, store, provider_key)
    if not provider.supports_currency(order.currency):
        raise PaymentError(f"{provider.label} does not settle {order.currency}.")
    reference = make_reference()
    attempt = await q.insert(db, "payment_attempts", {
        "store_id": store.pk, "order_id": order.pk, "provider": provider_key, "reference": reference, "amount_minor": order.total_minor,
        "currency": order.currency, "status": "pending", "client_ip": client_ip, "user_agent": (user_agent or "")[:500] or None})
    try:
        result = await provider.initialize(credentials=credentials, reference=reference, amount_minor=order.total_minor, currency=order.currency,
                                           email=order.email, return_url=return_url, cancel_url=cancel_url,
                                           metadata={"order_id": str(order.pk), "order_number": str(order.number), "store": store.slug,
                                                     "description": f"{store.name} order #{order.number}"})
    except ProviderError as error:
        await q.update(db, "payment_attempts", attempt.pk, {"status": "failed", "error_code": error.code, "error_message": str(error)[:500]})
        await orders.record_event(db, order, "payment.failed", f"Could not start payment: {error}", data={"provider": provider_key, "code": error.code})
        raise PaymentError(str(error)) from error
    await q.get_or_create(db, "payments", {"provider": provider_key, "provider_reference": result.provider_reference}, {
        "store_id": store.pk, "order_id": order.pk, "provider_account_id": account.pk if account else None, "reference": reference,
        "currency": order.currency, "amount_minor": order.total_minor, "status": "pending", "refunded_minor": 0, "platform_fee_minor": 0,
        "raw_response": {}})
    await q.update(db, "payment_attempts", attempt.pk, {"status": "processing"})
    return result


async def settle(c: Any, *, store: q.Row, provider_key: str, result: ChargeResult, order: q.Row | None = None) -> q.Row | None:
    """Apply a provider's verdict to the order it belongs to. **Idempotent.** ``None`` when the charge is unknown here."""
    db = await c.db()
    payment = await q.first(db, "payments", {"reference": result.reference})
    if payment is None and result.provider_reference:
        payment = await q.first(db, "payments", {"provider": provider_key, "provider_reference": result.provider_reference})
    if payment is None and result.provider_reference:
        payment = await q.first(db, "payments", {"store_id": store.pk, "provider_reference": result.provider_reference})
    if payment is None:
        return None
    if payment.status == "succeeded":
        return payment
    order = order or await q.get(db, "orders", payment.order_id)
    if order is None:
        return payment
    if result.status == "succeeded":
        await _apply_success(c, store=store, order=order, payment=payment, result=result)
    elif result.status in ("failed", "cancelled"):
        await _apply_failure(c, store=store, order=order, payment=payment, result=result)
    else:
        await q.update(db, "payments", payment.pk, {"status": result.status})
    return await q.get(db, "payments", payment.pk)


async def _apply_success(c: Any, *, store: q.Row, order: q.Row, payment: q.Row, result: ChargeResult) -> None:
    db = await c.db()
    now = datetime.now(UTC)
    # The gate: an atomic claim. Whoever flips the status wins; every other concurrent caller stops here.
    claimed = await db.execute("UPDATE payments SET status = 'succeeded' WHERE id = ? AND status <> 'succeeded'", [payment.pk])
    if not claimed:
        return
    fields: dict[str, Any] = {"provider_reference": result.provider_reference or payment.provider_reference,
                              "amount_minor": result.amount_minor or payment.amount_minor, "method": result.method,
                              "card_brand": result.card_brand, "card_last4": result.card_last4, "captured_at": now, "raw_response": result.raw}
    if result.fee_minor is not None:  # only when the provider reported it: an estimate would be a wrong number that looks right
        fields["provider_fee_minor"] = result.fee_minor
    await q.update(db, "payments", payment.pk, fields)
    await db.execute("UPDATE payment_attempts SET status = 'succeeded', payment_id = ? WHERE reference = ?", [payment.pk, payment.reference])
    payment = await q.get(db, "payments", payment.pk)
    await orders.mark_paid(c, store=store, order=order, payment_reference=payment.reference, amount_minor=payment.amount_minor)
    order = await q.get(db, "orders", order.pk)
    await ledger.book_charge(c, store=store, order=order, payment=payment, occurred_at=now)
    await emit(c, "order.paid", store=store, order=order, payment=payment)
    await emit(c, "payment.succeeded", store=store, order=order, payment=payment)


async def _apply_failure(c: Any, *, store: q.Row, order: q.Row, payment: q.Row, result: ChargeResult) -> None:
    db = await c.db()
    claimed = await db.execute("UPDATE payments SET status = 'failed' WHERE id = ? AND status NOT IN ('failed', 'succeeded')", [payment.pk])
    if not claimed:
        return
    await q.update(db, "payments", payment.pk, {"failure_code": result.failure_code, "failure_message": (result.failure_message or "")[:500] or None,
                                                "failed_at": datetime.now(UTC)})
    await db.execute("UPDATE payment_attempts SET status = 'failed', error_code = ?, error_message = ? WHERE reference = ?",
                     [result.failure_code, (result.failure_message or "")[:500] or None, payment.reference])
    await q.update(db, "orders", order.pk, {"payment_status": "failed"})
    order = await q.get(db, "orders", order.pk)
    await orders.record_event(db, order, "payment.failed", result.failure_message or "Payment failed",
                              data={"code": result.failure_code, "provider": payment.provider}, customer_visible=True)
    # A failed payment releases the stock it was holding, so a decline at 3am does not block someone else's purchase.
    for item in await q.find(db, "order_items", {"order_id": order.pk}):
        if item.variant_id:
            await inventory.release_reservation(c, variant_id=item.variant_id, quantity=item.quantity)
    payment = await q.get(db, "payments", payment.pk)
    await risk.record_failure(c, store=store, order=order, payment=payment)
    await emit(c, "payment.failed", store=store, order=order, payment=payment)


async def refund_payment(c: Any, *, store: q.Row, order: q.Row, payment: q.Row, amount_minor: int, reason: str | None = None,
                         line_items: list[dict[str, Any]] | None = None, restock: bool = True, actor_id: str | None = None) -> q.Row:
    """Send money back, and book it. The refundable amount is checked against the payment; the idempotency key is the refund's reference."""
    db = await c.db()
    if payment.status not in ("succeeded", "partially_refunded"):
        raise PaymentError("Only a successful payment can be refunded.")
    refundable = payment.amount_minor - (payment.refunded_minor or 0)
    if amount_minor <= 0:
        raise PaymentError("Refund amount must be positive.")
    if amount_minor > refundable:
        raise PaymentError(f"Only {Money(refundable, payment.currency).format()} of this payment can still be refunded.")
    reference = make_reference("ref")
    refund = await q.insert(db, "refunds", {
        "store_id": store.pk, "order_id": order.pk, "payment_id": payment.pk, "reference": reference, "provider": payment.provider,
        "amount_minor": amount_minor, "currency": payment.currency, "reason": reason, "line_items": line_items or [], "restock": restock,
        "status": "pending", "actor_id": actor_id, "platform_fee_reversed": False})
    provider, credentials, _ = await credentials_for(c, store, payment.provider)
    try:
        result = await provider.refund(credentials=credentials, provider_reference=payment.provider_reference, amount_minor=amount_minor,
                                       currency=payment.currency, reason=reason, idempotency_key=reference)
    except ProviderError as error:
        await q.update(db, "refunds", refund.pk, {"status": "failed", "failure_message": str(error)[:500]})
        raise PaymentError(str(error)) from error
    await q.update(db, "refunds", refund.pk, {"provider_reference": result.provider_reference, "status": result.status, "processed_at": datetime.now(UTC)})
    refund = await q.get(db, "refunds", refund.pk)
    if result.succeeded:
        await apply_refund(c, store=store, order=order, payment=payment, refund=refund, restock=restock, actor_id=actor_id)
    return refund


async def apply_refund(c: Any, *, store: q.Row, order: q.Row, payment: q.Row, refund: q.Row, restock: bool, actor_id: str | None) -> None:
    db = await c.db()
    payment = await q.get(db, "payments", payment.pk)
    refunded = (payment.refunded_minor or 0) + refund.amount_minor
    await q.update(db, "payments", payment.pk, {"refunded_minor": refunded, **({"status": "refunded"} if refunded >= payment.amount_minor else {})})
    order = await q.get(db, "orders", order.pk)
    order_refunded = (order.refunded_minor or 0) + refund.amount_minor
    full = order_refunded >= order.total_minor
    state = "refunded" if full else "partially_refunded"
    await q.update(db, "orders", order.pk, {"refunded_minor": order_refunded, "payment_status": state, "status": state})
    order = await q.get(db, "orders", order.pk)
    if restock:
        for line in refund.line_items or []:
            item = await q.get(db, "order_items", line.get("order_item_id"))
            quantity = int(line.get("quantity") or 0)
            if item is None or not item.variant_id or quantity <= 0:
                continue
            variant = await q.get(db, "product_variants", item.variant_id)
            if variant is None:
                continue
            await inventory.adjust(c, store=store, variant=variant, delta=quantity, reason="returned", note=f"Refund on order #{order.number}",
                                   reference_type="refund", reference_id=refund.pk, actor_id=actor_id)
            await q.increment(db, "order_items", item.pk, quantity_refunded=quantity)
    await ledger.book_refund(c, store=store, order=order, refund=refund)
    await orders.record_event(db, order, "refund.created",
                              f"Refunded {Money(refund.amount_minor, refund.currency).format()}" + (f" — {refund.reason}" if refund.reason else ""),
                              data={"refund_id": refund.pk, "full": full}, actor_id=actor_id, customer_visible=True)
    payment = await q.get(db, "payments", payment.pk)
    await emit(c, "refund.created", store=store, order=order, refund=refund, payment=payment)
