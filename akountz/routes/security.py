"""OAuth sign-in and linking, MFA, sessions, and sign-in history for the user."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, no_content, redirect
from sillo.exceptions import HTTPException
from sillo_oauth import OAuthError, authorize_url, exchange

from app import mfa, oauth
from app.accounts import create_account, find_by_email
from app.environment import load_config
from app.platform import Akountz
from app.sessions import elevate, list_sessions, log_event, revoke_session, start_session
from database.models import AuthUser, Identity, LoginEvent
from pawabase_core.ratelimit import rate_limit_middleware
from routes.auth import signed_in_user


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=32)


def _error_redirect(target: str | None, code: str) -> object:
    if target:
        return redirect(f"{target}#{urlencode({'error': code})}")
    raise HTTPException(status_code=400, detail=f"sign-in failed: {code}")


def register(r: Router, akountz: Akountz) -> None:
    mfa_limits = rate_limit_middleware(
        {"limit": 10, "window": 60}, "akz:mfa", akountz.settings.redis_url
    )

    # ── OAuth (browser navigations: the environment is in the path) ──────

    @r.get(
        "/authorize/{project}/{env}/{provider}", summary="Start a social sign-in", tags=["oauth"]
    )
    async def start(ctx: HttpContext, project: str, env: str, provider: str):
        config = await load_config(akountz, project, env)
        redirect_to = ctx.query_params.get("redirect_to") or config.site_url or None
        if redirect_to and not config.redirect_allowed(redirect_to):
            raise HTTPException(status_code=400, detail="redirect_to is not an allowed URL")
        link_token = ctx.query_params.get("link_token")
        state = (
            {"redirect_to": redirect_to, "link": link_token}
            if link_token
            else {"redirect_to": redirect_to}
        )
        provider_obj = oauth.build_provider(akountz, config, provider)
        import json

        authorize = authorize_url(provider_obj, return_to=json.dumps(state))
        secure = (config.public_url or akountz.settings.public_url).startswith("https://")
        return redirect(authorize.url).set_cookie(**authorize.cookie_kwargs(secure=secure))

    @r.get(
        "/callback/{project}/{env}/{provider}",
        summary="Finish a social sign-in",
        tags=["oauth"],
        exclude_from_schema=True,
    )
    async def callback(ctx: HttpContext, project: str, env: str, provider: str):
        import json

        config = await load_config(akountz, project, env)
        provider_obj = oauth.build_provider(akountz, config, provider)
        try:
            profile = await exchange(provider_obj, ctx)
        except OAuthError as exc:
            await log_event(
                project,
                env,
                "sign_in",
                method=f"oauth:{provider}",
                success=False,
                reason=exc.code,
                ctx=ctx,
            )
            return _error_redirect(config.site_url or None, exc.code)
        try:
            state = json.loads(profile.return_to or "{}")
        except ValueError:
            state = {}
        target = state.get("redirect_to")
        if target and not config.redirect_allowed(target):
            target = None

        identity = (
            await Identity.filter(
                project=project, env=env, provider=provider, subject=profile.subject
            )
            .prefetch_related("user")
            .first()
        )
        user: AuthUser | None = identity.user if identity else None
        if state.get("link"):
            from app import links

            row = await links.consume(akountz, config, "link", state["link"])
            if identity is not None and identity.user_id != row.user_id:
                return _error_redirect(target, "identity_already_linked")
            user = row.user
        if user is None and profile.email and profile.email_verified:
            # Only a verified address may attach a new identity to an existing account.
            user = await find_by_email(project, env, profile.email)
        created = False
        if user is None:
            if not config.signup_enabled:
                return _error_redirect(target, "signup_disabled")
            if not profile.email:
                return _error_redirect(target, "email_required")
            if await find_by_email(project, env, profile.email):
                return _error_redirect(target, "email_unverified_conflict")
            user = await create_account(
                config,
                email=profile.email,
                password=None,
                username=profile.username,
                name=profile.name or "",
                verified=bool(profile.email_verified),
            )
            user.avatar_url = profile.avatar_url
            await user.save(update_fields=["avatar_url"])
            created = True
        if user.is_disabled:
            return _error_redirect(target, "account_disabled")
        now = datetime.now(UTC)
        if identity is None:
            await Identity.create(
                user=user,
                project=project,
                env=env,
                provider=provider,
                subject=profile.subject,
                email=profile.email,
                email_verified=bool(profile.email_verified),
                data=profile.raw or {},
                last_sign_in_at=now,
            )
            await akountz.emit(
                project,
                env,
                "identity.linked",
                {"user_id": str(user.id), "provider": provider},
                actor=str(user.id),
            )
        else:
            identity.last_sign_in_at = now
            identity.data = profile.raw or identity.data
            await identity.save(update_fields=["last_sign_in_at", "data"])
        if created:
            await akountz.emit(
                project,
                env,
                "user.created",
                {"user_id": str(user.id), "email": user.email, "method": f"oauth:{provider}"},
                actor=str(user.id),
            )
        if state.get("link"):
            return (
                redirect(f"{target}#{urlencode({'linked': provider})}")
                if target
                else {"linked": provider}
            )
        if user.mfa_enabled and config.mfa_enabled:
            from app import links

            challenge = await links.issue(
                akountz, config, "mfa", user=user, data={"method": f"oauth:{provider}"}
            )
            payload = {"mfa_required": "true", "mfa_token": challenge}
        else:
            session = await start_session(
                akountz, config, user, method=f"oauth:{provider}", ctx=ctx
            )
            payload = {
                k: session[k] for k in ("access_token", "refresh_token", "expires_in", "token_type")
            }
        if target:
            return redirect(f"{target}#{urlencode(payload)}")
        return payload

    @r.post(
        "/identities/link",
        summary="Get a URL that links a provider to this account",
        tags=["oauth"],
    )
    async def link_identity(ctx: HttpContext):
        from app import links

        user, _ = await signed_in_user(ctx)
        config = await load_config(akountz, user.project, user.env)
        provider = ctx.query_params.get("provider", "")
        oauth.build_provider(akountz, config, provider)
        ticket = await links.issue(akountz, config, "link", user=user, data={"linking": provider})
        base = (config.public_url or akountz.settings.public_url).rstrip("/")
        query = {
            "link_token": ticket,
            **(
                {"redirect_to": ctx.query_params["redirect_to"]}
                if ctx.query_params.get("redirect_to")
                else {}
            ),
        }
        return {
            "url": f"{base}/auth/v1/authorize/{user.project}/{user.env}/{provider}?{urlencode(query)}"
        }

    @r.delete("/identities/{identity_id}", summary="Unlink an identity", tags=["oauth"])
    async def unlink_identity(ctx: HttpContext, identity_id: int):
        user, _ = await signed_in_user(ctx)
        identity = await Identity.get_or_none(id=identity_id, user=user)
        if identity is None:
            raise HTTPException(status_code=404, detail="no such identity")
        if not user.has_usable_password() and await Identity.filter(user=user).count() <= 1:
            raise HTTPException(
                status_code=409, detail="set a password before removing your only sign-in method"
            )
        await identity.delete()
        await akountz.emit(
            user.project,
            user.env,
            "identity.unlinked",
            {"user_id": str(user.id), "provider": identity.provider},
            actor=str(user.id),
        )
        return no_content()

    # ── MFA ──────────────────────────────────────────────────────────────

    @r.post("/mfa/totp/enroll", summary="Start enrolling an authenticator app", tags=["mfa"])
    async def enroll(ctx: HttpContext):
        user, _ = await signed_in_user(ctx)
        config = await load_config(akountz, user.project, user.env)
        if not config.mfa_enabled:
            raise HTTPException(status_code=403, detail="MFA is disabled for this project")
        return await mfa.enroll(akountz, user, config.project_name or config.project)

    @r.post(
        "/mfa/totp/verify",
        request_model=CodeBody,
        middleware=mfa_limits,
        summary="Confirm enrolment with a code",
        tags=["mfa"],
    )
    async def confirm(ctx: HttpContext, body: CodeBody):
        user, principal = await signed_in_user(ctx)
        codes = await mfa.confirm(akountz, user, body.code)
        if codes is None:
            raise HTTPException(status_code=400, detail="the code is not valid")
        if principal.claims.get("sid"):
            await elevate(akountz, None, user, principal.claims["sid"])
        await log_event(user.project, user.env, "mfa_enrolled", user=user, method="totp", ctx=ctx)
        await akountz.emit(
            user.project,
            user.env,
            "user.mfa_enabled",
            {"user_id": str(user.id)},
            actor=str(user.id),
        )
        return {"enabled": True, "recovery_codes": codes}

    @r.post(
        "/mfa/disable",
        request_model=CodeBody,
        middleware=mfa_limits,
        summary="Turn MFA off (needs a current code)",
        tags=["mfa"],
    )
    async def disable(ctx: HttpContext, body: CodeBody):
        user, _ = await signed_in_user(ctx)
        if not user.mfa_enabled:
            return {"enabled": False}
        if await mfa.verify(akountz, user, body.code) is None:
            raise HTTPException(status_code=400, detail="the code is not valid")
        await mfa.disable(user)
        await akountz.emit(
            user.project,
            user.env,
            "user.mfa_disabled",
            {"user_id": str(user.id)},
            actor=str(user.id),
        )
        return {"enabled": False}

    @r.post(
        "/mfa/recovery-codes",
        request_model=CodeBody,
        middleware=mfa_limits,
        summary="Replace the recovery codes",
        tags=["mfa"],
    )
    async def new_codes(ctx: HttpContext, body: CodeBody):
        user, _ = await signed_in_user(ctx)
        if not user.mfa_enabled or await mfa.verify(akountz, user, body.code) != "totp":
            raise HTTPException(status_code=400, detail="a current authenticator code is required")
        return {"recovery_codes": await mfa.regenerate_recovery_codes(user)}

    @r.get("/mfa", summary="MFA status", tags=["mfa"])
    async def status(ctx: HttpContext):
        user, principal = await signed_in_user(ctx)
        return {
            "enabled": user.mfa_enabled,
            "aal": principal.claims.get("aal"),
            "recovery_codes_remaining": await mfa.remaining_recovery_codes(user),
        }

    # ── sessions and history ─────────────────────────────────────────────

    @r.get("/sessions", summary="Active sessions", tags=["sessions"])
    async def sessions(ctx: HttpContext):
        user, principal = await signed_in_user(ctx)
        return {"data": await list_sessions(user, current=principal.claims.get("sid"))}

    @r.delete("/sessions/{session_id}", summary="Sign out one session", tags=["sessions"])
    async def end_session(ctx: HttpContext, session_id: str):
        user, _ = await signed_in_user(ctx)
        if not await revoke_session(user, session_id):
            raise HTTPException(status_code=404, detail="no such session")
        await akountz.emit(
            user.project,
            user.env,
            "session.revoked",
            {"user_id": str(user.id), "session_id": session_id},
            actor=str(user.id),
        )
        return no_content()

    @r.get("/history", summary="Recent sign-in activity", tags=["sessions"])
    async def history(ctx: HttpContext):
        user, _ = await signed_in_user(ctx)
        events = (
            await LoginEvent.filter(project=user.project, env=user.env, user_id=user.id)
            .order_by("-id")
            .limit(50)
        )
        return {
            "data": [
                {
                    "kind": e.kind,
                    "method": e.method,
                    "success": e.success,
                    "reason": e.reason,
                    "ip": e.ip,
                    "user_agent": e.user_agent,
                    "at": e.created_at.isoformat(),
                }
                for e in events
            ]
        }
