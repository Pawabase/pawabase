"""Verification, password recovery, magic links and email changes.

Requests that email a link always answer the same way whether or not the
address has an account, so the endpoints cannot be used to find out who is
registered.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, html, redirect
from sillo.exceptions import HTTPException

from pawabase_server.core.ratelimit import rate_limit_middleware
from pawabase_server.identity import emails as auth_emails
from pawabase_server.identity import hosted, links, mfa
from pawabase_server.identity.accounts import (
    Accounts,
    create_account,
    find_by_email,
    normalise_email,
    set_password,
)
from pawabase_server.identity.environment import AuthConfig, load_config
from pawabase_server.identity.sessions import log_event, revoke_all, start_session
from pawabase_server.routes.identity.auth import config_for

SENT = {"sent": True, "message": "If the address has an account, a message is on its way."}


class EmailRequest(BaseModel):
    email: str
    redirect_to: str | None = None


class MagicLinkRequest(EmailRequest):
    create_user: bool = True


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=2048)


class ResetBody(TokenBody):
    password: str = Field(min_length=1, max_length=1024)


async def complete(
    accounts: Accounts,
    config: AuthConfig,
    kind: str,
    token: str,
    *,
    ctx: HttpContext,
    password: str | None = None,
) -> dict:
    """Finish a link flow. Returns what the client should receive."""
    if kind == "verify":
        row = await links.consume(accounts, config, "verify", token)
        user = row.user
        if user.email_verified_at is None:
            await user.mark_email_verified()
            await accounts.emit(
                config.env,
                "user.verified",
                {"user_id": str(user.id), "email": user.email},
                actor=str(user.id),
            )
        return await start_session(accounts, config, user, method="email_verification", ctx=ctx)
    if kind == "magic":
        row = await links.consume(accounts, config, "magic", token)
        user = row.user
        if user.is_disabled:
            raise HTTPException(status_code=403, detail="this account is disabled")
        if user.email_verified_at is None:
            await user.mark_email_verified()
        if user.mfa_enabled and config.mfa_enabled:
            return await mfa.challenge_payload(
                accounts, config, user, data={"method": "magic_link"}
            )
        return await start_session(accounts, config, user, method="magic_link", ctx=ctx)
    if kind == "recovery":
        if password is None:
            await links.consume(accounts, config, "recovery", token, peek=True)
            return {"valid": True}
        row = await links.consume(accounts, config, "recovery", token, peek=True)
        user = row.user
        await set_password(config, user, password)
        await links.consume(accounts, config, "recovery", token)
        user.failed_logins = 0
        user.locked_until = None
        await user.save(
            update_fields=["password", "password_changed_at", "failed_logins", "locked_until"]
        )
        if config.policy("recovery")["revoke_sessions"]:
            await revoke_all(user)
        if config.policy("recovery")["notify_change"]:
            await auth_emails.send(accounts, config, "password_changed", user.email)
        await log_event(config.env, "password_reset", user=user, method="recovery", ctx=ctx)
        await accounts.emit(
            config.env,
            "user.password_reset",
            {"user_id": str(user.id)},
            actor=str(user.id),
        )
        return await start_session(accounts, config, user, method="recovery", ctx=ctx)
    if kind == "email_change":
        row = await links.consume(accounts, config, "email_change", token)
        user = row.user
        if await find_by_email(config.env, row.email):
            raise HTTPException(status_code=409, detail="that email is in use")
        previous = user.email
        user.email = row.email
        user.email_verified_at = datetime.now(UTC)
        await user.save(update_fields=["email", "email_verified_at"])
        await accounts.emit(
            config.env,
            "user.email_changed",
            {"user_id": str(user.id), "from": previous, "to": user.email},
            actor=str(user.id),
        )
        return {"email": user.email, "changed": True}
    raise HTTPException(status_code=404, detail="unknown link type")


def register(r: Router, accounts: Accounts) -> None:
    limits = rate_limit_middleware(
        {"limit": 10, "window": 60}, "akz:links", accounts.settings.redis_url
    )

    @r.post(
        "/verify/request",
        request_model=EmailRequest,
        middleware=limits,
        summary="Send (again) an email verification link",
    )
    async def request_verification(ctx: HttpContext, body: EmailRequest):
        config = await config_for(accounts, ctx)
        user = await find_by_email(config.env, normalise_email(body.email))
        if user is not None and user.email_verified_at is None:
            token = await links.issue(accounts, config, "verify", user=user)
            await auth_emails.send(
                accounts,
                config,
                "verify",
                user.email,
                link=links.link(config, "verify", token, body.redirect_to),
            )
        return SENT

    @r.post("/verify", request_model=TokenBody, summary="Confirm an email address")
    async def verify(ctx: HttpContext, body: TokenBody):
        return await complete(
            accounts, await config_for(accounts, ctx), "verify", body.token, ctx=ctx
        )

    @r.post(
        "/recover",
        request_model=EmailRequest,
        middleware=limits,
        summary="Send a password reset link",
    )
    async def recover(ctx: HttpContext, body: EmailRequest):
        config = await config_for(accounts, ctx)
        if not config.policy("recovery")["enabled"]:
            raise HTTPException(status_code=403, detail="password recovery is turned off")
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        user = await find_by_email(config.env, normalise_email(body.email))
        if user is not None and not user.is_disabled:
            token = await links.issue(accounts, config, "recovery", user=user)
            await auth_emails.send(
                accounts,
                config,
                "recovery",
                user.email,
                link=links.link(config, "recovery", token, body.redirect_to),
            )
            await log_event(config.env, "recovery_requested", user=user, ctx=ctx)
        return SENT

    @r.post(
        "/recover/confirm", request_model=ResetBody, summary="Set a new password with a reset link"
    )
    async def recover_confirm(ctx: HttpContext, body: ResetBody):
        return await complete(
            accounts,
            await config_for(accounts, ctx),
            "recovery",
            body.token,
            ctx=ctx,
            password=body.password,
        )

    @r.post(
        "/magic-link",
        request_model=MagicLinkRequest,
        middleware=limits,
        summary="Email a sign-in link",
    )
    async def magic_link(ctx: HttpContext, body: MagicLinkRequest):
        config = await config_for(accounts, ctx)
        if not config.magic_link_enabled:
            raise HTTPException(status_code=403, detail="magic links are disabled")
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        email = normalise_email(body.email)
        user = await find_by_email(config.env, email)
        if user is None and body.create_user and config.signup_enabled:
            user = await create_account(
                config, email=email, password=None, max_users=accounts.max_users(config.env)
            )
            await accounts.emit(
                config.env,
                "user.created",
                {"user_id": str(user.id), "email": email, "method": "magic_link"},
                actor=str(user.id),
            )
        if user is not None and not user.is_disabled:
            token = await links.issue(accounts, config, "magic", user=user)
            await auth_emails.send(
                accounts,
                config,
                "magic",
                email,
                link=links.link(config, "magic", token, body.redirect_to),
            )
        return SENT

    @r.post(
        "/magic-link/verify", request_model=TokenBody, summary="Sign in with a magic link token"
    )
    async def magic_verify(ctx: HttpContext, body: TokenBody):
        return await complete(
            accounts, await config_for(accounts, ctx), "magic", body.token, ctx=ctx
        )

    @r.post("/email/confirm", request_model=TokenBody, summary="Confirm an email change")
    async def email_confirm(ctx: HttpContext, body: TokenBody):
        return await complete(
            accounts, await config_for(accounts, ctx), "email_change", body.token, ctx=ctx
        )

    # Links opened from an email carry no API key: the environment is in the path.
    @r.get("/links/{env}/{kind}", exclude_from_schema=True)
    async def open_link(
        ctx: HttpContext,
        env: str,
        kind: Literal["verify", "magic", "email_change", "recovery"],
    ):
        config = await load_config(accounts, env)
        token = ctx.query_params.get("token", "")
        redirect_to = ctx.query_params.get("redirect_to")
        if kind == "recovery":
            # The branded page, with the token carried across.
            return redirect(f"/auth/v1/hosted/{env}/reset?{urlencode({'token': token})}")
        try:
            result = await complete(accounts, config, kind, token, ctx=ctx)
        except HTTPException as exc:
            return _notice(
                config, "This link cannot be used", str(exc.detail), status_code=exc.status_code
            )
        if redirect_to and config.redirect_allowed(redirect_to) and "access_token" in result:
            fragment = urlencode(
                {
                    k: result[k]
                    for k in ("access_token", "refresh_token", "expires_in", "token_type")
                    if k in result
                }
            )
            return redirect(f"{redirect_to}#{fragment}")
        return _notice(
            config, "You're all set", "You can close this window and return to the application."
        )


def register_keyless_recovery(r: Router, accounts: Accounts) -> None:
    @r.post("/links/{env}/recovery", request_model=ResetBody, exclude_from_schema=True)
    async def recovery_submit(ctx: HttpContext, env: str, body: ResetBody):
        config = await load_config(accounts, env)
        result = await complete(
            accounts, config, "recovery", body.token, ctx=ctx, password=body.password
        )
        return {"changed": True, "user": result.get("user")}


def _notice(config: AuthConfig, title: str, message: str, *, status_code: int = 200):
    """A branded page that only says something: a link that was used, or one that could not be."""
    nonce = secrets.token_urlsafe(16)
    body = hosted.render(
        "notice",
        env=config.env,
        project_name=config.project_name,
        branding=config.branding,
        providers=[],
        signup_enabled=False,
        magic_link_enabled=False,
        query={},
        nonce=nonce,
        base=f"/auth/v1/hosted/{config.env}",
        notice=(title, message, "Go to sign in", f"/auth/v1/hosted/{config.env}/login"),
    )
    return html(
        body,
        status_code=status_code,
        headers={
            "content-security-policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                "img-src https: data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "x-frame-options": "DENY",
            "cache-control": "no-store",
        },
    )
