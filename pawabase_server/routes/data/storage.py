"""The public storage API (``/storage/v1``), on Sillo storage buckets.

Every operation goes through a Sillo :class:`~sillo.storage.Bucket`, so keys are
normalised, content is sniffed, size limits are enforced mid-stream, and the
bucket's Pawabase policy decides.

Delivery (:mod:`pawabase_server.infra.storage.delivery`) is shared by every way an object
can be read: the API (``/object``, with an API key), the keyless gateway URL
(``/files``, for ``<img>`` and ``<video>``), a share link (``/share``) and a legacy
signed URL (``/signed``). It adds ranges, validators, compression, content
disposition and image transformations.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo import json as json_response
from sillo.exceptions import HTTPException
from sillo.storage import (
    Action,
    FileNotFound,
    PolicyRefused,
    SignatureInvalid,
    StorageError,
    UnsafeKey,
    stream_upload,
)
from sillo.storage.paths import normalise

from pawabase_server.core.context import require_context
from pawabase_server.core.principal import Principal
from pawabase_server.core.tokens import TokenInvalid, verify_user_token
from pawabase_server.infra.storage import shares
from pawabase_server.infra.storage.delivery import Delivery, error
from pawabase_server.platform import Platform
from pawabase_server.policies import credential_context
from pawabase_server.projects import quotas

ANONYMOUS = {"is_service": False, "role": "anon", "scopes": []}
SERVICE = {"is_service": True}


class SignRequest(BaseModel):
    method: str = Field(default="GET", pattern="^(GET|PUT)$")
    expires_in: int = Field(default=300, ge=1, le=7 * 24 * 3600)
    content_type: str = ""
    max_bytes: int = Field(default=0, ge=0)


def _user(ctx: HttpContext) -> Any:
    user = ctx.scope.get("user")
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def _refused(user: Any):
    """What a policy refusal looks like: 401 to someone who is not signed in, 403 to someone who is."""
    if user is None:
        return error(
            401,
            "sign in to read this object",
            {"www-authenticate": 'Bearer realm="pawabase"'},
        )
    return error(403, "you may not read this object")


async def _body_stream(ctx: HttpContext) -> tuple[AsyncIterator[bytes], str]:
    """The upload: a multipart ``file`` field, or the raw request body."""
    content_type = ctx.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        files = await ctx.files
        upload = files.get("file") or next(iter(files.values()), None)
        if upload is None:
            raise HTTPException(status_code=400, detail="send the file in a 'file' form field")
        return stream_upload(upload), upload.content_type or ""
    return ctx.stream(), content_type


async def _limited(source: AsyncIterator[bytes], limit: int) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in source:
        total += len(chunk)
        if limit and total > limit:
            raise HTTPException(status_code=413, detail="the upload is larger than this URL allows")
        yield chunk


def register(app: Any, platform: Platform) -> None:
    r = Router(prefix="/storage/v1", tags=["storage"])
    storage = platform.storage

    def upload_limit(state) -> int:
        """The largest upload this environment accepts: its own MAX_UPLOAD_BYTES, else the platform's."""
        return quotas.upload_cap(
            state,
            platform.limit(state.env_name, "MAX_UPLOAD_BYTES", platform.settings.max_upload_bytes),
        )

    async def bucket_for(ctx: HttpContext, name: str):
        context = require_context(ctx)
        state = await platform.state_for(context)
        return state, storage.bucket(state, name, credential=credential_context(ctx))

    async def read(
        ctx: HttpContext,
        state,
        held,
        bucket: str,
        key: str,
        *,
        user: Any,
        signed: bool = False,
        allow_inline: bool = True,
        scope: str = "policy",
        open_access: bool = False,
        anonymous: bool = False,
        query: Mapping[str, Any] | None = None,
        hide_refusal: bool = False,
    ):
        """One object read through the shared delivery: ranges, validators, compression, transformations."""
        model = state.buckets.get(bucket)
        try:
            info = await held.stat(key, user=user, signed=signed)
        except PolicyRefused:
            # A capability URL (a signed link) says nothing about why it stopped working.
            return error(404, "Not found") if hide_refusal else _refused(user)
        except (FileNotFound, UnsafeKey):
            return error(404, "Not found")
        except (StorageError, httpx.HTTPError, OSError):
            return error(502, "the storage provider could not be reached")
        public = bool(
            anonymous and model is not None and (model.public or model.read_policy == "public")
        )
        delivery = Delivery(
            held,
            env=state.env_name,
            key=key,
            headers=ctx.headers,
            query=ctx.query_params if query is None else query,
            variants=storage.variants,
            transforms=model.transforms if model is not None else {},
            public=public,
            max_age=int(model.cache_max_age or 0) if model is not None else 0,
            user=user,
            signed=signed,
            allow_inline=allow_inline,
            scope=scope,
            open_access=open_access,
        )
        try:
            return await delivery.response(info, head=ctx.scope["method"] == "HEAD")
        except (FileNotFound, UnsafeKey):
            return error(404, "Not found")
        except (StorageError, httpx.HTTPError, OSError):
            return error(502, "the storage provider could not be reached")

    def object_info(state, bucket: str, stored: Any) -> dict[str, Any]:
        return {
            "bucket": bucket,
            "key": stored.key,
            "size": stored.size,
            "content_type": stored.content_type,
            "etag": stored.etag,
            "url": storage.object_url(state, bucket, stored.key),
        }

    @r.get("/buckets", summary="Buckets in this environment")
    async def buckets(ctx: HttpContext):
        state = await platform.state_for(require_context(ctx))
        return {
            "data": [
                {"name": b.name, "public": b.public, "accepts": b.accepts, "max_bytes": b.max_bytes}
                for b in state.buckets.values()
            ]
        }

    # ── reading ──────────────────────────────────────────────────────────

    async def download(ctx: HttpContext, bucket: str, key: str):
        state, held = await bucket_for(ctx, bucket)
        context = require_context(ctx)
        return await read(
            ctx,
            state,
            held,
            bucket,
            key,
            user=_user(ctx),
            anonymous=_user(ctx) is None and not context.is_service,
        )

    r.get("/object/{bucket}/{key:path}", handler=download, summary="Download an object")
    r.head(
        "/object/{bucket}/{key:path}",
        handler=download,
        summary="An object's headers",
        exclude_from_schema=True,
    )

    # The stable, keyless URL an application puts in ``<img src>``. The environment is in the path, as it is for other
    # browser navigations; the read policy is evaluated for an anonymous caller (or the user whose bearer token came with
    # the request), exactly as it would be on /object.
    async def gateway(ctx: HttpContext, env: str, bucket: str, key: str):
        try:
            state = await platform.state(env)
        except HTTPException:
            return error(404, "Not found")
        if bucket not in state.buckets:
            return error(404, "Not found")
        user = None
        header = ctx.headers.get("authorization") or ""
        if header[:7].lower() == "bearer ":
            try:
                claims = verify_user_token(
                    header[7:].strip(), platform.settings.jwt_master_secret, env=env
                )
                user = Principal("user", claims)
            except TokenInvalid:
                user = None
        held = storage.bucket(state, bucket, credential=ANONYMOUS)
        return await read(
            ctx, state, held, bucket, key, user=user, anonymous=user is None, open_access=True
        )

    r.get(
        "/files/{env}/{bucket}/{key:path}",
        handler=gateway,
        summary="An object by its stable gateway URL",
    )
    r.head(
        "/files/{env}/{bucket}/{key:path}",
        handler=gateway,
        summary="An object's headers by its stable gateway URL",
        exclude_from_schema=True,
    )

    async def share(ctx: HttpContext, token: str):
        row = await shares.find(token)
        if row is None:
            return error(404, "Not found")
        state_ = shares.status(row)
        if state_ != "active":
            return error(410, f"this link has {state_}")
        try:
            state = await platform.state(row.env)
        except HTTPException:
            return error(404, "Not found")
        if row.bucket not in state.buckets:
            return error(404, "Not found")
        query = dict(ctx.query_params)
        if row.permissions == "view" and "download" in query:
            return error(403, "this link allows viewing only")
        if row.permissions == "download":
            # A download-only link never renders in the browser: it is an attachment whatever the type.
            query["download"] = query.get("download") or "1"
        # The grant was verified above and names this one object, so the read is made as the platform for that key only.
        held = storage.bucket(state, row.bucket, credential=SERVICE)
        response = await read(
            ctx,
            state,
            held,
            row.bucket,
            row.key,
            user=None,
            scope=f"share:{row.id}",
            open_access=True,
            allow_inline=row.permissions != "download",
            query=query,
        )
        if getattr(response, "status_code", 500) < 400:
            await shares.touch(row)
        return response

    r.get("/share/{token}", handler=share, summary="Open an object through a share link")
    r.head(
        "/share/{token}",
        handler=share,
        summary="A share link's headers",
        exclude_from_schema=True,
    )

    # ── writing ──────────────────────────────────────────────────────────

    async def upload(ctx: HttpContext, bucket: str, key: str):
        state, held = await bucket_for(ctx, bucket)
        user = _user(ctx)
        # An upload to a key that exists is a replacement and answers to the update policy; a new key to the upload policy.
        replacing = False
        try:
            replacing = await held.driver.exists(normalise(key))
        except (UnsafeKey, StorageError):
            replacing = False
        if replacing:
            held = storage.bucket(
                state,
                bucket,
                credential=credential_context(ctx),
                overwrite=True,
            )
        body, declared = await _body_stream(ctx)
        try:
            stored = await held.put(
                key,
                _limited(body, upload_limit(state)),
                content_type=declared,
                user=user,
            )
        except PolicyRefused as exc:
            raise HTTPException(
                status_code=403,
                detail="the bucket's policy refused this "
                + ("replacement" if replacing else "upload"),
            ) from exc
        except (UnsafeKey, StorageError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if replacing:
            storage.variants.purge(state.env_name, bucket, stored.key)
        return created(object_info(state, bucket, stored))

    r.put(
        "/object/{bucket}/{key:path}",
        handler=upload,
        summary="Upload an object (raw body or multipart 'file')",
    )
    r.post(
        "/object/{bucket}/{key:path}",
        handler=upload,
        summary="Upload an object (multipart 'file')",
        exclude_from_schema=True,
    )

    @r.delete("/object/{bucket}/{key:path}", summary="Delete an object")
    async def delete(ctx: HttpContext, bucket: str, key: str):
        state, held = await bucket_for(ctx, bucket)
        try:
            removed = await held.delete(key, user=_user(ctx))
        except PolicyRefused as exc:
            raise HTTPException(
                status_code=403, detail="the bucket's policy refused this delete"
            ) from exc
        if not removed:
            raise _not_found()
        storage.variants.purge(state.env_name, bucket, key)
        await shares.revoke_object(state.env_name, bucket, key)
        return no_content()

    @r.get("/list/{bucket}", summary="List objects, one page at a time")
    async def listing(ctx: HttpContext, bucket: str):
        state, held = await bucket_for(ctx, bucket)
        q = ctx.query_params
        try:
            page = await held.page(
                q.get("prefix", ""),
                cursor=q.get("cursor", ""),
                limit=min(int(q.get("limit", 100)), 1000),
                user=_user(ctx),
            )
        except PolicyRefused as exc:
            raise HTTPException(
                status_code=403, detail="the bucket's policy refused this listing"
            ) from exc
        return {
            "files": [
                {
                    "key": f.key,
                    "size": f.size,
                    "content_type": f.content_type,
                    "modified": f.modified,
                    "etag": f.etag,
                    "url": storage.object_url(state, bucket, f.key),
                }
                for f in page.files
            ],
            "prefixes": list(page.prefixes),
            "cursor": page.cursor,
        }

    # ── signed URLs (kept for existing API consumers; Studio no longer uses them) ──

    @r.post("/sign/{bucket}/{key:path}", request_model=SignRequest, summary="Create a signed URL")
    async def sign(ctx: HttpContext, bucket: str, key: str, body: SignRequest):
        state, held = await bucket_for(ctx, bucket)
        action = Action.READ if body.method == "GET" else Action.WRITE
        if not held.policy.allows(action, key, _user(ctx)):
            raise HTTPException(status_code=403, detail="you may not grant access you do not have")
        try:
            url = storage.signed_url(
                state,
                bucket,
                key,
                method=body.method,
                expires_in=body.expires_in,
                content_type=body.content_type,
                max_bytes=body.max_bytes,
                credential=credential_context(ctx),
            )
        except PolicyRefused as exc:
            raise HTTPException(
                status_code=403, detail="this bucket does not issue signed URLs for that"
            ) from exc
        except UnsafeKey as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"url": url, "expires_at": int(time.time()) + body.expires_in, "method": body.method}

    # Signed URLs carry their own authority, so they need no API key; the
    # environment is in the path, the grant is in the token.
    async def signed(ctx: HttpContext, env: str, bucket: str, key: str):
        state = await platform.state(env)
        held = storage.bucket(state, bucket, credential={"is_service": False})
        token = ctx.query_params.get("token", "")
        method = ctx.scope["method"]
        try:
            grant = storage.signer(state, bucket).verify(
                token, key=key, method="GET" if method == "HEAD" else method
            )
        except SignatureInvalid as exc:
            raise _not_found() from exc
        if method in ("GET", "HEAD"):
            return await read(
                ctx, state, held, bucket, key, user=None, signed=True, hide_refusal=True
            )
        body, declared = await _body_stream(ctx)
        if grant.content_type and declared.split(";")[0].strip() != grant.content_type:
            raise HTTPException(
                status_code=415, detail=f"this URL accepts {grant.content_type} only"
            )
        try:
            stored = await held.put(
                key,
                _limited(
                    body,
                    min(value for value in (grant.max_bytes, upload_limit(state)) if value)
                    if grant.max_bytes or upload_limit(state)
                    else 0,
                ),
                content_type=declared,
                signed=True,
            )
        except (UnsafeKey, StorageError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        storage.variants.purge(state.env_name, bucket, stored.key)
        return created(
            {
                "bucket": bucket,
                "key": stored.key,
                "size": stored.size,
                "content_type": stored.content_type,
            }
        )

    r.get(
        "/signed/{env}/{bucket}/{key:path}",
        handler=signed,
        summary="Fetch through a signed URL",
        exclude_from_schema=True,
    )
    r.head(
        "/signed/{env}/{bucket}/{key:path}",
        handler=signed,
        summary="Headers through a signed URL",
        exclude_from_schema=True,
    )
    r.put(
        "/signed/{env}/{bucket}/{key:path}",
        handler=signed,
        summary="Upload through a signed URL",
        exclude_from_schema=True,
    )
    app.mount_router(r)


__all__ = ["json_response", "register"]
