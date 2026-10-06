"""The public storage API (``/storage/v1``), on Sillo storage buckets.

Every operation goes through a Sillo :class:`~sillo.storage.Bucket`, so keys are
normalised, content is sniffed, size limits are enforced mid-stream, and the
bucket's Pawabase policy decides. Responses carry the same protective headers
Sillo's own serving route uses.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content, stream
from sillo import json as json_response
from sillo.exceptions import HTTPException
from sillo.storage import (
    FileNotFound,
    PolicyRefused,
    SignatureInvalid,
    StorageError,
    UnsafeKey,
    stream_upload,
)
from sillo.storage.routes import GUARDS, INLINE, _quote

from app.platform import Platform
from pawabase_core.context import require_context
from pawabase_core.policies import credential_context


class SignRequest(BaseModel):
    method: str = Field(default="GET", pattern="^(GET|PUT)$")
    expires_in: int = Field(default=300, ge=1, le=7 * 24 * 3600)
    content_type: str = ""
    max_bytes: int = Field(default=0, ge=0)


def _user(ctx: HttpContext) -> Any:
    user = ctx.scope.get("user")
    return user if user is not None and getattr(user, "is_authenticated", False) else None


def _not_found() -> HTTPException:
    # One answer for "missing" and "not yours", as Sillo's storage route gives.
    return HTTPException(status_code=404, detail="Not found")


async def _serve(held: Any, key: str, *, user: Any, signed: bool):
    try:
        info = await held.stat(key, user=user, signed=signed)
    except (FileNotFound, UnsafeKey, PolicyRefused) as exc:
        raise _not_found() from exc
    disposition = "inline" if info.content_type in INLINE else "attachment"
    headers = {
        "content-length": str(info.size),
        "etag": f'"{info.etag}"',
        "content-disposition": f'{disposition}; filename="{_quote(key.rsplit("/", 1)[-1])}"',
        "cache-control": "private, max-age=0, must-revalidate",
        **dict(GUARDS),
    }
    return stream(
        held.get(key, user=user, signed=signed), content_type=info.content_type, headers=headers
    )


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

    async def bucket_for(ctx: HttpContext, name: str):
        context = require_context(ctx)
        state = await platform.state_for(context)
        return state, platform.storage.bucket(state, name, credential=credential_context(ctx))

    @r.get("/buckets", summary="Buckets in this environment")
    async def buckets(ctx: HttpContext):
        state = await platform.state_for(require_context(ctx))
        return {
            "data": [
                {"name": b.name, "public": b.public, "accepts": b.accepts, "max_bytes": b.max_bytes}
                for b in state.buckets.values()
            ]
        }

    @r.get("/object/{bucket}/{key:path}", summary="Download an object")
    async def download(ctx: HttpContext, bucket: str, key: str):
        _, held = await bucket_for(ctx, bucket)
        return await _serve(held, key, user=_user(ctx), signed=False)

    async def upload(ctx: HttpContext, bucket: str, key: str):
        state, held = await bucket_for(ctx, bucket)
        body, declared = await _body_stream(ctx)
        try:
            stored = await held.put(
                key,
                _limited(body, platform.settings.max_upload_bytes),
                content_type=declared,
                user=_user(ctx),
            )
        except PolicyRefused as exc:
            raise HTTPException(
                status_code=403, detail="the bucket's policy refused this upload"
            ) from exc
        except (UnsafeKey, StorageError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return created(
            {
                "bucket": bucket,
                "key": stored.key,
                "size": stored.size,
                "content_type": stored.content_type,
                "etag": stored.etag,
            }
        )

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
        _, held = await bucket_for(ctx, bucket)
        try:
            removed = await held.delete(key, user=_user(ctx))
        except PolicyRefused as exc:
            raise _not_found() from exc
        if not removed:
            raise _not_found()
        return no_content()

    @r.get("/list/{bucket}", summary="List objects, one page at a time")
    async def listing(ctx: HttpContext, bucket: str):
        _, held = await bucket_for(ctx, bucket)
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
                }
                for f in page.files
            ],
            "prefixes": list(page.prefixes),
            "cursor": page.cursor,
        }

    @r.post("/sign/{bucket}/{key:path}", request_model=SignRequest, summary="Create a signed URL")
    async def sign(ctx: HttpContext, bucket: str, key: str, body: SignRequest):
        state, held = await bucket_for(ctx, bucket)
        from sillo.storage.base import Action

        action = Action.READ if body.method == "GET" else Action.WRITE
        if not held.policy.allows(action, key, _user(ctx)):
            raise HTTPException(status_code=403, detail="you may not grant access you do not have")
        try:
            url = held.signed_url(
                key,
                method=body.method,
                expires_in=body.expires_in,
                content_type=body.content_type,
                max_bytes=body.max_bytes,
            )
        except PolicyRefused as exc:
            raise HTTPException(
                status_code=403, detail="this bucket does not issue signed URLs for that"
            ) from exc
        except UnsafeKey as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"url": url, "expires_at": int(time.time()) + body.expires_in, "method": body.method}

    # Signed URLs carry their own authority, so they need no API key; the
    # The environment is in the path, the grant is in the token.
    async def signed(ctx: HttpContext, env: str, bucket: str, key: str):
        state = await platform.state(env)
        held = platform.storage.bucket(state, bucket, credential={"is_service": False})
        token = ctx.query_params.get("token", "")
        method = ctx.scope["method"]
        try:
            grant = platform.storage.signer(state, bucket).verify(
                token, key=key, method="GET" if method == "HEAD" else method
            )
        except SignatureInvalid as exc:
            raise _not_found() from exc
        if method in ("GET", "HEAD"):
            return await _serve(held, key, user=None, signed=True)
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
                    min(
                        value
                        for value in (grant.max_bytes, platform.settings.max_upload_bytes)
                        if value
                    )
                    if grant.max_bytes or platform.settings.max_upload_bytes
                    else 0,
                ),
                content_type=declared,
                signed=True,
            )
        except (UnsafeKey, StorageError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    r.put(
        "/signed/{env}/{bucket}/{key:path}",
        handler=signed,
        summary="Upload through a signed URL",
        exclude_from_schema=True,
    )
    app.mount_router(r)


__all__ = ["json_response", "register"]
