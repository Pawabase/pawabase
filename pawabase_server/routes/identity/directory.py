"""A person's API keys and devices, and the same for an administrator looking after an environment."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from pawabase_server.core.service import SERVICE_ONLY
from pawabase_server.db.models import Device, UserApiKey
from pawabase_server.identity import devices, hosted, user_keys
from pawabase_server.identity.accounts import Accounts, get_user
from pawabase_server.identity.environment import load_config
from pawabase_server.routes.identity.auth import signed_in_user


class SourceRequest(BaseModel):
    page: str
    #: The branding being edited; the environment's saved branding when omitted.
    branding: dict[str, Any] | None = None
    form: dict[str, Any] | None = None


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list, max_length=50)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    org: str | None = Field(default=None, max_length=63)


def register(r: Router, accounts: Accounts) -> None:
    @r.get("/user/api-keys", summary="Your API keys", tags=["api-keys"])
    async def my_keys(ctx: HttpContext):
        user, _ = await signed_in_user(ctx)
        rows = await UserApiKey.filter(user=user, revoked_at=None).order_by("-id")
        return {"data": [user_keys.view(row) for row in rows]}

    @r.post(
        "/user/api-keys", request_model=KeyCreate, summary="Create an API key", tags=["api-keys"]
    )
    async def make_key(ctx: HttpContext, body: KeyCreate):
        user, principal = await signed_in_user(ctx)
        if str(principal.claims.get("sid", "")).startswith(user_keys.SESSION_PREFIX):
            raise HTTPException(status_code=403, detail="an API key cannot create API keys")
        config = await load_config(accounts, user.env)
        try:
            plaintext, row = await user_keys.create(
                config,
                user,
                name=body.name.strip(),
                scopes=body.scopes,
                expires_in_days=body.expires_in_days,
                org=body.org,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await accounts.emit(
            user.env,
            "user.api_key_created",
            {"user_id": str(user.id), "key_id": str(row.id)},
            actor=str(user.id),
        )
        return created({**user_keys.view(row), "key": plaintext})

    @r.delete("/user/api-keys/{key_id}", summary="Revoke an API key", tags=["api-keys"])
    async def revoke_key(ctx: HttpContext, key_id: str):
        user, _ = await signed_in_user(ctx)
        done = await UserApiKey.filter(id=key_id, user=user, revoked_at=None).update(
            revoked_at=datetime.now(UTC)
        )
        if not done:
            raise HTTPException(status_code=404, detail="no such API key")
        await accounts.emit(
            user.env,
            "user.api_key_revoked",
            {"user_id": str(user.id), "key_id": key_id},
            actor=str(user.id),
        )
        return no_content()

    @r.get("/user/devices", summary="Devices you have signed in from", tags=["devices"])
    async def my_devices(ctx: HttpContext):
        user, _ = await signed_in_user(ctx)
        current, _durable = devices.identify(ctx)
        rows = await Device.filter(user=user, revoked_at=None).order_by("-last_seen_at")
        return {"data": [devices.view(row, current=current) for row in rows]}

    @r.delete("/user/devices/{device_id}", summary="Forget a device", tags=["devices"])
    async def forget_device(ctx: HttpContext, device_id: str):
        user, _ = await signed_in_user(ctx)
        done = await Device.filter(id=device_id, user=user, revoked_at=None).update(
            revoked_at=datetime.now(UTC), trusted_until=None
        )
        if not done:
            raise HTTPException(status_code=404, detail="no such device")
        return no_content()


def register_admin(r: Router, accounts: Accounts) -> None:
    base = "/envs/{env}"

    async def user_or_404(env: str, user_id: str):
        user = await get_user(env, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="no such user")
        return user

    @r.post(
        f"{base}/hosted-source",
        auth=SERVICE_ONLY,
        request_model=SourceRequest,
        tags=["admin"],
        summary="The HTML a hosted page is served as, for the branding being edited",
    )
    async def hosted_source(ctx: HttpContext, env: str, body: SourceRequest):
        if body.page not in hosted.PREVIEW_PAGES:
            raise HTTPException(status_code=404, detail="no such page")
        config = await load_config(accounts, env)
        branding = hosted.clean_branding(
            config.branding if body.branding is None else body.branding
        )
        document = hosted.render(
            body.page,
            env=env,
            project_name=config.project_name,
            branding=branding,
            providers=config.enabled_providers(),
            signup_enabled=config.signup_enabled,
            magic_link_enabled=config.magic_link_enabled,
            query={},
            nonce="NONCE",
            base=f"/auth/v1/hosted/{env}",
            preview=False,
            password_min=config.password_min_length,
            password_rules=config.password_rules,
            form=config.signup_form if body.form is None else body.form,
            remember_days=config.policy("devices")["remember_days"],
            legal=config.legal,
        )
        return {"page": body.page, "html": document, "live": body.page in hosted.PAGES}

    @r.get(f"{base}/api-keys", auth=SERVICE_ONLY, tags=["admin"], summary="Every user API key")
    async def all_keys(ctx: HttpContext, env: str):
        limit = max(1, min(int(ctx.query_params.get("limit", 100)), 500))
        rows = (
            await UserApiKey.filter(env=env).prefetch_related("user").order_by("-id").limit(limit)
        )
        return {"data": [user_keys.view(row, email=row.user.email) for row in rows]}

    @r.delete(
        f"{base}/api-keys/{{key_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Revoke a user's key",
    )
    async def revoke(ctx: HttpContext, env: str, key_id: str):
        done = await UserApiKey.filter(id=key_id, env=env, revoked_at=None).update(
            revoked_at=datetime.now(UTC)
        )
        if not done:
            raise HTTPException(status_code=404, detail="no such API key")
        return no_content()

    @r.get(
        f"{base}/users/{{user_id}}/api-keys",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="One user's API keys",
    )
    async def keys_of(ctx: HttpContext, env: str, user_id: str):
        user = await user_or_404(env, user_id)
        rows = await UserApiKey.filter(user=user).order_by("-id")
        return {"data": [user_keys.view(row) for row in rows]}

    @r.get(
        f"{base}/users/{{user_id}}/devices",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="One user's devices",
    )
    async def devices_of(ctx: HttpContext, env: str, user_id: str):
        user = await user_or_404(env, user_id)
        rows = await Device.filter(user=user, revoked_at=None).order_by("-last_seen_at")
        return {"data": [devices.view(row) for row in rows]}

    @r.delete(
        f"{base}/users/{{user_id}}/devices/{{device_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Forget one of a user's devices",
    )
    async def forget(ctx: HttpContext, env: str, user_id: str, device_id: str):
        user = await user_or_404(env, user_id)
        done = await Device.filter(id=device_id, user=user, revoked_at=None).update(
            revoked_at=datetime.now(UTC), trusted_until=None
        )
        if not done:
            raise HTTPException(status_code=404, detail="no such device")
        return no_content()

    @r.get(f"{base}/devices", auth=SERVICE_ONLY, tags=["admin"], summary="Recent devices")
    async def recent_devices(ctx: HttpContext, env: str):
        limit = max(1, min(int(ctx.query_params.get("limit", 100)), 500))
        rows = (
            await Device.filter(env=env, revoked_at=None)
            .prefetch_related("user")
            .order_by("-last_seen_at")
            .limit(limit)
        )
        return {
            "data": [
                {**devices.view(row), "user_email": row.user.email, "user_id": str(row.user_id)}
                for row in rows
            ]
        }
