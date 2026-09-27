"""Operator sign-in: Akountz users of the reserved ``_platform`` project.

Studio is a backend-for-frontend. The browser holds only a signed session
cookie; the operator's tokens stay on the server. Calls to the other services
are made with a service token that names the operator (``operator=``), so the
API's audit log records who did what without the browser ever holding a
credential the API would accept.
"""

from __future__ import annotations

import time
from typing import Any

from sillo import HttpContext

from pawabase_kit.clients import ServiceClient, ServiceError
from pawabase_kit.context import PlatformContext
from pawabase_kit.settings import PLATFORM_ENV, PLATFORM_PROJECT
from pawabase_kit.tokens import TokenInvalid, verify_user_token

SESSION_KEY = "operator"
PLATFORM_CONTEXT = PlatformContext(
    project=PLATFORM_PROJECT, env=PLATFORM_ENV, role="anon", key_id="studio"
)
#: Refresh the access token this many seconds before it expires.
REFRESH_MARGIN = 60


class SignInFailed(Exception):
    def __init__(self, message: str, *, mfa_token: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.mfa_token = mfa_token


def _session(ctx: HttpContext) -> Any | None:
    return ctx.scope.get("session")


def _store(ctx: HttpContext, master_secret: str, tokens: dict[str, Any]) -> dict[str, Any]:
    claims = verify_user_token(
        tokens["access_token"], master_secret, project=PLATFORM_PROJECT, env=PLATFORM_ENV
    )
    operator = {
        "sub": str(claims.get("sub")),
        "email": claims.get("email"),
        "roles": claims.get("roles") or [],
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": int(claims.get("exp") or time.time() + 300),
    }
    session = _session(ctx)
    if session is not None:
        session.set(SESSION_KEY, operator)
    return operator


async def sign_in(
    ctx: HttpContext, akountz: ServiceClient, master_secret: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Password (and, when enrolled, TOTP) sign-in against ``_platform``."""
    try:
        tokens = await akountz.post("/auth/v1/token", json=body, context=PLATFORM_CONTEXT)
    except ServiceError as exc:
        detail = exc.body.get("detail") if isinstance(exc.body, dict) else None
        raise SignInFailed(detail or "sign-in failed") from exc
    if tokens.get("mfa_required"):
        raise SignInFailed(
            "enter the code from your authenticator app", mfa_token=tokens.get("mfa_token")
        )
    return _store(ctx, master_secret, tokens)


async def current_operator(
    ctx: HttpContext, akountz: ServiceClient, master_secret: str
) -> dict[str, Any] | None:
    """The signed-in operator, refreshing the access token when it is close to expiry."""
    session = _session(ctx)
    operator = session.get(SESSION_KEY) if session is not None else None
    if not operator:
        return None
    if operator["expires_at"] - REFRESH_MARGIN > time.time():
        return operator
    if not operator.get("refresh_token"):
        sign_out(ctx)
        return None
    try:
        tokens = await akountz.post(
            "/auth/v1/token",
            json={"grant_type": "refresh_token", "refresh_token": operator["refresh_token"]},
            context=PLATFORM_CONTEXT,
        )
        return _store(ctx, master_secret, tokens)
    except (ServiceError, TokenInvalid, KeyError):
        sign_out(ctx)
        return None


async def revoke(ctx: HttpContext, akountz: ServiceClient) -> None:
    session = _session(ctx)
    operator = session.get(SESSION_KEY) if session is not None else None
    if operator:
        try:
            await akountz.post(
                "/auth/v1/logout",
                json={"scope": "local"},
                context=PLATFORM_CONTEXT,
                headers={"Authorization": f"Bearer {operator['access_token']}"},
            )
        except ServiceError:
            pass
    sign_out(ctx)


def sign_out(ctx: HttpContext) -> None:
    session = _session(ctx)
    if session is not None:
        session.delete(SESSION_KEY)


def public(operator: dict[str, Any]) -> dict[str, Any]:
    """What the browser may see about the operator."""
    return {"id": operator["sub"], "email": operator["email"], "roles": operator["roles"]}
