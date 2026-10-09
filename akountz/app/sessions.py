"""Issuing, refreshing and revoking sessions.

Sillo does the token lifecycle. ``JWTUserMixin.issue_token_pair`` creates a
token family with tracked access and refresh JTIs, and ``refresh_token_pair``
rotates it, detecting reuse and revoking the whole family when a consumed
refresh token comes back. Pawabase adds what Sillo's tokens do not carry: the
environment, roles, permissions and assurance level, in an access
token re-minted under the tracked JTI and signed with the environment's key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sillo.exceptions import HTTPException

from app import rbac
from app.accounts import user_view
from app.environment import AuthConfig
from app.platform import Akountz
from database.models import AuthUser, JWTToken, LoginEvent, Membership, Organization, SessionInfo
from pawabase_core.ids import is_ulid
from pawabase_core.tokens import issue_user_token, peek_claims


def client_info(ctx: Any) -> tuple[str | None, str | None]:
    forwarded = ctx.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (ctx.client.host if ctx.client else None)
    return ip, (ctx.headers.get("user-agent") or "")[:500]


async def log_event(
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


#: ``refresh_session(org=KEEP)`` keeps the session's organization.
KEEP: Any = object()


async def membership_role(config: AuthConfig, user: AuthUser, slug: str | None) -> str | None:
    """The user's role in organization *slug* of this environment, or None."""
    if not slug:
        return None
    org = await Organization.get_or_none(env=config.env, slug=slug)
    if org is None:
        return None
    member = await Membership.get_or_none(organization=org, user=user)
    return member.role if member else None


async def require_membership(config: AuthConfig, user: AuthUser, slug: str | None) -> str | None:
    """Validate a requested organization: the slug, or 403 if the user isn't a member."""
    if not slug:
        return None
    if await membership_role(config, user, slug) is None:
        raise HTTPException(status_code=403, detail="you are not a member of that organization")
    return slug


async def _access_token(
    akountz: Akountz,
    config: AuthConfig,
    user: AuthUser,
    *,
    jti: str,
    family: str,
    aal: str,
    org: str | None = None,
    org_role: str | None = None,
) -> str:
    return issue_user_token(
        akountz.settings.jwt_master_secret,
        env=config.env,
        user_id=str(user.id),
        jti=jti,
        session_id=family,
        ttl=config.access_ttl,
        email=user.email,
        roles=await rbac.roles_of(user),
        permissions=await rbac.permissions_of(user),
        org=org,
        org_role=org_role,
        aal=aal,
        extra=user.user_metadata or None,
        app=user.app_metadata or None,
    )


async def _response(
    akountz: Akountz,
    config: AuthConfig,
    user: AuthUser,
    pair: dict[str, Any],
    aal: str,
    org: str | None = None,
) -> dict[str, Any]:
    claims = peek_claims(pair["access_token"]) or {}
    # The role is read at issue time, so a membership change applies on the next refresh.
    org_role = await membership_role(config, user, org)
    if org_role is None:
        org = None
    access = await _access_token(
        akountz,
        config,
        user,
        jti=claims.get("jti", ""),
        family=pair["token_family"],
        aal=aal,
        org=org,
        org_role=org_role,
    )
    return {
        "access_token": access,
        "refresh_token": pair["refresh_token"],
        "token_type": "bearer",
        "expires_in": config.access_ttl,
        "expires_at": int(datetime.now(UTC).timestamp()) + config.access_ttl,
        "session_id": pair["token_family"],
        "org": org,
        "org_role": org_role,
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
    org: str | None = None,
) -> dict[str, Any]:
    org = await require_membership(config, user, org)
    pair = await user.issue_token_pair(
        secret=akountz.refresh_secret(config.env),
        access_expires=timedelta(seconds=config.access_ttl),
        refresh_expires=timedelta(seconds=config.refresh_ttl),
    )
    ip, agent = client_info(ctx) if ctx is not None else (None, None)
    await SessionInfo.create(
        family=pair["token_family"],
        user=user,
        env=config.env,
        method=method,
        aal=aal,
        org=org,
        ip=ip,
        user_agent=agent,
    )
    from app.accounts import record_success

    await record_success(user, ip)
    await log_event(config.env, "sign_in", user=user, method=method, ctx=ctx)
    await akountz.emit(
        config.env,
        "user.signed_in",
        {"user_id": str(user.id), "method": method},
        actor=str(user.id),
    )
    return await _response(akountz, config, user, pair, aal, org)


async def refresh_session(
    akountz: Akountz,
    config: AuthConfig,
    refresh_token: str,
    *,
    ctx: Any = None,
    org: Any = KEEP,
) -> dict[str, Any]:
    """Rotate a refresh token. ``org`` switches the session's organization
    (``None`` or ``""`` leaves every organization); omitted, it's kept."""
    claims = peek_claims(refresh_token) or {}
    user = (
        await AuthUser.filter(
            id=str(claims["sub"]).upper(),
            env=config.env,
            deleted_at=None,
        ).first()
        if is_ulid(claims.get("sub"))
        else None
    )
    if user is None or user.is_disabled:
        raise HTTPException(status_code=401, detail="invalid refresh token")
    try:
        pair = await user.refresh_token_pair(refresh_token, akountz.refresh_secret(config.env))
    except ValueError as exc:
        await log_event(
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
                config.env,
                "session.reuse_detected",
                {"user_id": str(user.id)},
                actor=str(user.id),
            )
        raise HTTPException(status_code=401, detail="invalid refresh token") from exc
    info = await SessionInfo.get_or_none(family=pair["token_family"])
    aal = info.aal if info else "aal1"
    active = info.org if info else None
    if org is not KEEP:
        active = await require_membership(config, user, org or None)
    if info is not None:
        if info.revoked_at is not None:
            raise HTTPException(status_code=401, detail="the session was revoked")
        info.last_refreshed_at = datetime.now(UTC)
        info.org = active
        await info.save(update_fields=["last_refreshed_at", "org"])
    return await _response(akountz, config, user, pair, aal, active)


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
