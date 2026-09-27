"""Issuing, refreshing and revoking sessions.

Sillo does the token lifecycle. ``JWTUserMixin.issue_token_pair`` creates a
token family with tracked access and refresh JTIs, and ``refresh_token_pair``
rotates it, detecting reuse and revoking the whole family when a consumed
refresh token comes back. Pawabase adds what Sillo's tokens do not carry: the
project, environment, roles, permissions and assurance level, in an access
token re-minted under the tracked JTI and signed with the environment's key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.auth.jwt_auth import JWTToken
from sillo.exceptions import HTTPException

from app import rbac
from app.accounts import user_view
from app.environment import AuthConfig
from app.platform import Akountz
from database.models import AuthUser, LoginEvent, SessionInfo
from pawabase_kit.tokens import issue_user_token, peek_claims


def client_info(ctx: Any) -> tuple[str | None, str | None]:
    forwarded = ctx.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (ctx.client.host if ctx.client else None)
    return ip, (ctx.headers.get("user-agent") or "")[:500]


async def log_event(
    project: str,
    env: str,
    kind: str,
    *,
    user: AuthUser | None = None,
    email: str | None = None,
    method: str = "password",
    success: bool = True,
    reason: str | None = None,
    ctx: Any = None,
) -> None:
    ip, agent = client_info(ctx) if ctx is not None else (None, None)
    await LoginEvent.create(
        project=project,
        env=env,
        user_id=user.id if user else None,
        email=email or (user.email if user else None),
        kind=kind,
        method=method,
        success=success,
        reason=reason,
        ip=ip,
        user_agent=agent,
    )


async def _access_token(
    akountz: Akountz, config: AuthConfig, user: AuthUser, *, jti: str, family: str, aal: str
) -> str:
    return issue_user_token(
        akountz.settings.jwt_master_secret,
        project=config.project,
        env=config.env,
        user_id=str(user.id),
        jti=jti,
        session_id=family,
        ttl=config.access_ttl,
        email=user.email,
        roles=await rbac.roles_of(user),
        permissions=await rbac.permissions_of(user),
        aal=aal,
        extra=user.user_metadata or None,
    )


async def _response(
    akountz: Akountz, config: AuthConfig, user: AuthUser, pair: dict[str, Any], aal: str
) -> dict[str, Any]:
    claims = peek_claims(pair["access_token"]) or {}
    access = await _access_token(
        akountz, config, user, jti=claims.get("jti", ""), family=pair["token_family"], aal=aal
    )
    return {
        "access_token": access,
        "refresh_token": pair["refresh_token"],
        "token_type": "bearer",
        "expires_in": config.access_ttl,
        "expires_at": int(datetime.now(UTC).timestamp()) + config.access_ttl,
        "session_id": pair["token_family"],
        "user": await user_view(user),
    }


async def start_session(
    akountz: Akountz,
    config: AuthConfig,
    user: AuthUser,
    *,
    method: str,
    ctx: Any = None,
    aal: str = "aal1",
) -> dict[str, Any]:
    pair = await user.issue_token_pair(
        secret=akountz.refresh_secret(config.project, config.env),
        access_expires=timedelta(seconds=config.access_ttl),
        refresh_expires=timedelta(seconds=config.refresh_ttl),
    )
    ip, agent = client_info(ctx) if ctx is not None else (None, None)
    await SessionInfo.create(
        family=pair["token_family"],
        user=user,
        project=config.project,
        env=config.env,
        method=method,
        aal=aal,
        ip=ip,
        user_agent=agent,
    )
    from app.accounts import record_success

    await record_success(user, ip)
    await log_event(config.project, config.env, "sign_in", user=user, method=method, ctx=ctx)
    await akountz.emit(
        config.project,
        config.env,
        "user.signed_in",
        {"user_id": str(user.id), "method": method},
        actor=str(user.id),
    )
    return await _response(akountz, config, user, pair, aal)


async def refresh_session(
    akountz: Akountz, config: AuthConfig, refresh_token: str, *, ctx: Any = None
) -> dict[str, Any]:
    claims = peek_claims(refresh_token) or {}
    user = (
        await AuthUser.filter(
            id=int(claims.get("sub", 0) or 0),
            project=config.project,
            env=config.env,
            deleted_at=None,
        ).first()
        if str(claims.get("sub", "")).isdigit()
        else None
    )
    if user is None or user.is_disabled:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    try:
        pair = await user.refresh_token_pair(
            refresh_token, akountz.refresh_secret(config.project, config.env)
        )
    except ValueError as exc:
        await log_event(
            config.project,
            config.env,
            "refresh",
            user=user,
            method="refresh_token",
            success=False,
            reason=str(exc),
            ctx=ctx,
        )
        if "theft" in str(exc):
            row = await JWTToken.filter(token_jti=claims.get("jti") or "").first()
            if row is not None:
                await SessionInfo.filter(family=row.token_family).update(
                    revoked_at=datetime.now(UTC)
                )
            await akountz.emit(
                config.project,
                config.env,
                "session.reuse_detected",
                {"user_id": str(user.id)},
                actor=str(user.id),
            )
        raise HTTPException(status_code=401, detail="invalid refresh token") from exc
    info = await SessionInfo.get_or_none(family=pair["token_family"])
    aal = info.aal if info else "aal1"
    if info is not None:
        if info.revoked_at is not None:
            raise HTTPException(status_code=401, detail="the session was revoked")
        info.last_refreshed_at = datetime.now(UTC)
        await info.save(update_fields=["last_refreshed_at"])
    return await _response(akountz, config, user, pair, aal)


async def revoke_session(user: AuthUser, family: str) -> bool:
    revoked = await JWTToken.revoke_family(family)
    updated = await SessionInfo.filter(user=user, family=family, revoked_at=None).update(
        revoked_at=datetime.now(UTC)
    )
    return bool(revoked or updated)


async def revoke_all(user: AuthUser) -> int:
    count = await user.revoke_all_tokens()
    await SessionInfo.filter(user=user, revoked_at=None).update(revoked_at=datetime.now(UTC))
    return count


async def list_sessions(user: AuthUser, *, current: str | None = None) -> list[dict[str, Any]]:
    active_families = set(
        await JWTToken.filter(
            user_id=user.id,
            token_type="refresh",
            revoked=False,
            consumed_at=None,
            expires_at__gt=datetime.now(UTC),
        ).values_list("token_family", flat=True)
    )
    result = []
    for info in await SessionInfo.filter(user=user, revoked_at=None).order_by("-created_at"):
        if info.family not in active_families:
            continue
        result.append(
            {
                "id": info.family,
                "method": info.method,
                "aal": info.aal,
                "ip": info.ip,
                "user_agent": info.user_agent,
                "created_at": info.created_at.isoformat() if info.created_at else None,
                "last_refreshed_at": info.last_refreshed_at.isoformat()
                if info.last_refreshed_at
                else None,
                "current": info.family == current,
            }
        )
    return result


async def elevate(akountz: Akountz, config: AuthConfig, user: AuthUser, family: str) -> None:
    """Mark a session as having passed MFA (``aal2``)."""
    await SessionInfo.filter(family=family, user=user).update(aal="aal2")
