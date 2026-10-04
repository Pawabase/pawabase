"""Verification, password recovery, magic links and email changes.

Requests that email a link always answer the same way whether or not the
address has an account, so the endpoints cannot be used to find out who is
registered.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, html, redirect
from sillo.exceptions import HTTPException

from app import emails, links
from app.accounts import check_password_policy, create_account, find_by_email, normalise_email
from app.environment import AuthConfig, load_config
from app.platform import Akountz
from app.sessions import log_event, revoke_all, start_session
from pawabase_core.ratelimit import rate_limit_middleware
from routes.auth import config_for

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
    akountz: Akountz,
    config: AuthConfig,
    kind: str,
    token: str,
    *,
    ctx: HttpContext,
    password: str | None = None,
) -> dict:
    """Finish a link flow. Returns what the client should receive."""
    if kind == "verify":
        row = await links.consume(akountz, config, "verify", token)
        user = row.user
        if user.email_verified_at is None:
            await user.mark_email_verified()
            await akountz.emit(
                config.env,
                "user.verified",
                {"user_id": str(user.id), "email": user.email},
                actor=str(user.id),
            )
        return await start_session(akountz, config, user, method="email_verification", ctx=ctx)
    if kind == "magic":
        row = await links.consume(akountz, config, "magic", token)
        user = row.user
        if user.is_disabled:
            raise HTTPException(status_code=403, detail="this account is disabled")
        if user.email_verified_at is None:
            await user.mark_email_verified()
        if user.mfa_enabled and config.mfa_enabled:
            challenge = await links.issue(
                akountz, config, "mfa", user=user, data={"method": "magic_link"}
            )
            return {"mfa_required": True, "mfa_token": challenge}
        return await start_session(akountz, config, user, method="magic_link", ctx=ctx)
    if kind == "recovery":
        if password is None:
            await links.consume(akountz, config, "recovery", token, peek=True)
            return {"valid": True}
        check_password_policy(config, password)
        row = await links.consume(akountz, config, "recovery", token)
        user = row.user
        user.set_password(password)
        user.failed_logins = 0
        user.locked_until = None
        await user.save(update_fields=["password", "failed_logins", "locked_until"])
        await revoke_all(user)
        await log_event(
            config.env, "password_reset", user=user, method="recovery", ctx=ctx
        )
        await akountz.emit(
            config.env,
            "user.password_reset",
            {"user_id": str(user.id)},
            actor=str(user.id),
        )
        return await start_session(akountz, config, user, method="recovery", ctx=ctx)
    if kind == "email_change":
        row = await links.consume(akountz, config, "email_change", token)
        user = row.user
        if await find_by_email(config.env, row.email):
            raise HTTPException(status_code=409, detail="that email is in use")
        previous = user.email
        user.email = row.email
        user.email_verified_at = datetime.now(UTC)
        await user.save(update_fields=["email", "email_verified_at"])
        await akountz.emit(
            config.env,
            "user.email_changed",
            {"user_id": str(user.id), "from": previous, "to": user.email},
            actor=str(user.id),
        )
        return {"email": user.email, "changed": True}
    raise HTTPException(status_code=404, detail="unknown link type")


def register(r: Router, akountz: Akountz) -> None:
    limits = rate_limit_middleware(
        {"limit": 10, "window": 60}, "akz:links", akountz.settings.redis_url
    )

    @r.post(
        "/verify/request",
        request_model=EmailRequest,
        middleware=limits,
        summary="Send (again) an email verification link",
    )
    async def request_verification(ctx: HttpContext, body: EmailRequest):
        config = await config_for(akountz, ctx)
        user = await find_by_email(config.env, normalise_email(body.email))
        if user is not None and user.email_verified_at is None:
            token = await links.issue(akountz, config, "verify", user=user)
            await emails.send(
                akountz,
                config,
                "verify",
                user.email,
                link=links.link(config, "verify", token, body.redirect_to),
            )
        return SENT

    @r.post("/verify", request_model=TokenBody, summary="Confirm an email address")
    async def verify(ctx: HttpContext, body: TokenBody):
        return await complete(
            akountz, await config_for(akountz, ctx), "verify", body.token, ctx=ctx
        )

    @r.post(
        "/recover",
        request_model=EmailRequest,
        middleware=limits,
        summary="Send a password reset link",
    )
    async def recover(ctx: HttpContext, body: EmailRequest):
        config = await config_for(akountz, ctx)
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        user = await find_by_email(config.env, normalise_email(body.email))
        if user is not None and not user.is_disabled:
            token = await links.issue(akountz, config, "recovery", user=user)
            await emails.send(
                akountz,
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
            akountz,
            await config_for(akountz, ctx),
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
        config = await config_for(akountz, ctx)
        if not config.magic_link_enabled:
            raise HTTPException(status_code=403, detail="magic links are disabled")
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        email = normalise_email(body.email)
        user = await find_by_email(config.env, email)
        if user is None and body.create_user and config.signup_enabled:
            user = await create_account(config, email=email, password=None)
            await akountz.emit(
                config.env,
                "user.created",
                {"user_id": str(user.id), "email": email, "method": "magic_link"},
                actor=str(user.id),
            )
        if user is not None and not user.is_disabled:
            token = await links.issue(akountz, config, "magic", user=user)
            await emails.send(
                akountz,
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
        return await complete(akountz, await config_for(akountz, ctx), "magic", body.token, ctx=ctx)

    @r.post("/email/confirm", request_model=TokenBody, summary="Confirm an email change")
    async def email_confirm(ctx: HttpContext, body: TokenBody):
        return await complete(
            akountz, await config_for(akountz, ctx), "email_change", body.token, ctx=ctx
        )

    # Links opened from an email carry no API key: the environment is in the path.
    @r.get("/links/{env}/{kind}", exclude_from_schema=True)
    async def open_link(
        ctx: HttpContext,
        env: str,
        kind: Literal["verify", "magic", "email_change", "recovery"],
    ):
        config = await load_config(akountz, env)
        token = ctx.query_params.get("token", "")
        redirect_to = ctx.query_params.get("redirect_to")
        if kind == "recovery":
            return html(_recovery_page(env, token))
        try:
            result = await complete(akountz, config, kind, token, ctx=ctx)
        except HTTPException as exc:
            return html(
                _page("This link cannot be used", str(exc.detail)), status_code=exc.status_code
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
        return html(
            _page("You're all set", "You can close this window and return to the application.")
        )


def _page(title: str, message: str) -> str:
    import html as escape

    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>{escape.escape(title)}</title><body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        f"<h1>{escape.escape(title)}</h1><p>{escape.escape(message)}</p></body>"
    )


def _recovery_page(env: str, token: str) -> str:
    import html as escape
    import json

    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Choose a new password</title>"
        "<body style='font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem'>"
        "<h1>Choose a new password</h1><form id=f><input id=p type=password required minlength=8 autocomplete=new-password "
        "style='width:100%;padding:.5rem;font-size:1rem'><p><button style='padding:.5rem 1rem'>Save</button></p></form><p id=m></p>"
        "<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();"
        f"const r=await fetch('/auth/v1/links/{escape.escape(env)}/recovery',{{method:'POST',headers:{{'content-type':'application/json'}},"
        f"body:JSON.stringify({{token:{json.dumps(token)},password:document.getElementById('p').value}})}});"
        "const b=await r.json();document.getElementById('m').textContent=r.ok?'Your password was changed. You can sign in now.':(b.detail?.problems||[b.detail]).join(' ')}</script></body>"
    )


def register_keyless_recovery(r: Router, akountz: Akountz) -> None:
    @r.post("/links/{env}/recovery", request_model=ResetBody, exclude_from_schema=True)
    async def recovery_submit(ctx: HttpContext, env: str, body: ResetBody):
        config = await load_config(akountz, env)
        result = await complete(
            akountz, config, "recovery", body.token, ctx=ctx, password=body.password
        )
        return {"changed": True, "user": result.get("user")}
