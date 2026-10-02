"""The payment provider contract.

Orders know nothing about Stripe or Paystack. They ask a
:class:`PaymentProvider` to start a charge and are handed back somewhere to
send the customer; later, a webhook or a verification call tells them what
happened. Adding a provider means writing one class and registering it — no
branch anywhere else changes.

Six operations, and each exists because the lifecycle genuinely needs it:

===================  =========================================================
`initialize`         start a charge, get somewhere to send the customer
`verify`             ask the provider what happened, by reference
`refund`             give money back
`parse_webhook`      authenticate an inbound event and normalise it
`account`            what the merchant's connected account can do
`transactions`       list charges, for reconciliation
===================  =========================================================

Two rules the implementations must hold to, because the rest of the platform
assumes them:

* **Amounts are minor units, always.** Providers disagree — Stripe wants cents,
  Paystack wants kobo, and both call the field "amount". The conversion, if any,
  happens inside the provider class and nowhere else.
* **No card data.** Every provider here uses a hosted or tokenised flow. A
  provider implementation that accepted a PAN would put this platform in PCI
  scope, and there is no code path that would carry one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "AccountInfo",
    "ChargeResult",
    "PaymentProvider",
    "ProviderError",
    "RefundResult",
    "WebhookEvent",
]


class ProviderError(Exception):
    """A provider refused, or could not be reached.

    Carries the provider's own code when there is one, so a decline
    (`card_declined`) can be shown to the customer while an outage
    (`connection_error`) is retried instead.
    """

    def __init__(
        self, message: str, *, code: str | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(slots=True)
class ChargeResult:
    """What starting or checking a charge produced.

    `status` is the normalised one — `pending`, `processing`, `succeeded`,
    `failed`, `cancelled` — not the provider's own vocabulary. Translating at
    the boundary is what keeps `Payment.status` meaning one thing.
    """

    reference: str
    """Our reference, echoed back so a result can be matched to an attempt."""

    provider_reference: str
    """The provider's identifier for the charge."""

    status: str
    amount_minor: int
    currency: str

    redirect_url: str | None = None
    """Where to send the customer to pay. `None` once the charge is settled."""

    fee_minor: int | None = None
    """What the provider kept. `None` until they report it — never estimated,
    because an estimated fee in the ledger is a wrong number that looks right."""

    method: str | None = None
    card_brand: str | None = None
    card_last4: str | None = None

    failure_code: str | None = None
    failure_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


@dataclass(slots=True)
class RefundResult:
    reference: str
    provider_reference: str | None
    status: str
    amount_minor: int
    currency: str
    failure_message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status in ("succeeded", "pending")


@dataclass(slots=True)
class WebhookEvent:
    """An inbound provider event, authenticated and normalised.

    `event_id` is the provider's own delivery id and is what makes handling
    idempotent — it is the unique key on the `webhook_events` table, so a
    retried delivery inserts nothing and re-runs nothing.

    `kind` is normalised to the platform's vocabulary — `payment.succeeded`,
    `payment.failed`, `refund.succeeded`, `payout.paid` — so the dispatcher in
    `app/services/webhooks_in.py` is a dict lookup rather than a chain of
    provider-specific branches.
    """

    event_id: str
    kind: str
    provider_reference: str | None
    amount_minor: int | None = None
    currency: str | None = None
    fee_minor: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AccountInfo:
    """What a connected account is and what it can do.

    Shown on the Providers screen. `payouts_enabled` is the one that matters
    operationally: a merchant whose provider has not finished verifying them
    can take payments and not be paid, and they should learn that here rather
    than a week later.
    """

    connected: bool
    display_name: str | None = None
    account_id: str | None = None
    currencies: tuple[str, ...] = ()
    payouts_enabled: bool = False
    charges_enabled: bool = False
    country: str | None = None
    message: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderCredentials:
    """What a provider needs to act as one merchant.

    Handed in per call rather than held on the provider instance: one process
    serves every store, and a provider object holding one store's keys would be
    a tenant boundary made of luck.
    """

    public_key: str | None = None
    secret_key: str | None = None
    webhook_secret: str | None = None
    account_id: str | None = None
    test_mode: bool = True
    #: Where the provider's API lives, when it is not the public one (a test double, a regional endpoint).
    base_url: str | None = None


class PaymentProvider(ABC):
    """One payment provider, for any merchant.

    Instances are stateless and shared. Everything store-specific arrives as
    :class:`ProviderCredentials` on each call.
    """

    #: The key this provider registers under, and the value stored in
    #: `Payment.provider`. Never shown to a customer.
    key: str = ""

    #: What the merchant sees on the Providers screen.
    label: str = ""
    description: str = ""

    #: Currencies this provider can settle. Checkout filters on it, so a
    #: merchant is not offered a provider that would reject their currency at
    #: the last step.
    currencies: tuple[str, ...] = ()

    #: Whether the merchant supplies keys at all. The sandbox provider does not.
    requires_credentials: bool = True

    #: The fields the connect form renders, as `(name, label, secret?)`.
    credential_fields: tuple[tuple[str, str, bool], ...] = ()

    @abstractmethod
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
        """Begin a charge and return where to send the customer.

        `reference` is ours and must reach the provider, so a webhook can be
        tied back to the order that started it. `return_url` is where the
        provider sends the customer afterwards; it is not evidence of payment,
        and the handler behind it verifies rather than trusting the redirect.
        """

    @abstractmethod
    async def verify(
        self, *, credentials: ProviderCredentials, reference: str
    ) -> ChargeResult:
        """Ask the provider what became of a charge.

        The authoritative answer. Called when the customer returns from the
        provider, and again by the reconciliation job for anything a webhook
        never arrived for.
        """

    @abstractmethod
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
        """Return money to the customer.

        `idempotency_key` is required in practice, not optional: a refund
        retried after a timeout must not send the money twice, and every
        provider here supports the header.
        """

    @abstractmethod
    async def parse_webhook(
        self,
        *,
        credentials: ProviderCredentials,
        body: bytes,
        headers: dict[str, str],
    ) -> WebhookEvent:
        """Authenticate an inbound delivery and normalise it.

        Must verify the signature against the raw body before parsing, and
        raise :class:`ProviderError` if it does not match. An unsigned payload
        that reached the dispatcher would let anyone mark any order paid.
        """

    async def account(self, *, credentials: ProviderCredentials) -> AccountInfo:
        """What this merchant's connected account is, and whether it works.

        Default is "connected if there are credentials", which is honest for a
        provider with no account API. Overriding it is how a provider reports
        `payouts_enabled`.
        """
        return AccountInfo(
            connected=bool(credentials.secret_key) or not self.requires_credentials,
            currencies=self.currencies,
        )

    async def transactions(
        self, *, credentials: ProviderCredentials, limit: int = 50
    ) -> list[ChargeResult]:
        """Recent charges on the provider's side, for reconciliation.

        Empty by default. A provider that cannot list transactions is not
        broken — it just cannot answer "what does your side think happened",
        and the finance screen says so rather than showing an empty table as if
        it were a fact.
        """
        return []

    def supports_currency(self, currency: str) -> bool:
        return not self.currencies or currency.upper() in self.currencies
