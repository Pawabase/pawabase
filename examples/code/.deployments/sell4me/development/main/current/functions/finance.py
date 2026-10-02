"""Money: the financial dashboard, transactions, providers, payouts and fees.

Every figure comes from the ledger, not from the orders table: the ledger is the book of record and the only place fees are represented at
all (reading revenue from orders and fees from somewhere else is how two screens end up disagreeing). The provider-connection endpoints hold
two rules: a secret key is encrypted before it touches the database and is never sent back (the form shows a mask, and submitting the mask
unchanged leaves the stored key alone), and "connect" saves the keys *and then makes a live call*, so a merchant cannot go live on a typo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.audit import record_from
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import bad_request, not_found, unprocessable
from sell4me_kit.money import Money
from sell4me_kit.payments import ProviderError, get_provider, provider_catalogue
from sell4me_kit.secrets_box import encrypt_secret, mask_secret
from sell4me_kit.services import analytics, ledger, onboarding, payments, payouts

MASK_MARKER = "…"  # what the form sends back when the merchant did not retype a secret


def _range(c: Ctx) -> tuple[Any, Any, str, str]:
    key = c.arg("range", "30d")
    since, until, label = analytics.range_bounds(key, start=c.arg("start"), end=c.arg("end"))
    return since, until, label, key


async def _connected(db: Any, store_id: int) -> list[dict[str, Any]]:
    return [{"provider": a.provider, "label": get_provider(a.provider).label, "is_default": a.is_default, "is_test_mode": a.is_test_mode}
            for a in await q.find(db, "payment_provider_accounts", {"store_id": store_id, "status": "connected"})]


@endpoint("finance.overview", "GET", "/dash/{store}/payments", area="finance", permission="payments.read", summary="The financial dashboard: ledger summary, payment counts, series, recent entries and payouts",
          original="GET /payments")
async def finance_overview(c: Ctx):
    await c.dashboard("payments.read")
    db, store = await c.db(), c.store
    since, until, label, key = _range(c)
    counts = {}
    for name, sql in (("succeeded", "status = 'succeeded'"), ("failed", "status = 'failed'"), ("pending", "status IN ('pending', 'processing')"), ("refunded", "status = 'refunded'")):
        counts[name] = int(await db.scalar(f"SELECT COUNT(*) FROM payments WHERE store_id = ? AND created_at >= ? AND created_at < ? AND deleted_at IS NULL AND {sql}", [store.pk, since, until], default=0))
    recent = [{"id": e["id"], "kind": e["kind"], "description": e["description"], "amount": Money(e["amount_minor"], e["currency"]).as_prop(), "order_number": e["order_number"],
               "order_id": e["order_id"], "occurred_at": e["occurred_at"]}
              for e in await db.fetch("SELECT e.*, o.number AS order_number FROM ledger_entries e LEFT JOIN orders o ON o.id = e.order_id WHERE e.store_id = ? AND e.deleted_at IS NULL ORDER BY e.id DESC LIMIT 25", [store.pk])]
    payout_rows = [{"id": p.pk, "provider": p.provider, "amount": Money(p.amount_minor, p.currency).as_prop(), "status": p.status, "destination": p.destination, "paid_at": p.paid_at}
                   for p in await q.find(db, "payouts", {"store_id": store.pk}, order="id DESC", limit=10)]
    return {"range": {"key": key, "label": label}, "summary": await ledger.financial_summary(db, store, since=since, until=until), "counts": counts,
            "providers": await _connected(db, store.pk), "series": await analytics.series(db, store=store, since=since, until=until), "recent": recent, "payouts": payout_rows}


@endpoint("finance.transactions", "GET", "/dash/{store}/payments/transactions", area="finance", permission="payments.read", summary="Transactions: status tabs, search, paging",
          original="GET /payments/transactions")
async def finance_transactions(c: Ctx):
    await c.dashboard("payments.read")
    db, store = await c.db(), c.store
    status, search = c.arg("status", "all"), (c.arg("q") or "").strip()
    page, per_page = c.page_params(25)
    where, params = ["p.store_id = ?", "p.deleted_at IS NULL"], [store.pk]
    if status != "all":
        where.append("p.status = ?")
        params.append(status)
    if search:
        where.append("(LOWER(p.reference) LIKE ? OR LOWER(p.provider_reference) LIKE ? OR LOWER(COALESCE(o.email, '')) LIKE ?)")
        params += [f"%{search.lower()}%"] * 3
    clause = " AND ".join(where)
    total = int(await db.scalar(f"SELECT COUNT(*) FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE {clause}", params, default=0))
    rows = await db.fetch(f"SELECT p.*, o.number AS order_number, o.email AS order_email FROM payments p LEFT JOIN orders o ON o.id = p.order_id WHERE {clause} "
                          f"ORDER BY p.id DESC LIMIT {per_page} OFFSET {(page - 1) * per_page}", params)
    data = []
    for r in rows:
        cur, net = r["currency"], (r["amount_minor"] or 0) - (r["provider_fee_minor"] or 0) - (r["platform_fee_minor"] or 0) - (r["refunded_minor"] or 0)
        data.append({"id": r["id"], "reference": r["reference"], "provider": r["provider"], "provider_reference": r["provider_reference"], "order_id": r["order_id"],
                     "order_number": r["order_number"], "email": r["order_email"], "status": r["status"], "amount": Money(r["amount_minor"], cur).as_prop(),
                     "provider_fee": Money(r["provider_fee_minor"], cur).as_prop() if r["provider_fee_minor"] is not None else None,
                     "platform_fee": Money(r["platform_fee_minor"] or 0, cur).as_prop(), "refunded": Money(r["refunded_minor"] or 0, cur).as_prop(), "net": Money(net, cur).as_prop(),
                     "method": r["method"], "card": f"{r['card_brand']} ···· {r['card_last4']}" if r["card_last4"] else None, "failure_message": r["failure_message"], "created_at": r["created_at"]})
    counts = {k: await q.count(db, "payments", {"store_id": store.pk} | ({} if k == "all" else {"status": k})) for k in ("all", "succeeded", "failed", "pending", "refunded")}
    return {"data": data, "status": status, "search": search, "counts": counts, "pagination": {"page": page, "per_page": per_page, "total": total, "pages": max(1, -(-total // per_page))}}


@endpoint("finance.refunds", "GET", "/dash/{store}/payments/refunds", area="finance", permission="payments.read", summary="Refunds issued", original="GET /payments/refunds")
async def finance_refunds(c: Ctx):
    await c.dashboard("payments.read")
    rows = await (await c.db()).fetch("SELECT r.*, o.number AS order_number, p.full_name AS actor_name FROM refunds r LEFT JOIN orders o ON o.id = r.order_id "
                                      "LEFT JOIN profiles p ON p.user_id = r.actor_id WHERE r.store_id = ? AND r.deleted_at IS NULL ORDER BY r.id DESC LIMIT 200", [c.store.pk])
    return {"data": [{"id": r["id"], "reference": r["reference"], "order_id": r["order_id"], "order_number": r["order_number"], "amount": Money(r["amount_minor"], r["currency"]).as_prop(),
                      "reason": r["reason"], "status": r["status"], "provider": r["provider"], "platform_fee_reversed": r["platform_fee_reversed"], "actor": r["actor_name"] or "System",
                      "created_at": r["created_at"]} for r in rows]}


@endpoint("finance.payouts", "GET", "/dash/{store}/payments/payouts", area="finance", permission="payouts.read", summary="Settlements to the merchant's bank (platform-initiated, one per confirmed order)",
          original="GET /payments/payouts")
async def finance_payouts(c: Ctx):
    await c.dashboard("payouts.read")
    db, store = await c.db(), c.store
    summary = await ledger.financial_summary(db, store)
    return {"data": [{"id": p.pk, "provider": p.provider, "reference": p.provider_reference, "amount": Money(p.amount_minor, p.currency).as_prop(), "status": p.status,
                      "destination": p.destination, "period_start": p.period_start, "period_end": p.period_end, "expected_at": p.expected_at, "paid_at": p.paid_at,
                      "failure_message": p.failure_message} for p in await q.find(db, "payouts", {"store_id": store.pk}, order="id DESC", limit=100)],
            "awaiting": summary["awaiting_payout"], "paid_out": summary["paid_out"], "providers": await _connected(db, store.pk)}


@endpoint("finance.fees", "GET", "/dash/{store}/payments/fees", area="finance", permission="payments.read",
          summary="Where the money went: platform and provider fees itemised per order (a single total is not a number a merchant can check)", original="GET /payments/fees")
async def finance_fees(c: Ctx):
    await c.dashboard("payments.read")
    db, store = await c.db(), c.store
    since, until, label, key = _range(c)
    rows = await db.fetch("SELECT e.*, o.number AS order_number FROM ledger_entries e LEFT JOIN orders o ON o.id = e.order_id WHERE e.store_id = ? AND e.kind IN ('platform_fee', 'provider_fee') "
                          "AND e.occurred_at >= ? AND e.occurred_at < ? AND e.deleted_at IS NULL ORDER BY e.id DESC LIMIT 500", [store.pk, since, until])
    return {"range": {"key": key, "label": label}, "summary": await ledger.financial_summary(db, store, since=since, until=until),
            "entries": [{"id": e["id"], "kind": e["kind"], "description": e["description"],
                         "amount": Money(abs(e["amount_minor"]), e["currency"]).as_prop(),  # displayed unsigned: a negative number under a "fee" column reads as a refund of one
                         "signed_minor": e["amount_minor"], "order_id": e["order_id"], "order_number": e["order_number"], "occurred_at": e["occurred_at"]} for e in rows]}


@endpoint("finance.ledger", "GET", "/dash/{store}/payments/ledger", area="finance", permission="payments.read", summary="The ledger entries (every movement of money), paged",
          original="the ledger behind GET /payments")
async def finance_ledger(c: Ctx):
    await c.dashboard("payments.read")
    db, store = await c.db(), c.store
    page, per_page = c.page_params(50, 200)
    kind = c.arg("kind")
    where, params = ["e.store_id = ?", "e.deleted_at IS NULL"], [store.pk]
    if kind:
        where.append("e.kind = ?")
        params.append(kind)
    total = int(await db.scalar(f"SELECT COUNT(*) FROM ledger_entries e WHERE {' AND '.join(where)}", params, default=0))
    rows = await db.fetch(f"SELECT e.*, o.number AS order_number FROM ledger_entries e LEFT JOIN orders o ON o.id = e.order_id WHERE {' AND '.join(where)} ORDER BY e.id DESC LIMIT {per_page} OFFSET {(page - 1) * per_page}", params)
    return {"data": [{"id": e["id"], "kind": e["kind"], "description": e["description"], "amount": Money(e["amount_minor"], e["currency"]).as_prop(), "order_id": e["order_id"],
                      "order_number": e["order_number"], "occurred_at": e["occurred_at"]} for e in rows], "total": total, "page": page, "per_page": per_page}


# ── providers ────────────────────────────────────────────────────────────

async def _accounts(c: Ctx) -> list[dict[str, Any]]:
    """The store's connections with every secret masked: ``secret_key_encrypted`` is never decrypted for this view."""
    return [{"provider": a.provider, "label": a.label, "status": a.status, "status_message": a.status_message, "is_default": a.is_default, "is_test_mode": a.is_test_mode,
             "public_key": a.public_key, "has_secret": bool(a.secret_key_encrypted), "has_webhook_secret": bool(a.webhook_secret_encrypted),
             "secret_mask": mask_secret(a.public_key) if a.public_key else None, "account_id": a.provider_account_id, "capabilities": a.capabilities or {},
             "last_verified_at": a.last_verified_at, "webhook_path": f"/hooks/v1/{a.provider}"}
            for a in await q.find(await c.db(), "payment_provider_accounts", {"store_id": c.store.pk}, order="provider")]


@endpoint("finance.providers", "GET", "/dash/{store}/payments/providers", area="finance", permission="payments.read", summary="Payment providers: the catalogue and this store's (masked) connections",
          original="GET /payments/providers")
async def finance_providers(c: Ctx):
    await c.dashboard("payments.read")
    return {"catalogue": provider_catalogue(), "accounts": await _accounts(c)}


async def _verify(c: Ctx, account: q.Row, provider_key: str) -> dict[str, Any]:
    db, store = await c.db(), c.store
    try:
        provider, credentials, _ = await payments.credentials_for(c, store, provider_key)
        info = await provider.account(credentials=credentials)
    except (payments.PaymentError, ProviderError) as error:
        await q.update(db, "payment_provider_accounts", account.pk, {"status": "error", "status_message": str(error)[:500]})
        raise unprocessable(f"Could not verify: {error}", "provider_error") from error
    if not info.connected:
        message = info.message or "The provider rejected these keys."
        await q.update(db, "payment_provider_accounts", account.pk, {"status": "error", "status_message": message})
        raise unprocessable(message, "provider_rejected")
    fields: dict[str, Any] = {"status": "connected", "status_message": info.message, "provider_account_id": info.account_id,
                              "capabilities": {"display_name": info.display_name, "currencies": list(info.currencies), "payouts_enabled": info.payouts_enabled,
                                               "charges_enabled": info.charges_enabled, "country": info.country}, "last_verified_at": datetime.now(UTC)}
    if not await q.exists(db, "payment_provider_accounts", {"store_id": store.pk, "is_default": True, "id": q.ne(account.pk)}):
        fields["is_default"] = True  # the first connected provider becomes the default, so checkout has one without a second choice
    await q.update(db, "payment_provider_accounts", account.pk, fields)
    await onboarding.complete_step(c, store, "payment_provider")
    out = {"provider": provider_key, "status": "connected", "message": f"{provider.label} is connected."}
    if not info.payouts_enabled:
        out["warning"] = (f"{provider.label} is connected, but payouts are not enabled yet: finish verification in their dashboard or you can take payments without being paid.")
    return out


@endpoint("finance.provider_connect", "POST", "/dash/{store}/payments/providers/{provider_key}/connect", area="finance", permission="settings.update",
          summary="Save a provider's credentials, then prove they work", fields=[{"name": "label", "type": "string"}, {"name": "is_test_mode", "type": "boolean"}, {"name": "public_key", "type": "string"},
                                                                              {"name": "secret_key", "type": "string"}, {"name": "webhook_secret", "type": "string"}],
          original="POST /payments/providers/{key}/connect")
async def provider_connect(c: Ctx):
    """A key the merchant did not retype arrives as the mask and is skipped, rather than overwriting the stored value with ``sk_live_…4dQ2``."""
    await c.dashboard("settings.update")
    db, store, data = await c.db(), c.store, c.input
    key = c.params.get("provider_key") or data.get("provider_key")
    try:
        provider = get_provider(key)
    except ProviderError:
        raise bad_request("Unknown provider.", "unknown_provider") from None
    settings = await c.settings()
    account = await q.first(db, "payment_provider_accounts", {"store_id": store.pk, "provider": key})
    if account is None:
        account = await q.insert(db, "payment_provider_accounts", {"store_id": store.pk, "provider": key, "status": "pending", "capabilities": {}, "is_test_mode": True, "is_default": False})
    fields: dict[str, Any] = {"label": (data.get("label") or "").strip() or provider.label, "is_test_mode": bool(data.get("is_test_mode", True)), "status": "pending", "status_message": None}
    if "public_key" in data:
        fields["public_key"] = (data.get("public_key") or "").strip() or None
    for name, column in (("secret_key", "secret_key_encrypted"), ("webhook_secret", "webhook_secret_encrypted")):
        value = (data.get(name) or "").strip()
        if value and MASK_MARKER not in value:  # a masked value means "unchanged"
            fields[column] = encrypt_secret(value, settings.secret_key)
    await q.update(db, "payment_provider_accounts", account.pk, fields)
    await record_from(c, action="provider.connected", resource_type="payment_provider", resource_id=account.pk, summary=f"Connected {provider.label}")
    return await _verify(c, await q.get(db, "payment_provider_accounts", account.pk), key)


@endpoint("finance.provider_verify", "POST", "/dash/{store}/payments/providers/{provider_key}/verify", area="finance", permission="settings.update", summary="Re-check a connected provider's keys",
          original="POST /payments/providers/{key}/verify")
async def provider_verify(c: Ctx):
    await c.dashboard("settings.update")
    key = c.params.get("provider_key")
    account = await q.first(await c.db(), "payment_provider_accounts", {"store_id": c.store.pk, "provider": key})
    if account is None:
        raise not_found("That provider")
    return await _verify(c, account, key)


@endpoint("finance.provider_disconnect", "POST", "/dash/{store}/payments/providers/{provider_key}/disconnect", area="finance", permission="settings.update",
          summary="Remove a provider's credentials (the row stays: payments point at it)", original="POST /payments/providers/{key}/disconnect")
async def provider_disconnect(c: Ctx):
    """Every secret is cleared, so disconnecting genuinely removes the platform's ability to act as the merchant."""
    await c.dashboard("settings.update")
    db = await c.db()
    key = c.params.get("provider_key")
    account = await q.first(db, "payment_provider_accounts", {"store_id": c.store.pk, "provider": key})
    if account is None:
        raise not_found("That provider")
    await q.update(db, "payment_provider_accounts", account.pk, {"secret_key_encrypted": None, "webhook_secret_encrypted": None, "public_key": None, "status": "disconnected",
                                                                 "status_message": None, "is_default": False})
    await record_from(c, action="provider.disconnected", resource_type="payment_provider", resource_id=account.pk, summary=f"Disconnected {key}")
    return {"provider": key, "status": "disconnected"}


@endpoint("finance.provider_default", "POST", "/dash/{store}/payments/providers/{provider_key}/default", area="finance", permission="settings.update", summary="Make a connected provider the default",
          original="POST /payments/providers/{key}/default")
async def provider_default(c: Ctx):
    await c.dashboard("settings.update")
    db = await c.db()
    key = c.params.get("provider_key")
    account = await q.first(db, "payment_provider_accounts", {"store_id": c.store.pk, "provider": key, "status": "connected"})
    if account is None:
        raise unprocessable("Connect that provider first.", "not_connected")
    await q.update_where(db, "payment_provider_accounts", {"store_id": c.store.pk}, {"is_default": False})
    await q.update(db, "payment_provider_accounts", account.pk, {"is_default": True})
    return {"provider": key, "is_default": True, "message": f"{get_provider(key).label} is now the default."}


# ── payout bank account (Paystack subaccount + transfer recipient) ───────

@endpoint("payouts.banks", "GET", "/dash/{store}/payments/payouts/connect", area="finance", permission="payments.read", summary="The bank list and this store's connected payout account",
          original="GET /payments/payouts/connect")
async def payouts_connect_info(c: Ctx):
    await c.dashboard("payments.read")
    account = await q.first(await c.db(), "payment_provider_accounts", {"store_id": c.store.pk, "provider": "paystack"})
    try:
        banks, error = await payouts.list_banks(c), None
    except payouts.PayoutError as e:
        banks, error = [], f"Could not load the bank list: {e}"
    return {"banks": banks, "bank_error": error,
            "account": None if account is None else {"status": account.status, "status_message": account.status_message, "bank_name": account.bank_name,
                                                      "account_name": account.account_name, "account_number_last4": account.account_number_last4, "is_connected": account.is_connected}}


@endpoint("payouts.connect", "POST", "/dash/{store}/payments/payouts/connect", area="finance", permission="settings.update",
          summary="Connect the bank account payouts are sent to (resolved, then a Paystack subaccount and transfer recipient are created)",
          fields=[{"name": "business_name", "type": "string"}, {"name": "bank_code", "type": "string", "required": True}, {"name": "bank_name", "type": "string"},
                  {"name": "account_number", "type": "string", "required": True}], original="POST /payments/payouts/connect")
async def payouts_connect(c: Ctx):
    await c.dashboard("settings.update")
    data = c.input
    business, bank_code, bank_name, number = ((data.get(k) or "").strip() for k in ("business_name", "bank_code", "bank_name", "account_number"))
    if not bank_code or not number:
        raise unprocessable("Choose a bank and enter the account number.", "validation_failed")
    if not number.isdigit() or not 8 <= len(number) <= 10:
        raise unprocessable("That doesn't look like an account number.", "validation_failed", {"account_number": "That doesn't look like an account number."})
    try:
        account = await payouts.connect_store(c, c.store, business_name=business, bank_code=bank_code, bank_name=bank_name, account_number=number)
    except payouts.PayoutError as error:
        raise unprocessable(str(error), "payout_connect_failed") from error
    await record_from(c, action="provider.connected", resource_type="payment_provider", resource_id=account.pk, summary="Connected Paystack payouts")
    return {"status": account.status, "account_name": account.account_name, "bank_name": account.bank_name, "account_number_last4": account.account_number_last4,
            "message": f"Payouts connected — {account.account_name}, {account.bank_name} •••• {account.account_number_last4}."}
