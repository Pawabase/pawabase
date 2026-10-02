"""Connecting a merchant's bank account, and paying them.

Paystack is a marketplace integration: a customer's charge settles to the *platform's* balance, and
this module is the other half: ``connect_store`` (bank details -> Paystack subaccount + transfer
recipient, created once) and ``initiate_payout_for_order`` (order confirmed -> the merchant's share is
transferred out, the platform keeps its percentage). Idempotent per order.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from .. import q
from ..payments import ProviderCredentials, ProviderError, get_provider
from . import onboarding


class PayoutError(Exception):
    """A bank account could not be connected, or a payout could not be sent."""


async def platform_credentials(c: Any) -> ProviderCredentials:
    settings = await c.settings()
    if not settings.paystack_secret_key or not settings.paystack_public_key:
        raise PayoutError("Paystack is not configured for this platform. Set PAYSTACK_SECRET_KEY and PAYSTACK_PUBLIC_KEY.")
    return ProviderCredentials(public_key=settings.paystack_public_key, secret_key=settings.paystack_secret_key,
                               test_mode=not settings.app_url.startswith("https://"), base_url=settings.paystack_base_url)


async def list_banks(c: Any) -> list[dict[str, str]]:
    try:
        return await get_provider("paystack").list_banks(credentials=await platform_credentials(c))
    except ProviderError as error:
        raise PayoutError(str(error)) from error


async def connect_store(c: Any, store: q.Row, *, business_name: str, bank_code: str, bank_name: str, account_number: str) -> q.Row:
    """Connect a store's bank account: resolve the account, create the subaccount (where the platform's cut is recorded), then the recipient.
    Nothing is saved on failure, so a typo does not leave a half-connected row."""
    db = await c.db()
    settings = await c.settings()
    provider, credentials = get_provider("paystack"), await platform_credentials(c)
    try:
        account_name = await provider.resolve_account(credentials=credentials, account_number=account_number, bank_code=bank_code)
        if not account_name:
            raise PayoutError("Could not verify that account number with this bank.")
        subaccount = await provider.create_subaccount(credentials=credentials, business_name=business_name or store.name, bank_code=bank_code,
                                                      account_number=account_number, percentage_charge=settings.platform_split_percent)
        recipient = await provider.create_transfer_recipient(credentials=credentials, name=account_name, account_number=account_number, bank_code=bank_code)
    except ProviderError as error:
        raise PayoutError(str(error)) from error
    account, _ = await q.get_or_create(db, "payment_provider_accounts", {"store_id": store.pk, "provider": "paystack"}, {"status": "pending"})
    fields: dict[str, Any] = {
        "label": business_name or store.name, "provider_account_id": subaccount, "recipient_code": recipient, "bank_code": bank_code,
        "bank_name": bank_name, "account_name": account_name, "account_number_last4": account_number[-4:],
        "is_test_mode": not settings.app_url.startswith("https://"), "status": "connected", "status_message": None,
        "capabilities": {"display_name": account_name, "payouts_enabled": True, "charges_enabled": True}, "last_verified_at": datetime.now(UTC)}
    if not await q.exists(db, "payment_provider_accounts", {"store_id": store.pk, "is_default": True, "id": q.ne(account.pk)}):
        fields["is_default"] = True
    await q.update(db, "payment_provider_accounts", account.pk, fields)
    await onboarding.complete_step(c, store, "payment_provider")
    return await q.get(db, "payment_provider_accounts", account.pk)


def split(percent: float, net_settled_minor: int) -> tuple[int, int]:
    """``(merchant_amount, platform_amount)`` from what actually settled (gross minus the provider's own fee)."""
    platform_amount = round(net_settled_minor * percent / 100)
    return net_settled_minor - platform_amount, platform_amount


async def initiate_payout_for_order(c: Any, order: q.Row) -> q.Row | None:
    """Pay the merchant their share of one order, once confirmed. ``None`` (not an error) when there is nothing to pay yet."""
    db = await c.db()
    existing = await q.first(db, "payouts", {"order_id": order.pk, "provider": "paystack"})
    if existing is not None:
        return existing
    payment = await q.first(db, "payments", {"order_id": order.pk, "provider": "paystack", "status": "succeeded"})
    if payment is None:
        return None
    account = await q.first(db, "payment_provider_accounts", {"store_id": order.store_id, "provider": "paystack", "status": "connected"})
    if account is None or not account.recipient_code:
        return None
    settings = await c.settings()
    merchant_amount, platform_amount = split(settings.platform_split_percent, payment.amount_minor - (payment.provider_fee_minor or 0))
    reference = f"payout_{order.pk}_{secrets.token_urlsafe(8)}"
    payout = await q.insert(db, "payouts", {
        "store_id": order.store_id, "order_id": order.pk, "payment_id": payment.pk, "provider": "paystack", "provider_account_id": account.pk,
        "provider_reference": reference, "amount_minor": merchant_amount, "platform_fee_minor": platform_amount, "currency": payment.currency,
        "status": "pending", "destination": f"{account.bank_name} •••• {account.account_number_last4}"})
    try:
        data = await get_provider("paystack").initiate_transfer(credentials=await platform_credentials(c), recipient_code=account.recipient_code,
                                                                amount_minor=merchant_amount, reference=reference,
                                                                reason=f"Payout for order #{order.number}", currency=payment.currency)
    except ProviderError as error:
        await q.update(db, "payouts", payout.pk, {"status": "failed", "failure_message": str(error)[:500]})
        return await q.get(db, "payouts", payout.pk)
    await q.update(db, "payouts", payout.pk, {"status": "processing", "initiated_at": datetime.now(UTC), "raw_response": data})
    return await q.get(db, "payouts", payout.pk)
