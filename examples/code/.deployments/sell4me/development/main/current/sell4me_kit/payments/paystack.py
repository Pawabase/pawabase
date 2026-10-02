"""Paystack, over plain HTTP.

Unlike the Stripe provider this sits beside, this is a marketplace
integration: the platform's own keys are used for every store — see
`app/config.py`'s `paystack_secret_key` — not a merchant's. A charge settles
to the platform's Paystack balance first; the merchant is paid separately,
after their order is confirmed, by `app/services/payouts.py`. `initialize()`,
`verify()`, `refund()` and `parse_webhook()` below are unchanged by that and
still speak plain transaction language; the marketplace-specific calls
(`create_subaccount`, `initiate_transfer`, ...) are their own section further
down, and are the only ones ever called with the platform's own credentials
rather than the ones handed in per-store by the base contract.

Three things about Paystack that this module exists to absorb, so nothing
upstream has to know them:

* **Its reference is ours.** Paystack takes the merchant's reference at
  initialisation and uses it as the transaction's identity throughout —
  verification is `GET /transaction/verify/{our reference}`. That is more
  convenient than Stripe's two-id dance, and it means `provider_reference` here
  is the numeric transaction id while lookups go by reference.
* **Amounts are in the currency's subunit**, which is what this platform
  already holds — kobo for NGN, pesewas for GHS, cents for ZAR and KES.
* **Every response is `{status, message, data}`.** A failure is a 200 with
  `status: false` as often as it is a 4xx, so the status code alone is not
  enough to tell whether something worked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

from sell4me_kit.payments.base import (
    AccountInfo,
    ChargeResult,
    PaymentProvider,
    ProviderCredentials,
    ProviderError,
    RefundResult,
    WebhookEvent,
)

__all__ = ["PaystackProvider"]

API = "https://api.paystack.co"

#: Paystack's transaction statuses, mapped to the platform's five.
_STATUS = {
    "success": "succeeded",
    "failed": "failed",
    "abandoned": "cancelled",
    "reversed": "cancelled",
    "ongoing": "processing",
    "pending": "pending",
    "processing": "processing",
    "queued": "processing",
}


class PaystackProvider(PaymentProvider):
    key = "paystack"
    label = "Paystack"
    description = (
        "Cards, bank transfers, USSD and mobile money across Nigeria, Ghana, "
        "Kenya and South Africa. Charges settle to the platform first; connect "
        "a bank account under Payouts and you're paid your share once an "
        "order is confirmed."
    )
    currencies = ("NGN", "GHS", "ZAR", "KES")
    credential_fields = (
        ("public_key", "Public key (pk_…)", False),
        ("secret_key", "Secret key (sk_…)", True),
    )

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._timeout = timeout

    # -- HTTP -------------------------------------------------------------

    async def _call(
        self,
        credentials: ProviderCredentials,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._secret(credentials)}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.request(
                    method, f"{credentials.base_url or API}{path}", json=json_body, params=params, headers=headers
                )
            except httpx.HTTPError as error:
                raise ProviderError(
                    f"Could not reach Paystack: {error}",
                    code="connection_error",
                    retryable=True,
                ) from error

        try:
            payload = response.json()
        except ValueError:
            raise ProviderError(
                f"Paystack returned a non-JSON response ({response.status_code}).",
                code="bad_response",
                retryable=response.status_code >= 500,
            ) from None

        # A Paystack failure is a 200 with `status: false` at least as often as
        # it is a 4xx, so both have to be checked — trusting the status code
        # alone would treat a declined charge as a successful call.
        if response.status_code >= 400 or payload.get("status") is False:
            raise ProviderError(
                payload.get("message", "Paystack rejected the request."),
                code=str(payload.get("code") or response.status_code),
                retryable=response.status_code >= 500 or response.status_code == 429,
            )
        return payload.get("data") or {}

    @staticmethod
    def _secret(credentials: ProviderCredentials) -> str:
        if not credentials.secret_key:
            raise ProviderError("Paystack is not connected for this store.", code="no_credentials")
        return credentials.secret_key

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
        if not self.supports_currency(currency):
            raise ProviderError(
                f"Paystack does not settle {currency.upper()}.", code="unsupported_currency"
            )

        body: dict[str, Any] = {
            "email": email,
            # Already the subunit — kobo, pesewas, cents. No conversion.
            "amount": amount_minor,
            "currency": currency.upper(),
            "reference": reference,
            "callback_url": return_url,
            "metadata": {
                **(metadata or {}),
                "reference": reference,
                # Paystack renders these on the merchant's dashboard, which is
                # what makes a charge traceable from their side back to here.
                "custom_fields": [
                    {
                        "display_name": "Order reference",
                        "variable_name": "order_reference",
                        "value": reference,
                    }
                ],
                "cancel_action": cancel_url,
            },
        }
        data = await self._call(credentials, "POST", "/transaction/initialize", json_body=body)
        return ChargeResult(
            reference=reference,
            # Paystack's own id does not exist until the customer pays; the
            # access code identifies the session until then.
            provider_reference=data.get("access_code") or reference,
            status="pending",
            amount_minor=amount_minor,
            currency=currency.upper(),
            redirect_url=data["authorization_url"],
            raw=data,
        )

    async def verify(
        self, *, credentials: ProviderCredentials, reference: str
    ) -> ChargeResult:
        data = await self._call(credentials, "GET", f"/transaction/verify/{reference}")
        return self._from_transaction(data, reference)

    def _from_transaction(self, data: dict[str, Any], reference: str) -> ChargeResult:
        auth = data.get("authorization") or {}
        currency = (data.get("currency") or "NGN").upper()
        fee = data.get("fees")
        return ChargeResult(
            reference=reference or data.get("reference", ""),
            provider_reference=str(data.get("id") or reference),
            status=_STATUS.get(data.get("status", ""), "processing"),
            amount_minor=int(data.get("amount") or 0),
            currency=currency,
            # Paystack reports the fee on the transaction itself, so unlike
            # Stripe it is known as soon as the charge succeeds.
            fee_minor=int(fee) if fee is not None else None,
            method=data.get("channel"),
            card_brand=auth.get("card_type") or auth.get("brand"),
            card_last4=auth.get("last4"),
            failure_code=data.get("gateway_response") if data.get("status") != "success" else None,
            failure_message=data.get("message") or data.get("gateway_response"),
            raw=data,
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
        body: dict[str, Any] = {
            "transaction": provider_reference,
            "amount": amount_minor,
        }
        if reason:
            body["merchant_note"] = reason[:500]

        data = await self._call(credentials, "POST", "/refund", json_body=body)
        return RefundResult(
            reference=idempotency_key or str(data.get("id", "")),
            provider_reference=str(data.get("id")) if data.get("id") else None,
            # Paystack refunds are asynchronous — `pending` here is the normal
            # answer, and the webhook confirms it later.
            status=_STATUS.get(data.get("status", ""), "pending"),
            amount_minor=int(data.get("amount") or amount_minor),
            currency=currency.upper(),
            raw=data,
        )

    async def parse_webhook(
        self,
        *,
        credentials: ProviderCredentials,
        body: bytes,
        headers: dict[str, str],
    ) -> WebhookEvent:
        """Verify `x-paystack-signature` and normalise.

        Paystack signs with HMAC-SHA512 of the raw body, keyed by the *secret
        key* — not a separate webhook secret, which is why the connect form has
        no field for one. The body must be the bytes as received.
        """
        secret = credentials.secret_key
        if not secret:
            raise ProviderError(
                "No Paystack secret key is configured for this store.",
                code="no_credentials",
            )
        signature = headers.get("x-paystack-signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ProviderError("Paystack signature did not match.", code="bad_signature")

        return self.normalise(json.loads(body.decode()))

    def normalise(self, event: dict[str, Any]) -> WebhookEvent:
        """A Paystack event as the platform's own event type.

        Split from :meth:`parse_webhook` because on Pawabase the signature is verified by the inbound hook (HMAC-SHA512 over the raw body, keyed by this
        same secret) *before* a function ever sees the delivery, and the function receives the parsed body, not the bytes.
        """
        data = event.get("data") or {}

        # Paystack does not send a delivery id. The event type plus the
        # transaction's own id is stable across the retries it does make, which
        # is what the idempotency table needs — a synthesised random id would
        # make every retry look like a new event and defeat the whole gate.
        event_id = f"{event.get('event')}:{data.get('id') or data.get('reference')}"

        return WebhookEvent(
            event_id=event_id,
            kind=_EVENT_KINDS.get(event.get("event", ""), "ignored"),
            provider_reference=data.get("reference") or str(data.get("id") or ""),
            amount_minor=int(data.get("amount") or 0) or None,
            currency=(data.get("currency") or "").upper() or None,
            fee_minor=int(data["fees"]) if data.get("fees") is not None else None,
            payload=event,
        )

    async def account(self, *, credentials: ProviderCredentials) -> AccountInfo:
        try:
            # Paystack has no `/account`; the integration's own settings are
            # the closest thing, and a successful call is itself the proof that
            # the key works.
            data = await self._call(credentials, "GET", "/integration/payment_session_timeout")
        except ProviderError as error:
            return AccountInfo(connected=False, message=str(error))
        return AccountInfo(
            connected=True,
            display_name="Paystack integration",
            currencies=self.currencies,
            # Paystack settles on its own schedule to the bank account on the
            # integration; there is no per-key capability flag to read, so this
            # reports what is true rather than inventing a check.
            payouts_enabled=True,
            charges_enabled=True,
            message="Settlement is configured in your Paystack dashboard.",
            raw=data,
        )

    async def transactions(
        self, *, credentials: ProviderCredentials, limit: int = 50
    ) -> list[ChargeResult]:
        data = await self._call(
            credentials, "GET", "/transaction", params={"perPage": min(limit, 100)}
        )
        rows = data if isinstance(data, list) else []
        return [self._from_transaction(row, row.get("reference", "")) for row in rows]

    # -- marketplace: subaccounts and transfers ----------------------------
    #
    # Everything below is called with the *platform's* credentials, never a
    # merchant's — there is no merchant secret key anymore. A subaccount is
    # how Paystack knows a merchant's bank details; a transfer recipient is
    # how the platform pays them. Neither is used to split a charge at the
    # point of sale (see app/services/payouts.py for why) — only afterwards,
    # to move the merchant's share out of the platform's own balance.

    async def list_banks(self, *, credentials: ProviderCredentials, country: str = "nigeria") -> list[dict[str, str]]:
        """Banks a merchant can settle to, for the connect form's picker."""
        data = await self._call(
            credentials, "GET", "/bank", params={"country": country, "perPage": 100}
        )
        rows = data if isinstance(data, list) else []
        return [{"name": row["name"], "code": row["code"]} for row in rows if row.get("code")]

    async def resolve_account(
        self, *, credentials: ProviderCredentials, account_number: str, bank_code: str
    ) -> str:
        """The account holder's name, as Paystack's own bank records give it.

        Called before a subaccount is created so the merchant sees whose
        account they are about to connect — a transposed digit is a wrong
        person's bank account otherwise, discovered only when a payout to it
        fails or, worse, does not.
        """
        data = await self._call(
            credentials,
            "GET",
            "/bank/resolve",
            params={"account_number": account_number, "bank_code": bank_code},
        )
        return str(data.get("account_name") or "")

    async def create_subaccount(
        self,
        *,
        credentials: ProviderCredentials,
        business_name: str,
        bank_code: str,
        account_number: str,
        percentage_charge: float,
    ) -> str:
        """A Paystack subaccount for one merchant. Returns its code.

        `percentage_charge` is the *platform's* cut, in Paystack's own terms —
        the percentage of a split transaction that would go to the main
        (platform) account rather than this subaccount. This platform never
        actually runs a split transaction against the subaccount (again, see
        `app/services/payouts.py`), but the value is what a payout computes
        against, so it has to be the real split, not a placeholder.
        """
        body = {
            "business_name": business_name[:100],
            "settlement_bank": bank_code,
            "account_number": account_number,
            "percentage_charge": percentage_charge,
        }
        data = await self._call(credentials, "POST", "/subaccount", json_body=body)
        return str(data["subaccount_code"])

    async def create_transfer_recipient(
        self,
        *,
        credentials: ProviderCredentials,
        name: str,
        account_number: str,
        bank_code: str,
        currency: str = "NGN",
    ) -> str:
        """A transfer recipient for one merchant's bank account. Returns its code.

        A `nuban` recipient built straight from the bank details rather than
        Paystack's `subaccount`-type recipient — the latter is documented
        inconsistently across Paystack's own API versions, where the former is
        the same call every integration already makes.
        """
        body = {
            "type": "nuban",
            "name": name[:100],
            "account_number": account_number,
            "bank_code": bank_code,
            "currency": currency.upper(),
        }
        data = await self._call(credentials, "POST", "/transferrecipient", json_body=body)
        return str(data["recipient_code"])

    async def initiate_transfer(
        self,
        *,
        credentials: ProviderCredentials,
        recipient_code: str,
        amount_minor: int,
        reference: str,
        reason: str,
        currency: str = "NGN",
    ) -> dict[str, Any]:
        """Send money to a recipient. This is the actual payout.

        Reference is ours and unique per payout, the same idempotency shape as
        `initialize()` — Paystack rejects a repeat with the same reference
        rather than sending the money twice, which is what makes a retried job
        safe to retry.
        """
        body = {
            "source": "balance",
            "amount": amount_minor,
            "recipient": recipient_code,
            "reference": reference,
            "reason": reason[:100],
            "currency": currency.upper(),
        }
        return await self._call(credentials, "POST", "/transfer", json_body=body)


_EVENT_KINDS = {
    "charge.success": "payment.succeeded",
    "charge.failed": "payment.failed",
    "refund.processed": "refund.succeeded",
    "refund.failed": "refund.failed",
    "transfer.success": "payout.paid",
    "transfer.failed": "payout.failed",
}
