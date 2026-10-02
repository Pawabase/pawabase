"""Sign-up, sign-in (password, refresh, MFA), sign-out and the current user."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created
from sillo.exceptions import HTTPException

from app import emails, links, mfa
from app.accounts import (
    check_password_policy,
    create_account,
    find_by_email,
    get_user,
    is_locked,
    normalise_email,
    record_failure,
    user_view,
)
from app.environment import AuthConfig, load_config
from app.platform import Akountz
from app.sessions import (
    KEEP,
    log_event,
    refresh_session,
    require_membership,
    revoke_all,
    revoke_session,
    start_session,
)
from database.models import AuthUser
from pawabase_core.context import require_context
from pawabase_core.principal import Principal
from pawabase_core.ratelimit import rate_limit_middleware


class SignUp(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    username: str | None = Field(default=None, max_length=150)
    name: str = Field(default="", max_length=200)
    data: dict[str, Any] = Field(default_factory=dict, description="Initial user_metadata")
    redirect_to: str | None = None


class TokenRequest(BaseModel):
    grant_type: Literal["password", "refresh_token", "mfa"] = "password"
    email: str | None = None
    password: str | None = None
    refresh_token: str | None = None
    mfa_token: str | None = None
    code: str | None = None
    #: An organization slug the tokens should be issued for. On refresh, switches
    #: the session's organization (``null`` leaves it); omit to keep it.
    org: str | None = Field(default=None, max_length=63)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    avatar_url: str | None = Field(default=None, max_length=2048)
    data: dict[str, Any] | None = Field(default=None, description="Merged into user_metadata")
    password: str | None = Field(default=None, max_length=1024)
    current_password: str | None = None
    email: str | None = None


class Logout(BaseModel):
    scope: Literal["local", "global", "others"] = "local"


async def config_for(akountz: Akountz, ctx: HttpContext) -> AuthConfig:
    context = require_context(ctx)
    return await load_config(akountz, context.project, context.env)


async def signed_in_user(ctx: HttpContext) -> tuple[AuthUser, Principal]:
    context = require_context(ctx)
    principal = ctx.scope.get("user")
    if not isinstance(principal, Principal) or principal.kind != "user":
        raise HTTPException(status_code=401, detail="sign in first")
    user = await get_user(context.project, context.env, principal.identity)
    if user is None or user.is_disabled:
        raise HTTPException(status_code=401, detail="the account is not available")
    return user, principal


def register(r: Router, akountz: Akountz) -> None:
    limits = rate_limit_middleware(
        {"limit": 30, "window": 60}, "akz:auth", akountz.settings.redis_url
    )

    @r.get("/settings", summary="What this environment offers for sign-in")
    async def settings(ctx: HttpContext):
        config = await config_for(akountz, ctx)
        return {
            "signup_enabled": config.signup_enabled,
            "email_verification_required": config.require_email_verification,
            "magic_link_enabled": config.magic_link_enabled,
            "mfa_enabled": config.mfa_enabled,
            "providers": config.enabled_providers(),
            "password_policy": {
                "kind": config.password_policy,
                "min_length": config.password_min_length,
            },
        }

    @r.post("/signup", request_model=SignUp, middleware=limits, summary="Create an account")
    async def signup(ctx: HttpContext, body: SignUp):
        config = await config_for(akountz, ctx)
        if not config.signup_enabled:
            raise HTTPException(status_code=403, detail="sign-up is disabled")
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        user = await create_account(
            config,
            email=body.email,
            password=body.password,
            username=body.username,
            name=body.name,
            user_metadata=body.data,
        )
        await akountz.emit(
            config.project,
            config.env,
            "user.created",
            {"user_id": str(user.id), "email": user.email, "method": "password"},
            actor=str(user.id),
        )
        if config.require_email_verification:
            token = await links.issue(akountz, config, "verify", user=user)
            await emails.send(
                akountz,
                config,
                "verify",
                user.email,
                link=links.link(config, "verify", token, body.redirect_to),
            )
            return created(
                {"user": await user_view(user), "session": None, "verification_required": True}
            )
        session = await start_session(akountz, config, user, method="password", ctx=ctx)
        return created({**session, "verification_required": False})

    @r.post(
        "/token",
        request_model=TokenRequest,
        middleware=limits,
        summary="Sign in, refresh, or complete MFA",
    )
    async def token(ctx: HttpContext, body: TokenRequest):
        config = await config_for(akountz, ctx)
        grant = ctx.query_params.get("grant_type") or body.grant_type
        if grant == "refresh_token":
            if not body.refresh_token:
                raise HTTPException(status_code=400, detail="refresh_token is required")
            return await refresh_session(
                akountz,
                config,
                body.refresh_token,
                ctx=ctx,
                org=body.org if "org" in body.model_fields_set else KEEP,
            )
        if grant == "mfa":
            return await _complete_mfa(ctx, config, body)
        if not body.email or not body.password:
            raise HTTPException(status_code=400, detail="email and password are required")
        email = normalise_email(body.email)
        user = await find_by_email(config.project, config.env, email)
        refusal = HTTPException(status_code=400, detail="invalid email or password")
        if user is None:
            await log_event(
                config.project,
                config.env,
                "sign_in",
                email=email,
                success=False,
                reason="unknown email",
                ctx=ctx,
            )
            raise refusal
        if is_locked(user):
            await log_event(
                config.project,
                config.env,
                "sign_in",
                user=user,
                success=False,
                reason="locked",
                ctx=ctx,
            )
            raise HTTPException(status_code=429, detail="too many failed attempts; try again later")
        if not user.check_password(body.password):
            await record_failure(
                user, akountz.settings.lockout_threshold, akountz.settings.lockout_minutes
            )
            await log_event(
                config.project,
                config.env,
                "sign_in",
                user=user,
                success=False,
                reason="wrong password",
                ctx=ctx,
            )
            raise refusal
        if user.is_disabled:
            await log_event(
                config.project,
                config.env,
                "sign_in",
                user=user,
                success=False,
                reason="disabled",
                ctx=ctx,
            )
            raise HTTPException(status_code=403, detail="this account is disabled")
        if config.require_email_verification and user.email_verified_at is None:
            raise HTTPException(status_code=403, detail="confirm your email address first")
        if user.mfa_enabled and config.mfa_enabled:
            await require_membership(config, user, body.org)
            challenge = await links.issue(
                akountz, config, "mfa", user=user, data={"method": "password", "org": body.org}
            )
            await log_event(config.project, config.env, "mfa_challenge", user=user, ctx=ctx)
            return {
                "mfa_required": True,
                "mfa_token": challenge,
                "factors": ["totp", "recovery_code"],
            }
        return await start_session(akountz, config, user, method="password", ctx=ctx, org=body.org)

    async def _complete_mfa(ctx: HttpContext, config: AuthConfig, body: TokenRequest):
        if not body.mfa_token or not body.code:
            raise HTTPException(status_code=400, detail="mfa_token and code are required")
        row = await links.consume(akountz, config, "mfa", body.mfa_token, peek=True)
        user = row.user
        if user is None or user.is_disabled:
            raise HTTPException(status_code=400, detail="the challenge is no longer valid")
        used = await mfa.verify(akountz, user, body.code)
        if used is None:
            await record_failure(
                user, akountz.settings.lockout_threshold, akountz.settings.lockout_minutes
            )
            await log_event(
                config.project,
                config.env,
                "mfa_verify",
                user=user,
                success=False,
                reason="wrong code",
                ctx=ctx,
            )
            raise HTTPException(status_code=400, detail="the code is not valid")
        await links.consume(akountz, config, "mfa", body.mfa_token)
        method = (row.data or {}).get("method", "password")
        org = (row.data or {}).get("org") or body.org
        session = await start_session(
            akountz, config, user, method=method, ctx=ctx, aal="aal2", org=org
        )
        session["mfa_method"] = used
        return session

    @r.post(
        "/logout",
        request_model=Logout,
        summary="Sign out this session, all sessions, or all others",
    )
    async def logout(ctx: HttpContext, body: Logout):
        user, principal = await signed_in_user(ctx)
        current = principal.claims.get("sid")
        if body.scope == "global":
            await revoke_all(user)
        elif body.scope == "others":
            from app.sessions import list_sessions

            for session in await list_sessions(user):
                if session["id"] != current:
                    await revoke_session(user, session["id"])
        elif current:
            await revoke_session(user, current)
        await log_event(user.project, user.env, "sign_out", user=user, method=body.scope, ctx=ctx)
        await akountz.emit(
            user.project,
            user.env,
            "session.revoked",
            {"user_id": str(user.id), "scope": body.scope},
            actor=str(user.id),
        )
        return {"signed_out": True, "scope": body.scope}

    @r.get("/user", summary="The signed-in user")
    async def me(ctx: HttpContext):
        user, principal = await signed_in_user(ctx)
        return {
            **await user_view(user),
            "aal": principal.claims.get("aal"),
            "session_id": principal.claims.get("sid"),
        }

    @r.patch("/user", request_model=UserUpdate, summary="Update the signed-in user")
    async def update_me(ctx: HttpContext, body: UserUpdate):
        user, _ = await signed_in_user(ctx)
        config = await load_config(akountz, user.project, user.env)
        changed: list[str] = []
        if body.name is not None:
            user.name = body.name
            changed.append("name")
        if body.avatar_url is not None:
            user.avatar_url = body.avatar_url
            changed.append("avatar_url")
        if body.data is not None:
            user.user_metadata = {**(user.user_metadata or {}), **body.data}
            changed.append("user_metadata")
        if body.password is not None:
            if user.has_usable_password() and not user.check_password(body.current_password or ""):
                raise HTTPException(status_code=400, detail="current_password is wrong")
            check_password_policy(config, body.password)
            user.set_password(body.password)
            changed.append("password")
        await user.save()
        if body.email is not None:
            email = normalise_email(body.email)
            if await find_by_email(user.project, user.env, email):
                raise HTTPException(status_code=409, detail="that email is in use")
            token = await links.issue(akountz, config, "email_change", user=user, email=email)
            await emails.send(
                akountz,
                config,
                "email_change",
                email,
                link=links.link(config, "email_change", token),
            )
            changed.append("email (pending confirmation)")
        if "password" in changed:
            await akountz.emit(
                user.project,
                user.env,
                "user.password_changed",
                {"user_id": str(user.id)},
                actor=str(user.id),
            )
        await akountz.emit(
            user.project,
            user.env,
            "user.updated",
            {"user_id": str(user.id), "changed": changed},
            actor=str(user.id),
        )
        return {**await user_view(user), "changed": changed}
