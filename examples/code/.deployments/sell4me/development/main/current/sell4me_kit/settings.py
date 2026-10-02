"""Platform settings, from Pawabase secrets with the original defaults.

Set these as secrets (Studio → Secrets, or ``PUT /platform/v1/projects/<ref>/envs/<env>/secrets/<NAME>``):

========================  =========================================================  =========
Secret                    Meaning                                                    Default
========================  =========================================================  =========
PAYSTACK_SECRET_KEY       The platform's Paystack secret key                          (none)
PAYSTACK_PUBLIC_KEY       The platform's Paystack public key                          (none)
PLATFORM_SPLIT_PERCENT    Platform's share of every order, in percent                 5
PLATFORM_FEE_MINOR        Flat fee kept per order, in minor units                     100
PLATFORM_FEE_CURRENCY     Currency of that flat fee                                   NGN
APP_URL                   The dashboard's public URL (links in emails)               http://localhost:3000
STOREFRONT_SUFFIX         Shops live at <slug>.<suffix>                              shop.localhost:3000
MAIL_FROM / MAIL_FROM_NAME  Sender of every email                                    orders@commerce.local
SANDBOX_ENABLED           Allow the offline sandbox provider (never in production)    false
WEBHOOKS_ALLOW_PRIVATE    Let merchant webhooks target private addresses (dev only)    false
PAYSTACK_BASE_URL         Paystack's API origin (point at a test double)               https://api.paystack.co
========================  =========================================================  =========
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULTS: dict[str, str] = {
    "PAYSTACK_BASE_URL": "https://api.paystack.co",
    "PLATFORM_SPLIT_PERCENT": "5",
    "PLATFORM_FEE_MINOR": "100",
    "PLATFORM_FEE_CURRENCY": "NGN",
    "APP_URL": "http://localhost:3000",
    "STOREFRONT_SUFFIX": "shop.localhost:3000",
    "MAIL_FROM": "orders@commerce.local",
    "MAIL_FROM_NAME": "SELL4ME",
    "SANDBOX_ENABLED": "false",
    "WEBHOOKS_ALLOW_PRIVATE": "false",
}


@dataclass(frozen=True, slots=True)
class Settings:
    paystack_secret_key: str
    paystack_public_key: str
    paystack_base_url: str
    platform_split_percent: float
    platform_fee_minor: int
    platform_fee_currency: str
    app_url: str
    storefront_suffix: str
    mail_from: str
    mail_from_name: str
    sandbox_enabled: bool
    webhooks_allow_private: bool = False
    secret_key: str = "dev-only-insecure-secret-key"
    app_name: str = "SELL4ME"
    media_base_url: str = ""
    max_upload_bytes: int = 12 * 1024 * 1024

    def shop_url(self, slug: str, hostname: str | None = None) -> str:
        scheme = "https" if self.app_url.lower().startswith("https://") else "http"
        return f"{scheme}://{hostname or f'{slug}.{self.storefront_suffix}'}"


async def load(runtime: Any) -> Settings:
    async def get(name: str) -> str:
        value = await runtime.secret(name)
        return str(value) if value not in (None, "") else DEFAULTS.get(name, "")

    return Settings(
        paystack_secret_key=await get("PAYSTACK_SECRET_KEY"),
        paystack_public_key=await get("PAYSTACK_PUBLIC_KEY"),
        paystack_base_url=await get("PAYSTACK_BASE_URL"),
        platform_split_percent=float(await get("PLATFORM_SPLIT_PERCENT") or 5),
        platform_fee_minor=int(await get("PLATFORM_FEE_MINOR") or 100),
        platform_fee_currency=await get("PLATFORM_FEE_CURRENCY"),
        app_url=await get("APP_URL"),
        storefront_suffix=await get("STOREFRONT_SUFFIX"),
        mail_from=await get("MAIL_FROM"),
        mail_from_name=await get("MAIL_FROM_NAME"),
        sandbox_enabled=(await get("SANDBOX_ENABLED")).lower() in ("1", "true", "yes", "on"),
        webhooks_allow_private=(await get("WEBHOOKS_ALLOW_PRIVATE")).lower() in ("1", "true", "yes", "on"),
        secret_key=(await runtime.secret("SELL4ME_SECRET_KEY")) or "dev-only-insecure-secret-key",
        media_base_url=(await runtime.secret("MEDIA_BASE_URL")) or "",
    )
