"""Single-use tokens: email verification, password recovery, magic links, invitations, MFA challenges.

A token is Sillo's ``URLSafeTimedSerializer`` signature over the id of a
:class:`~database.models.OneTimeToken` row. The signature makes it
unforgeable and time-limited; the row makes it single-use and revocable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sillo.exceptions import HTTPException
from sillo.helpers.signing import BadSignature

from app.environment import AuthConfig
from app.platform import Akountz
from database.models import AuthUser, OneTimeToken
from pawabase_core.ids import new_ulid

LIFETIMES = {
    "verify": 24 * 3600,
    "recovery": 3600,
    "magic": 900,
    "invite": 7 * 24 * 3600,
    "mfa": 300,
    "email_change": 24 * 3600,
    "link": 600,
}


async def issue(
    akountz: Akountz,
    config: AuthConfig,
    purpose: str,
    *,
    user: AuthUser | None = None,
    email: str | None = None,
    data: dict[str, Any] | None = None,
) -> str:
    lifetime = LIFETIMES[purpose]
    row = await OneTimeToken.create(
        id=new_ulid(),
        env=config.env,
        purpose=purpose,
        user=user,
        email=email or (user.email if user else None),
        data=data or {},
        expires_at=datetime.now(UTC) + timedelta(seconds=lifetime),
    )
    return akountz.serializer(config.env, purpose).dumps({"id": row.id})


async def consume(
    akountz: Akountz, config: AuthConfig, purpose: str, token: str, *, peek: bool = False
) -> OneTimeToken:
    """Verify and use up a token. Every failure is the same 400."""
    invalid = HTTPException(status_code=400, detail="the link is invalid or has expired")
    try:
        payload = akountz.serializer(config.env, purpose).loads(token, max_age=LIFETIMES[purpose])
    except (BadSignature, ValueError, TypeError) as exc:
        raise invalid from exc
    row = (
        await OneTimeToken.filter(id=str(payload.get("id", "")), env=config.env, purpose=purpose)
        .prefetch_related("user")
        .first()
    )
    if row is None or row.used_at is not None or row.expires_at <= datetime.now(UTC):
        raise invalid
    if not peek:
        updated = await OneTimeToken.filter(id=row.id, used_at=None).update(
            used_at=datetime.now(UTC)
        )
        if not updated:  # a concurrent request used it first
            raise invalid
    return row


def link(config: AuthConfig, kind: str, token: str, redirect_to: str | None = None) -> str:
    """The URL a message links to.

    With a ``site_url`` the link goes to the application, which completes the
    flow with the API. Without one it goes to Akountz's own link page.
    """
    query = {"token": token, "type": kind}
    if redirect_to:
        query["redirect_to"] = redirect_to
    if config.site_url:
        return f"{config.site_url.rstrip('/')}/auth/callback?{urlencode(query)}"
    base = (config.public_url or "").rstrip("/")
    return f"{base}/auth/v1/links/{config.env}/{kind}?{urlencode(query)}"
