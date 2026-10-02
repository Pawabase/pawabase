"""Stripe, over plain HTTP.

No SDK. Stripe's REST API is small enough for the six operations this platform
needs, and driving it directly means the dependency tree does not carry a
library whose release cadence can break the build, and a sandbox install pulls
in nothing at all.

The flow is **Checkout Sessions** — Stripe hosts the payment page. No card data
reaches this application, and it never enters PCI scope. The merchant's own
secret key is used, so the money settles into the merchant's Stripe balance and
Stripe pays them out directly; the platform is not in the funds flow and takes
its $1 as a record in its own ledger.

Stripe's amounts are already minor units for every currency this platform
supports, so no conversion happens here — but the zero-decimal currencies are
handled explicitly rather than assumed, because sending "1000" for ¥1000 and
for $10.00 means two different things.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from sell4me_kit.money import scale
from sell4me_kit.payments.base import (
    AccountInfo,
    ChargeResult,
    PaymentProvider,
    ProviderCredentials,
    ProviderError,
    RefundResult,
    WebhookEvent,
)

__all__ = ["StripeProvider"]

API = "https://api.stripe.com/v1"

#: How long a webhook signature stays acceptable. Stripe's own guidance, and
#: the reason a replayed delivery from last week is rejected before it reaches
#: the idempotency table.
SIGNATURE_TOLERANCE_SECONDS = 300

#: Stripe's payment_intent statuses, mapped to the platform's five. Anything
#: unlisted is treated as `processing`, which is the safe reading: it means
#: "not yet money", and the reconciliation job will ask again.
_STATUS = {
    "succeeded": "succeeded",
    "processing": "processing",
    "requires_payment_method": "pending",
    "requires_confirmation": "pending",
    "requires_action": "pending",
    "requires_capture": "processing",
    "canceled": "cancelled",
}


class StripeProvider(PaymentProvider):
    key = "stripe"
    label = "Stripe"
    description = (
        "Cards, wallets and bank debits in 40+ countries. Payments settle into "
        "your own Stripe balance and Stripe pays you out directly."
    )
    currencies = ("USD", "EUR", "GBP", "CAD", "AUD", "NGN", "ZAR")
    credential_fields = (
        ("public_key", "Publishable key (pk_…)", False),
        ("secret_key", "Secret key (sk_…)", True),
        ("webhook_secret", "Webhook signing secret (whsec_…)", True),
    )

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._timeout = timeout

    # -- HTTP -------------------------------------------------------------

    async def _post(
        self,
        credentials: ProviderCredentials,
        path: str,
        data: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._secret(credentials)}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # Stripe deduplicates on this header for 24 hours. Without it, a
        # request that times out after Stripe accepted it is indistinguishable
        # from one that never arrived, and retrying charges twice.
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    f"{API}{path}", content=_form(data), headers=headers
                )
            except httpx.HTTPError as error:
                raise ProviderError(
                    f"Could not reach Stripe: {error}", code="connection_error", retryable=True
                ) from error
        return self._read(response)

    async def _get(
        self, credentials: ProviderCredentials, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._secret(credentials)}"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(f"{API}{path}", params=params, headers=headers)
            except httpx.HTTPError as error:
                raise ProviderError(
                    f"Could not reach Stripe: {error}", code="connection_error", retryable=True
                ) from error
        return self._read(response)

    @staticmethod
    def _secret(credentials: ProviderCredentials) -> str:
        if not credentials.secret_key:
            raise ProviderError("Stripe is not connected for this store.", code="no_credentials")
        return credentials.secret_key

    @staticmethod
    def _read(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            raise ProviderError(
                f"Stripe returned a non-JSON response ({response.status_code}).",
                code="bad_response",
                retryable=response.status_code >= 500,
            ) from None
        if response.status_code >= 400:
            error = payload.get("error", {})
            raise ProviderError(
                error.get("message", "Stripe rejected the request."),
                code=error.get("code") or error.get("type"),
                # 5xx and rate limits are worth retrying; a declined card is not.
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        return payload

    # -- the contract -----------------------------------------------------

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
        data: dict[str, Any] = {
            "mode": "payment",
            "success_url": return_url,
            "cancel_url": cancel_url,
            "customer_email": email,
            # Our reference on the session *and* on the resulting intent:
            # the webhook carries the intent, the return URL carries the
            # session, and both have to lead back to the same order.
            "client_reference_id": reference,
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": self._to_stripe(amount_minor, currency),
            "line_items[0][price_data][product_data][name]": (
                (metadata or {}).get("description") or f"Order {reference}"
            ),
            "payment_intent_data[metadata][reference]": reference,
            "metadata[reference]": reference,
        }
        for key, value in (metadata or {}).items():
            if key != "description":
                data[f"metadata[{key}]"] = str(value)

        payload = await self._post(
            credentials, "/checkout/sessions", data, idempotency_key=reference
        )
        return ChargeResult(
            reference=reference,
            # The session id, until an intent exists. `verify` upgrades it.
            provider_reference=payload.get("payment_intent") or payload["id"],
            status="pending",
            amount_minor=amount_minor,
            currency=currency.upper(),
            redirect_url=payload["url"],
            raw=payload,
        )

    async def verify(
        self, *, credentials: ProviderCredentials, reference: str
    ) -> ChargeResult:
        """Find the charge by *our* reference.

        Stripe has no "get by client_reference_id", so this searches sessions.
        Searching rather than storing the session id would be wasteful if the
        caller already had it — and it does, so `app/services/payments.py`
        passes the provider reference through and this is the fallback for
        reconciliation, where all we have is our own reference.
        """
        found = await self._get(
            credentials,
            "/checkout/sessions",
            {"limit": 100},
        )
        for session in found.get("data", []):
            if session.get("client_reference_id") == reference:
                return await self._from_session(credentials, session, reference)
        raise ProviderError("No Stripe session for that reference.", code="not_found")

    async def verify_session(
        self, *, credentials: ProviderCredentials, session_id: str
    ) -> ChargeResult:
        """The path actually taken when a customer returns from Stripe."""
        session = await self._get(credentials, f"/checkout/sessions/{session_id}")
        return await self._from_session(
            credentials, session, session.get("client_reference_id", "")
        )

    async def _from_session(
        self, credentials: ProviderCredentials, session: dict[str, Any], reference: str
    ) -> ChargeResult:
        intent_id = session.get("payment_intent")
        currency = (session.get("currency") or "usd").upper()
        amount = self._from_stripe(session.get("amount_total") or 0, currency)

        if not intent_id:
            return ChargeResult(
                reference=reference,
                provider_reference=session["id"],
                status="pending",
                amount_minor=amount,
                currency=currency,
                raw=session,
            )

        intent = await self._get(
            credentials, f"/payment_intents/{intent_id}", {"expand[]": "latest_charge"}
        )
        charge = intent.get("latest_charge")
        if isinstance(charge, str):
            charge = await self._get(credentials, f"/charges/{charge}")
        charge = charge or {}

        # The fee is on the balance transaction, not the charge, and only once
        # the funds have settled. Null until then — never estimated.
        fee_minor: int | None = None
        balance_transaction = charge.get("balance_transaction")
        if isinstance(balance_transaction, str):
            try:
                balance = await self._get(
                    credentials, f"/balance_transactions/{balance_transaction}"
                )
                fee_minor = self._from_stripe(balance.get("fee") or 0, currency)
            except ProviderError:
                fee_minor = None

        card = (charge.get("payment_method_details") or {}).get("card") or {}
        return ChargeResult(
            reference=reference,
            provider_reference=intent["id"],
            status=_STATUS.get(intent.get("status", ""), "processing"),
            amount_minor=amount,
            currency=currency,
            fee_minor=fee_minor,
            method=(charge.get("payment_method_details") or {}).get("type"),
            card_brand=card.get("brand"),
            card_last4=card.get("last4"),
            failure_code=(intent.get("last_payment_error") or {}).get("code"),
            failure_message=(intent.get("last_payment_error") or {}).get("message"),
            raw={"session": session, "intent": intent},
        )

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
        data: dict[str, Any] = {
            "payment_intent": provider_reference,
            "amount": self._to_stripe(amount_minor, currency),
        }
        # Stripe accepts only three reasons and 400s on anything else, so a
        # merchant's free-text reason goes in metadata and the enum is only set
        # when it actually matches.
        if reason in ("duplicate", "fraudulent", "requested_by_customer"):
            data["reason"] = reason
        elif reason:
            data["metadata[reason]"] = reason[:500]

        payload = await self._post(
            credentials, "/refunds", data, idempotency_key=idempotency_key
        )
        return RefundResult(
            reference=idempotency_key or payload["id"],
            provider_reference=payload["id"],
            status="succeeded" if payload.get("status") == "succeeded" else "pending",
            amount_minor=self._from_stripe(payload.get("amount") or 0, currency),
            currency=currency.upper(),
            raw=payload,
        )

    async def parse_webhook(
        self,
        *,
        credentials: ProviderCredentials,
        body: bytes,
        headers: dict[str, str],
    ) -> WebhookEvent:
        secret = credentials.webhook_secret
        if not secret:
            raise ProviderError(
                "No Stripe webhook secret is configured for this store.",
                code="no_webhook_secret",
            )
        self._verify_signature(body, headers.get("stripe-signature", ""), secret)

        event = json.loads(body.decode())
        obj = (event.get("data") or {}).get("object") or {}
        currency = (obj.get("currency") or "usd").upper()

        return WebhookEvent(
            event_id=event["id"],
            kind=_EVENT_KINDS.get(event.get("type", ""), "ignored"),
            provider_reference=_reference_from(obj),
            amount_minor=self._from_stripe(
                obj.get("amount_received") or obj.get("amount") or obj.get("amount_total") or 0,
                currency,
            ),
            currency=currency,
            payload=event,
        )

    @staticmethod
    def _verify_signature(body: bytes, header: str, secret: str) -> None:
        """Stripe's `t=…,v1=…` scheme, checked against the raw body.

        Two failures this guards against, and both matter: a forged payload
        (the HMAC will not match) and a replayed genuine one (the timestamp is
        outside the tolerance). The body must be the bytes as received — any
        re-serialisation changes the digest and every delivery starts failing.
        """
        parts = dict(
            piece.split("=", 1) for piece in header.split(",") if "=" in piece
        )
        timestamp = parts.get("t")
        signature = parts.get("v1")
        if not timestamp or not signature:
            raise ProviderError("Malformed Stripe signature header.", code="bad_signature")

        try:
            age = abs(time.time() - int(timestamp))
        except ValueError:
            raise ProviderError("Malformed Stripe signature timestamp.", code="bad_signature") from None
        if age > SIGNATURE_TOLERANCE_SECONDS:
            raise ProviderError("Stripe signature is outside the tolerance window.", code="stale_signature")

        expected = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ProviderError("Stripe signature did not match.", code="bad_signature")

    async def account(self, *, credentials: ProviderCredentials) -> AccountInfo:
        try:
            payload = await self._get(credentials, "/account")
        except ProviderError as error:
            return AccountInfo(connected=False, message=str(error))
        return AccountInfo(
            connected=True,
            display_name=(payload.get("business_profile") or {}).get("name")
            or payload.get("email"),
            account_id=payload.get("id"),
            currencies=tuple(
                c.upper() for c in (payload.get("capabilities") or {}) if isinstance(c, str)
            )
            or self.currencies,
            payouts_enabled=bool(payload.get("payouts_enabled")),
            charges_enabled=bool(payload.get("charges_enabled")),
            country=payload.get("country"),
            raw=payload,
        )

    async def transactions(
        self, *, credentials: ProviderCredentials, limit: int = 50
    ) -> list[ChargeResult]:
        payload = await self._get(credentials, "/charges", {"limit": min(limit, 100)})
        results: list[ChargeResult] = []
        for charge in payload.get("data", []):
            currency = (charge.get("currency") or "usd").upper()
            card = (charge.get("payment_method_details") or {}).get("card") or {}
            results.append(
                ChargeResult(
                    reference=(charge.get("metadata") or {}).get("reference", ""),
                    provider_reference=charge.get("payment_intent") or charge["id"],
                    status="succeeded" if charge.get("paid") else "failed",
                    amount_minor=self._from_stripe(charge.get("amount") or 0, currency),
                    currency=currency,
                    method=(charge.get("payment_method_details") or {}).get("type"),
                    card_brand=card.get("brand"),
                    card_last4=card.get("last4"),
                    raw=charge,
                )
            )
        return results

    # -- amounts ----------------------------------------------------------

    @staticmethod
    def _to_stripe(amount_minor: int, currency: str) -> int:
        """Stripe's integer for an amount this platform holds in minor units.

        The two agree for every two-decimal currency, and for zero-decimal ones
        (JPY, KRW) both sides count whole units — so this is the identity. It
        exists as a named function anyway, because the day a three-decimal
        currency is added, this is the one place that has to change.
        """
        _ = scale(currency)
        return amount_minor

    @staticmethod
    def _from_stripe(amount: int, currency: str) -> int:
        _ = scale(currency)
        return int(amount)


#: Stripe's event types, mapped to the platform's vocabulary. Anything not
#: listed becomes `ignored` — recorded so support can see it arrived, and not
#: acted on.
_EVENT_KINDS = {
    "checkout.session.completed": "payment.succeeded",
    "checkout.session.async_payment_succeeded": "payment.succeeded",
    "payment_intent.succeeded": "payment.succeeded",
    "payment_intent.payment_failed": "payment.failed",
    "checkout.session.async_payment_failed": "payment.failed",
    "charge.refunded": "refund.succeeded",
    "refund.created": "refund.succeeded",
    "payout.paid": "payout.paid",
    "payout.failed": "payout.failed",
}


def _reference_from(obj: dict[str, Any]) -> str | None:
    """The provider reference a webhook object points at.

    A session names its intent; an intent is its own reference; a charge names
    the intent it belongs to. All three arrive, and all three have to resolve
    to the same `Payment` row.
    """
    return obj.get("payment_intent") or obj.get("id")


def _form(data: dict[str, Any]) -> str:
    """Stripe's form encoding, with `None` dropped rather than sent as "None"."""
    return urlencode({k: v for k, v in data.items() if v is not None}, doseq=True)
