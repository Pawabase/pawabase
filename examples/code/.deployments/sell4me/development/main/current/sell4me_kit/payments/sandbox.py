"""An offline stand-in for Paystack, for the test suite, demos and development.

A real provider implementation, not a mock branch inside the others: it implements the same interface, is registered the same way and is
selected the same way, so the whole lifecycle (checkout, hosted page, verify, order paid, stock committed, ledger booked) runs end to end with
nothing installed and no network. It is refused unless ``SANDBOX_ENABLED`` is on, and that is off in production.

Charges are rows in ``sandbox_charges``, not process memory (the original kept them in a dict). Pawabase runs functions in worker processes
that do not share memory, so a charge started by one request has to be findable by the next. ``settle`` is one conditional UPDATE, so two
callers racing to decide a charge cannot both win.

There is no hosted page: ``redirect_url`` is the API path the storefront renders its own "pay" screen from
(``GET /shop/{store}/sandbox/{provider_reference}``) and decides with ``POST .../settle``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from sell4me_kit import q
from sell4me_kit.context import current_ctx
from sell4me_kit.payments.base import (
    AccountInfo,
    ChargeResult,
    PaymentProvider,
    ProviderCredentials,
    ProviderError,
    RefundResult,
    WebhookEvent,
)

__all__ = ["SandboxProvider", "forced_outcome", "sandbox_charges"]

#: A flat 2.9% + 30c, so the fee columns and the ledger have something realistic to hold.
FEE_PERCENT = 29  # tenths of a percent: 2.9%
FEE_FIXED_MINOR = 30

#: Test amounts that force an outcome, matched on the *last two digits* of the minor amount (a tester writes "$10.01" and gets a decline).
DECLINE_SUFFIX = 1
FAILURE_SUFFIX = 2


@dataclass
class _Charge:
    reference: str
    provider_reference: str
    amount_minor: int
    currency: str
    email: str
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
    refunded_minor: int = 0
    return_url: str = ""
    cancel_url: str = ""
    created_at: Any = None

    @property
    def fee_minor(self) -> int:
        return (self.amount_minor * FEE_PERCENT) // 1000 + FEE_FIXED_MINOR

    @classmethod
    def of(cls, row: q.Row) -> "_Charge":
        return cls(reference=row.reference, provider_reference=row.provider_reference, amount_minor=row.amount_minor, currency=row.currency, email=row.email or "",
                   status=row.status, metadata=row.metadata or {}, refunded_minor=row.refunded_minor or 0, return_url=row.return_url or "", cancel_url=row.cancel_url or "",
                   created_at=row.created_at)


async def _db() -> Any:
    ctx = current_ctx()
    if ctx is None:
        raise ProviderError("The sandbox needs a request context.", code="no_context")
    return await ctx.db()


class _Store:
    """The sandbox's books, in ``sandbox_charges``."""

    async def add(self, charge: _Charge) -> None:
        await q.insert(await _db(), "sandbox_charges", {"reference": charge.reference, "provider_reference": charge.provider_reference, "amount_minor": charge.amount_minor,
                                                       "currency": charge.currency, "email": charge.email, "status": "pending", "metadata": charge.metadata, "refunded_minor": 0,
                                                       "return_url": charge.return_url, "cancel_url": charge.cancel_url})

    async def get(self, provider_reference: str) -> _Charge | None:
        row = await q.first(await _db(), "sandbox_charges", {"provider_reference": provider_reference})
        return _Charge.of(row) if row else None

    async def by_reference(self, reference: str) -> _Charge | None:
        row = await q.first(await _db(), "sandbox_charges", {"reference": reference})
        return _Charge.of(row) if row else None

    async def settle(self, provider_reference: str, outcome: str) -> _Charge:
        db = await _db()
        # One conditional UPDATE: deciding twice changes nothing, and two racing callers cannot both win.
        await db.execute("UPDATE sandbox_charges SET status = ? WHERE provider_reference = ? AND status = 'pending'", [outcome, provider_reference])
        charge = await self.get(provider_reference)
        if charge is None:
            raise ProviderError("No such sandbox charge.", code="not_found")
        return charge

    async def refund(self, provider_reference: str, amount_minor: int) -> _Charge:
        db = await _db()
        charge = await self.get(provider_reference)
        if charge is None:
            raise ProviderError("No such sandbox charge.", code="not_found")
        if charge.status != "succeeded":
            raise ProviderError("Only a succeeded charge can be refunded.", code="invalid_state")
        # The ceiling is enforced inside the UPDATE, so two concurrent partial refunds cannot together exceed the charge.
        changed = await db.execute("UPDATE sandbox_charges SET refunded_minor = refunded_minor + ? WHERE provider_reference = ? AND refunded_minor + ? <= amount_minor",
                                   [amount_minor, provider_reference, amount_minor])
        if not changed:
            raise ProviderError("Refund exceeds the charge amount.", code="amount_too_large")
        return await self.get(provider_reference)  # type: ignore[return-value]

    async def recent(self, limit: int) -> list[_Charge]:
        return [_Charge.of(r) for r in await q.find(await _db(), "sandbox_charges", order="id DESC", limit=limit)]


sandbox_charges = _Store()


class SandboxProvider(PaymentProvider):
    """A local provider with a hosted page, webhooks and refunds."""

    key = "sandbox"
    label = "Sandbox"
    description = (
        "A local test provider. Payments are simulated in this process — no "
        "network, no credentials, no real money. Use it to run the full order "
        "lifecycle before connecting Stripe or Paystack."
    )
    #: Empty means "any currency", which is what a test provider should accept.
    currencies = ()
    requires_credentials = False
    credential_fields = ()

    async def initialize(
        self,
        *,
        credentials: ProviderCredentials,
        reference: str,
        amount_minor: int,
        currency: str,
        email: str,
        return_url: str,
        cancel_url: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChargeResult:
        if amount_minor <= 0:
            raise ProviderError("Amount must be positive.", code="invalid_amount")

        charge = _Charge(
            reference=reference,
            provider_reference=f"sbx_{secrets.token_hex(12)}",
            amount_minor=amount_minor,
            currency=currency.upper(),
            email=email,
            metadata=metadata or {},
            return_url=return_url,
            cancel_url=cancel_url,
        )
        await sandbox_charges.add(charge)

        return ChargeResult(
            reference=reference,
            provider_reference=charge.provider_reference,
            status="pending",
            amount_minor=amount_minor,
            currency=charge.currency,
            # An API path, not a page: the storefront draws its own "pay" screen from it and settles with POST .../settle.
            redirect_url=f"/shop/{(metadata or {}).get('store', '')}/sandbox/{charge.provider_reference}",
            raw={"sandbox": True},
        )

    async def verify(
        self, *, credentials: ProviderCredentials, reference: str
    ) -> ChargeResult:
        charge = await sandbox_charges.by_reference(reference)
        if charge is None:
            raise ProviderError("Unknown reference.", code="not_found")
        return self._result(charge)

    async def refund(
        self,
        *,
        credentials: ProviderCredentials,
        provider_reference: str,
        amount_minor: int,
        currency: str,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> RefundResult:
        charge = await sandbox_charges.refund(provider_reference, amount_minor)
        return RefundResult(
            reference=idempotency_key or secrets.token_hex(8),
            provider_reference=f"sbxr_{secrets.token_hex(10)}",
            status="succeeded",
            amount_minor=amount_minor,
            currency=charge.currency,
            raw={"sandbox": True, "refunded_total": charge.refunded_minor},
        )

    async def parse_webhook(
        self,
        *,
        credentials: ProviderCredentials,
        body: bytes,
        headers: dict[str, str],
    ) -> WebhookEvent:
        """Read a sandbox delivery.

        Signed like a real one — the same HMAC check the other providers do,
        with a fixed development secret — so the verification path is exercised
        rather than skipped. A payload with a bad signature is rejected here
        exactly as Stripe's would be.
        """
        import hashlib
        import hmac
        import json

        signature = headers.get("x-sandbox-signature", "")
        secret = (credentials.webhook_secret or "sandbox-webhook-secret").encode()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ProviderError("Bad sandbox webhook signature.", code="bad_signature")

        payload = json.loads(body.decode())
        return WebhookEvent(
            event_id=payload["id"],
            kind=payload["type"],
            provider_reference=payload["data"].get("provider_reference"),
            amount_minor=payload["data"].get("amount_minor"),
            currency=payload["data"].get("currency"),
            fee_minor=payload["data"].get("fee_minor"),
            payload=payload,
        )

    async def account(self, *, credentials: ProviderCredentials) -> AccountInfo:
        return AccountInfo(
            connected=True,
            display_name="Sandbox account",
            account_id="acct_sandbox",
            currencies=(),
            payouts_enabled=True,
            charges_enabled=True,
            message="Simulated locally. No real money moves.",
        )

    async def transactions(
        self, *, credentials: ProviderCredentials, limit: int = 50
    ) -> list[ChargeResult]:
        return [self._result(charge) for charge in await sandbox_charges.recent(limit)]

    def _result(self, charge: _Charge) -> ChargeResult:
        return ChargeResult(
            reference=charge.reference,
            provider_reference=charge.provider_reference,
            status=charge.status,
            amount_minor=charge.amount_minor,
            currency=charge.currency,
            fee_minor=charge.fee_minor if charge.status == "succeeded" else None,
            method="card",
            card_brand="Sandbox",
            card_last4="4242",
            failure_code="card_declined" if charge.status == "failed" else None,
            failure_message=(
                "The sandbox declined this card." if charge.status == "failed" else None
            ),
            raw={"sandbox": True, "refunded_minor": charge.refunded_minor},
        )


def forced_outcome(amount_minor: int) -> str:
    """The outcome a test amount asks for.

    `…01` declines, `…02` errors, anything else succeeds. Written as a function
    so the hosted page and the tests agree on the convention.
    """
    suffix = amount_minor % 100
    if suffix == DECLINE_SUFFIX:
        return "failed"
    if suffix == FAILURE_SUFFIX:
        return "failed"
    return "succeeded"
