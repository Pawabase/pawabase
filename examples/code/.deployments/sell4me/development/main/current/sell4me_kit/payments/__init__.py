"""The provider registry. Paystack is the platform's marketplace account; sandbox is an
offline stand-in for demos and tests; Stripe stays registered but unoffered."""

from __future__ import annotations

from .base import (
    AccountInfo,
    ChargeResult,
    PaymentProvider,
    ProviderCredentials,
    ProviderError,
    RefundResult,
    WebhookEvent,
)
from .paystack import PaystackProvider
from .sandbox import SandboxProvider
from .stripe import StripeProvider

__all__ = [
    "AccountInfo",
    "ChargeResult",
    "PaymentProvider",
    "ProviderCredentials",
    "ProviderError",
    "RefundResult",
    "WebhookEvent",
    "available_providers",
    "get_provider",
    "provider_catalogue",
]

_REGISTRY: dict[str, PaymentProvider] = {
    provider.key: provider for provider in (SandboxProvider(), StripeProvider(), PaystackProvider())
}


def get_provider(key: str) -> PaymentProvider:
    provider = _REGISTRY.get(key)
    if provider is None:
        raise ProviderError(f"No payment provider named {key!r}.", code="unknown_provider")
    return provider


def available_providers() -> list[PaymentProvider]:
    """Providers a merchant may connect by entering keys: none (Paystack is the platform's own)."""
    return []


def provider_catalogue() -> list[dict[str, object]]:
    return [
        {
            "key": provider.key,
            "label": provider.label,
            "description": provider.description,
            "currencies": list(provider.currencies),
            "requires_credentials": provider.requires_credentials,
            "fields": [{"name": n, "label": l, "secret": s} for n, l, s in provider.credential_fields],
        }
        for provider in available_providers()
    ]
