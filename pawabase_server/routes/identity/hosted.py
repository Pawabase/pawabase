"""The hosted sign-in pages, and the endpoints they post to.

The pages are served without an API key (the environment is in the path), so a person can be sent straight to them from any app.
A successful sign-in never puts tokens in a URL: the page is given a short-lived, single-use *code*, and the app exchanges it at
``POST /auth/v1/token/exchange`` with its publishable key (and, when it started with a ``code_challenge``, the matching verifier).
"""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, html
from sillo.exceptions import HTTPException

from pawabase_server.core.ratelimit import rate_limit_middleware
from pawabase_server.identity import devices, federation, hosted, legal, links, mfa, signup_form
from pawabase_server.identity import emails as auth_emails
from pawabase_server.identity.accounts import (
    Accounts,
    create_account,
    find_by_email,
    normalise_email,
)
from pawabase_server.identity.environment import AuthConfig, load_config
from pawabase_server.identity.sessions import log_event
from pawabase_server.routes.identity.auth import authenticate_password, verify_second_factor
from pawabase_server.routes.identity.recovery import complete

SENT = {"sent": True, "message": "If the address has an account, a message is on its way."}


class Hosted(BaseModel):
    redirect_to: str | None = Field(default=None, max_length=2048)
    state: str | None = Field(default=None, max_length=512)
    code_challenge: str | None = Field(default=None, max_length=256)


class SignIn(Hosted):
    email: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=1024)
    mfa_token: str | None = None
    remember_device: bool = False
    code: str | None = Field(default=None, max_length=64)


class SignUp(Hosted):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)
    name: str | None = Field(default=None, max_length=200)
    #: Answers to the extra questions the environment asks at sign-up, by key.
    fields: dict[str, Any] = Field(default_factory=dict)
    accepted_terms: bool = False


class MfaStart(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=2048)
    method: str = Field(max_length=16)


class MfaFinish(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=2048)
    code: str = Field(min_length=6, max_length=32)


class MfaSend(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=2048)


class Reset(BaseModel):
    token: str = Field(min_length=1, max_length=2048)
    password: str = Field(max_length=1024)


class Email(Hosted):
    email: str = Field(max_length=320)


def register(r: Router, accounts: Accounts) -> None:
    limits = rate_limit_middleware(
        {"limit": 30, "window": 60}, "akz:hosted", accounts.settings.redis_url
    )

    async def config_of(env: str) -> AuthConfig:
        try:
            return await load_config(accounts, env)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=404, detail="no such environment") from exc

    def headers(nonce: str, *, framed: bool = False) -> dict[str, str]:
        """A sign-in page may never be framed (clickjacking); the preview Studio shows inside itself carries no session and may be."""
        out = {
            "content-security-policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src 'self' https: data:; "
                f"connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors {'*' if framed else chr(39) + 'none' + chr(39)}"
            ),
            "referrer-policy": "no-referrer",
            "cache-control": "no-store",
        }
        if not framed:
            out["x-frame-options"] = "DENY"
        return out

    def build(
        page: str,
        config: AuthConfig,
        ctx: HttpContext,
        *,
        branding: Any,
        preview: bool,
        form: Any = None,
    ):
        nonce = secrets.token_urlsafe(16)
        query = {
            k: v
            for k, v in ctx.query_params.items()
            if k in ("redirect_to", "state", "code_challenge", "token")
        }
        if query.get("redirect_to") and not config.redirect_allowed(query["redirect_to"]):
            return html(
                hosted.render(
                    "notice",
                    env=config.env,
                    project_name=config.project_name,
                    branding=branding,
                    providers=[],
                    signup_enabled=False,
                    magic_link_enabled=False,
                    query={},
                    nonce=nonce,
                    base=f"/auth/v1/hosted/{config.env}",
                    notice=(
                        "This sign-in link is not allowed",
                        "The app that sent you here is not on this project's list of allowed addresses.",
                        "",
                        "",
                    ),
                ),
                status_code=400,
                headers=headers(nonce),
            )
        body = hosted.render(
            page,
            env=config.env,
            project_name=config.project_name,
            branding=branding,
            providers=config.enabled_providers(),
            signup_enabled=config.signup_enabled,
            magic_link_enabled=config.magic_link_enabled,
            query=query,
            nonce=nonce,
            base=f"/auth/v1/hosted/{config.env}",
            preview=preview,
            password_min=config.password_min_length,
            password_rules=config.password_rules,
            remember_days=config.policy("devices")["remember_days"],
            legal=config.legal,
            discover=any(
                c.get("domains")
                for n, c in config.providers.items()
                if n in config.enabled_providers()
            ),
            form=config.signup_form if form is None else form,
        )
        response = html(body, headers=headers(nonce, framed=preview))
        if not preview and not devices.identify(ctx)[1]:
            response.set_cookie(
                devices.COOKIE,
                devices.new_id(),
                max_age=400 * 24 * 3600,
                httponly=True,
                samesite="lax",
                secure=ctx.url.scheme == "https",
            )
        return response

    @r.get("/hosted/{env}/preview/{page}", exclude_from_schema=True)
    async def preview(ctx: HttpContext, env: str, page: str):
        if page not in hosted.PREVIEW_PAGES:
            raise HTTPException(status_code=404, detail="no such page")
        config = await config_of(env)
        raw = ctx.query_params.get("b", "")
        try:
            decoded = (
                json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode())
                if raw
                else config.branding
            )
        except ValueError:
            decoded = config.branding
        raw_form = ctx.query_params.get("f", "")
        try:
            form = (
                signup_form.clean(
                    json.loads(
                        base64.urlsafe_b64decode(raw_form + "=" * (-len(raw_form) % 4)).decode()
                    )
                )
                if raw_form
                else None
            )
        except ValueError:
            form = None
        return build(page, config, ctx, branding=decoded, preview=True, form=form)

    @r.get("/hosted/{env}/discover/provider", exclude_from_schema=True)
    async def discover_provider(ctx: HttpContext, env: str):
        """Which company sign-in an email address belongs to, so the page can send the person straight there."""
        config = await config_of(env)
        name = federation.domain_provider(config, ctx.query_params.get("email", ""))
        if name is None:
            return {"provider": None}
        carried = {
            k: v
            for k, v in ctx.query_params.items()
            if k in ("redirect_to", "state", "code_challenge") and v
        }
        query = f"?{urlencode(carried)}" if carried else ""
        return {"provider": name, "redirect": f"/auth/v1/authorize/{env}/{name}{query}"}

    @r.get("/hosted/{env}/{page}", exclude_from_schema=True)
    async def serve(ctx: HttpContext, env: str, page: str):
        if page not in hosted.PAGES:
            raise HTTPException(status_code=404, detail="no such page")
        config = await config_of(env)
        return build(page, config, ctx, branding=config.branding, preview=False)

    async def finish(
        config: AuthConfig,
        user,
        body: Hosted,
        *,
        method: str,
        aal: str = "aal1",
        org: str | None = None,
        ctx: HttpContext | None = None,
        trust: bool = False,
    ):
        """The sign-in succeeded: hand the page a code to take back to the app, or say so when there is nowhere to go."""
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        if aal == "aal1" and await mfa.setup_required(config, user):
            token = await links.issue(
                accounts,
                config,
                "mfa_setup",
                user=user,
                data={
                    "method": method,
                    "org": org,
                    "redirect_to": body.redirect_to,
                    "state": body.state,
                    "code_challenge": body.code_challenge,
                },
            )
            return {
                "mfa_setup_required": True,
                "mfa_token": token,
                "methods": mfa.methods_offered(config),
            }
        target = body.redirect_to or (config.site_url or "")
        if not target:
            return {"signed_in": True}
        code = await links.issue(
            accounts,
            config,
            "code",
            user=user,
            data={
                "method": method,
                "aal": aal,
                "org": org,
                "challenge": body.code_challenge,
                "device": devices.identify(ctx)[0] if ctx is not None else None,
                "trust": trust,
            },
        )
        query = {"code": code}
        if body.state:
            query["state"] = body.state
        joiner = "&" if "?" in target else "?"
        return {"redirect": f"{target}{joiner}{urlencode(query)}"}

    @r.post(
        "/hosted/{env}/signin", request_model=SignIn, middleware=limits, exclude_from_schema=True
    )
    async def signin(ctx: HttpContext, env: str, body: SignIn):
        config = await config_of(env)
        if body.mfa_token:
            user, method, org, _ = await verify_second_factor(
                accounts, config, body.mfa_token, body.code, ctx
            )
            return await finish(
                config,
                user,
                body,
                method=method,
                aal="aal2",
                org=org,
                ctx=ctx,
                trust=body.remember_device,
            )
        user, challenge = await authenticate_password(
            accounts, config, body.email, body.password, None, ctx
        )
        if challenge is not None:
            return challenge
        return await finish(config, user, body, method="password", ctx=ctx)

    @r.post(
        "/hosted/{env}/signup", request_model=SignUp, middleware=limits, exclude_from_schema=True
    )
    async def signup(ctx: HttpContext, env: str, body: SignUp):
        config = await config_of(env)
        if not config.signup_enabled:
            raise HTTPException(status_code=403, detail="sign-up is disabled")
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        collected = signup_form.collect(
            config.signup_form,
            {"name": body.name, **body.fields},
            config.branding.get("signup_fields"),
        )
        problems = list(collected.problems)
        docs = legal.documents(config.legal, config.branding)
        if refusal := legal.problem(docs, body.accepted_terms):
            problems.append(refusal)
        if problems:
            raise HTTPException(status_code=422, detail=" ".join(problems))
        user = await create_account(
            config,
            email=body.email,
            password=body.password,
            username=collected.username,
            phone=collected.phone,
            name=collected.name or body.name or "",
            user_metadata=collected.metadata or None,
            app_metadata=(
                {"consent": record} if (record := legal.consent(config.legal, docs)) else None
            ),
            max_users=accounts.max_users(config.env),
        )
        await accounts.emit(
            config.env,
            "user.created",
            {"user_id": str(user.id), "email": user.email, "method": "password"},
            actor=str(user.id),
        )
        if config.require_email_verification:
            token = await links.issue(accounts, config, "verify", user=user)
            await auth_emails.send(
                accounts,
                config,
                "verify",
                user.email,
                link=links.link(config, "verify", token, body.redirect_to),
            )
            return {"verify": True}
        return await finish(config, user, body, method="password", ctx=ctx)

    @r.post(
        "/hosted/{env}/mfa/start",
        request_model=MfaStart,
        middleware=limits,
        exclude_from_schema=True,
    )
    async def mfa_start(ctx: HttpContext, env: str, body: MfaStart):
        config = await config_of(env)
        _, started = await mfa.setup_begin(accounts, config, body.mfa_token, body.method)
        return started

    @r.post(
        "/hosted/{env}/mfa/finish",
        request_model=MfaFinish,
        middleware=limits,
        exclude_from_schema=True,
    )
    async def mfa_finish(ctx: HttpContext, env: str, body: MfaFinish):
        config = await config_of(env)
        user, data, codes = await mfa.setup_finish(accounts, config, body.mfa_token, body.code)
        await log_event(config.env, "mfa_enrolled", user=user, method="setup", ctx=ctx)
        carried = Hosted(
            redirect_to=data.get("redirect_to"),
            state=data.get("state"),
            code_challenge=data.get("code_challenge"),
        )
        outcome = await finish(
            config,
            user,
            carried,
            method=data.get("method", "password"),
            aal="aal2",
            org=data.get("org"),
            ctx=ctx,
        )
        return {**outcome, "recovery_codes": codes}

    @r.post(
        "/hosted/{env}/mfa/send",
        request_model=MfaSend,
        middleware=limits,
        exclude_from_schema=True,
    )
    async def mfa_send(ctx: HttpContext, env: str, body: MfaSend):
        config = await config_of(env)
        row = await links.consume(accounts, config, "mfa", body.mfa_token, peek=True)
        if row.user is None or "email" not in await mfa.kinds(row.user):
            raise HTTPException(
                status_code=400, detail="email codes are not set up for this account"
            )
        await mfa.send_email_code(accounts, config, row.user)
        return {"sent": True}

    @r.post(
        "/hosted/{env}/forgot", request_model=Email, middleware=limits, exclude_from_schema=True
    )
    async def forgot(ctx: HttpContext, env: str, body: Email):
        config = await config_of(env)
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

    @r.post("/hosted/{env}/magic", request_model=Email, middleware=limits, exclude_from_schema=True)
    async def magic(ctx: HttpContext, env: str, body: Email):
        config = await config_of(env)
        if not config.magic_link_enabled:
            raise HTTPException(status_code=403, detail="magic links are disabled")
        if body.redirect_to and not config.redirect_allowed(body.redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        user = await find_by_email(config.env, normalise_email(body.email))
        if user is not None and not user.is_disabled:
            token = await links.issue(accounts, config, "magic", user=user)
            await auth_emails.send(
                accounts,
                config,
                "magic",
                user.email,
                link=links.link(config, "magic", token, body.redirect_to),
            )
        return SENT

    @r.post("/hosted/{env}/reset", request_model=Reset, middleware=limits, exclude_from_schema=True)
    async def reset(ctx: HttpContext, env: str, body: Reset):
        config = await config_of(env)
        result = await complete(
            accounts, config, "recovery", body.token, ctx=ctx, password=body.password
        )
        return {"changed": True, "user": result.get("user")}
