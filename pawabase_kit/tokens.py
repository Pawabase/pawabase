"""The three kinds of token Pawabase signs.

* **Service tokens**: one service calling another. Short-lived and addressed to one
  audience.
* **Context tokens**: the gateway telling a service which project, environment and
  credential a request carries.
* **User tokens**: access tokens Akountz issues to a project's users, signed with
  a key derived per environment, so a token from ``acme/dev`` can never be used
  against ``acme/production``.

All three are JWTs encoded with :mod:`sillo.helpers.jwt`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from sillo.helpers import jwt as sillo_jwt

from .context import PlatformContext

SERVICE_ISSUER_CLAIM = "svc"
USER_AUDIENCE = "authenticated"


class TokenInvalid(Exception):
    """A token was missing, expired, tampered with, or addressed elsewhere."""


def derive_env_secret(master: str, project: str, env: str) -> str:
    """The signing key for one project environment's user tokens.

    Deterministic, so every service can verify without asking Akountz, and
    independent per environment, so compromising one key exposes one environment.
    """
    message = f"pawabase:user-token:{project}:{env}".encode()
    return hmac.new(master.encode(), message, hashlib.sha256).hexdigest()


def _decode(token: str, secret: str, audience: str | None = None) -> dict[str, Any]:
    try:
        return sillo_jwt.decode(token, secret, audience=audience, leeway=5)
    except sillo_jwt.TokenError as exc:
        raise TokenInvalid(str(exc)) from exc
    except Exception as exc:  # malformed input reaching PyJWT
        raise TokenInvalid("malformed token") from exc


# ── service tokens ──────────────────────────────────────────────────────────


def issue_service_token(
    secret: str,
    *,
    issuer: str,
    audience: str,
    subject: str | None = None,
    claims: dict[str, Any] | None = None,
    ttl: int = 60,
) -> str:
    """Mint a token for *issuer* to call *audience*.

    Args:
        secret: The installation's internal secret.
        issuer: The calling service.
        audience: The service being called.
        subject: Who the call is on behalf of, e.g. a Studio operator id.
        claims: Extra claims, e.g. the operator's roles.
        ttl: Lifetime in seconds. Keep it short: the token is re-minted per call.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        SERVICE_ISSUER_CLAIM: issuer,
        "aud": f"pawabase:{audience}",
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(8),
        "typ": "service",
    }
    if subject is not None:
        payload["sub"] = subject
    if claims:
        payload.update(claims)
    return sillo_jwt.encode(payload, secret)


def verify_service_token(token: str, secret: str, *, audience: str) -> dict[str, Any]:
    """Verify a service token addressed to *audience*. Raises :class:`TokenInvalid`."""
    claims = _decode(token, secret, audience=f"pawabase:{audience}")
    if claims.get("typ") != "service":
        raise TokenInvalid("not a service token")
    return claims


# ── context tokens ──────────────────────────────────────────────────────────


def issue_context_token(secret: str, context: PlatformContext, *, ttl: int = 60) -> str:
    """Sign *context* for forwarding to an internal service."""
    now = int(time.time())
    payload = {**context.to_claims(), "iat": now, "exp": now + ttl, "typ": "context"}
    return sillo_jwt.encode(payload, secret)


def verify_context_token(token: str, secret: str) -> PlatformContext:
    """Verify a context header. Raises :class:`TokenInvalid`."""
    claims = _decode(token, secret)
    if claims.get("typ") != "context":
        raise TokenInvalid("not a context token")
    return PlatformContext.from_claims(claims)


# ── user tokens ─────────────────────────────────────────────────────────────


def issue_user_token(
    master: str,
    *,
    project: str,
    env: str,
    user_id: str,
    jti: str,
    session_id: str,
    ttl: int = 900,
    email: str | None = None,
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
    org: str | None = None,
    org_role: str | None = None,
    aal: str = "aal1",
    extra: dict[str, Any] | None = None,
    app: dict[str, Any] | None = None,
) -> str:
    """Mint a user access token for one project environment.

    Args:
        master: The platform JWT master secret.
        project, env: The environment the token is valid for.
        user_id: The user's id, as ``sub``.
        jti: The token id. Akountz reuses the id Sillo's token family tracks, so
            revocation and rotation line up with Sillo's ``JWTToken`` rows.
        session_id: The Sillo token family, reported as ``sid``.
        ttl: Lifetime in seconds.
        email, roles, permissions, org: Identity claims services can use in
            policies without calling Akountz.
        org_role: The caller's role in ``org`` (``owner``, ``admin``, ``member``, ``viewer``).
        aal: Authentication assurance level, ``aal2`` after MFA.
        extra: Additional public claims (``user_metadata``), as ``meta``. Users can
            edit these, so policies must not trust them.
        app: Administrator-controlled claims (``app_metadata``), as ``app``. Only
            admins can change them, so policies may rely on them.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "aud": USER_AUDIENCE,
        "iss": f"pawabase:akountz:{project}",
        "prj": project,
        "env": env,
        "sid": session_id,
        "jti": jti,
        "iat": now,
        "exp": now + ttl,
        "typ": "access",
        "aal": aal,
        "email": email,
        "roles": roles or [],
        "perms": permissions or [],
        "org": org,
        "org_role": org_role,
    }
    if extra:
        payload["meta"] = extra
    if app:
        payload["app"] = app
    return sillo_jwt.encode(payload, derive_env_secret(master, project, env))


def verify_user_token(token: str, master: str, *, project: str, env: str) -> dict[str, Any]:
    """Verify a user access token for *project*/*env*. Raises :class:`TokenInvalid`."""
    claims = _decode(token, derive_env_secret(master, project, env), audience=USER_AUDIENCE)
    if claims.get("typ") != "access":
        raise TokenInvalid("not an access token")
    if claims.get("prj") != project or claims.get("env") != env:
        raise TokenInvalid("token belongs to another environment")
    return claims


def peek_claims(token: str) -> dict[str, Any] | None:
    """Unverified claims, used only to learn which environment a token names."""
    return sillo_jwt.get_unverified_claims(token)
