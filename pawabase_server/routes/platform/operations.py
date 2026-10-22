"""Queues, jobs, workers, cache, storage objects, mail and observability."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, no_content
from sillo.exceptions import HTTPException
from sillo.responses import PlainTextResponse
from sillo.storage import FileNotFound, PolicyRefused, StorageError, UnsafeKey, stream_upload
from sillo.storage.paths import normalise as held_normalise
from tortoise.functions import Count

from pawabase_server.db.models import (
    ApiKey,
    Environment,
    EventLog,
    FailedJobRecord,
    FlowRun,
    FunctionRun,
    JobRun,
    MailLog,
    MetricCounter,
    ObjectShare,
    RequestLog,
    WorkerHeartbeat,
)
from pawabase_server.environments.configuration import report as configuration_report
from pawabase_server.infra.storage import folders
from pawabase_server.infra.storage import shares as object_shares
from pawabase_server.infra.storage import transform
from pawabase_server.observability import audit_log as audit_log_store
from pawabase_server.observability import route_stats
from pawabase_server.observability.advisor import advise
from pawabase_server.observability.analytics import DEFAULT_RANGE, environment_analytics
from pawabase_server.observability.capacity import report as capacity_report
from pawabase_server.observability.system_health import system_report
from pawabase_server.platform import PLATFORM_QUEUES, Platform
from pawabase_server.routes.common import MANAGE, actor, audit, dump, get_environment, page_params


class InvalidateBody(BaseModel):
    tags: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)


class SignBody(BaseModel):
    key: str
    method: Literal["GET", "PUT"] = "GET"
    expires_in: int = Field(default=300, ge=1, le=7 * 24 * 3600)
    content_type: str = ""
    max_bytes: int = Field(default=0, ge=0)


class ShareBody(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
    #: Seconds until the link stops working (one minute to one year).
    expires_in: int = Field(default=3600, ge=60, le=365 * 24 * 3600)
    permissions: Literal["view", "download", "both"] = "both"
    label: str = Field(default="", max_length=120)


class MoveBody(BaseModel):
    source: str = Field(min_length=1, max_length=1024)
    target: str = Field(min_length=1, max_length=1024)


class FolderBody(BaseModel):
    prefix: str = Field(min_length=1, max_length=1024)


def _custom_window(start: str | None, end: str | None) -> tuple[datetime, datetime] | None:
    """A custom analytics window from ``from`` and ``to`` ISO timestamps; nothing when either is missing or unreadable."""
    if not start or not end:
        return None
    try:
        first = datetime.fromisoformat(start.replace("Z", "+00:00"))
        last = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "from and to must be ISO 8601 timestamps") from None
    if last <= first:
        raise HTTPException(422, "to must be after from")
    return first, last


async def _folder_body():
    """The bytes of a folder marker: written once to make a folder exist, never served."""
    yield folders.MARKER_BODY


class ReplayBody(BaseModel):
    """Which recorded jobs to queue again: named ids, or every job matching a filter (failed by default)."""

    ids: list[str] = Field(default_factory=list, max_length=500)
    status: str = "failed"
    queue: str | None = None
    job: str | None = None
    since_minutes: int | None = Field(default=None, ge=1)
    limit: int = Field(default=200, ge=1, le=1000)
    dry_run: bool = False


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    # ── queues and jobs ──────────────────────────────────────────────────

    @r.get(
        f"{base}/queues", auth=MANAGE, tags=["queues"], summary="Queues with depth and job counts"
    )
    async def queues(ctx: HttpContext, env: str):
        rows = (
            await JobRun.filter(env=env)
            .group_by("queue", "status")
            .annotate(n=Count("id"))
            .values("queue", "status", "n")
        )
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["queue"], {})[row["status"]] = row["n"]
        names = sorted(set(PLATFORM_QUEUES) | set(counts))
        result = []
        for name in names:
            size = await platform.queue.size(name)
            in_flight = (
                await platform.queue.in_flight(name)
                if hasattr(platform.queue, "in_flight")
                else None
            )
            result.append(
                {
                    "name": name,
                    "platform": name in PLATFORM_QUEUES,
                    "depth": size,
                    "in_flight": in_flight,
                    "jobs": counts.get(name, {}),
                }
            )
        return {"data": result, "backend": type(platform.queue).__name__}

    @r.get(f"{base}/jobs", auth=MANAGE, tags=["queues"], summary="Jobs, newest first")
    async def jobs(ctx: HttpContext, env: str):
        limit, offset = page_params(ctx)
        query = JobRun.filter(env=env)
        for field in ("queue", "status", "job", "source"):
            if ctx.query_params.get(field):
                query = query.filter(**{field: ctx.query_params[field]})
        return {
            "data": [
                dump(job) for job in await query.order_by("-created_at").offset(offset).limit(limit)
            ]
        }

    @r.get(f"{base}/jobs/{{job_id}}", auth=MANAGE, tags=["queues"], summary="One job")
    async def job(ctx: HttpContext, env: str, job_id: str):
        found = await JobRun.get_or_none(id=job_id, env=env)
        if found is None:
            raise HTTPException(status_code=404, detail="no such job")
        failure = await FailedJobRecord.filter(job_id=job_id).first()
        return {**dump(found), "failure": dump(failure) if failure else None}

    async def replay_one(env: str, found: JobRun, source: str) -> tuple[str | None, str | None]:
        """Queue a recorded job again from its payload. Returns (new job id, why not)."""
        import pawabase_server.automation.jobs as job_classes

        job_class = getattr(job_classes, found.job, None)
        if (
            job_class is None
            or not isinstance(found.payload, dict)
            or found.payload.get("truncated")
        ):
            return None, "its payload was not kept in full"
        new_id = await platform.dispatch(
            job_class,
            env=env,
            queue=found.queue,
            target=found.target,
            source=source,
            **found.payload,
        )
        return new_id, None

    @r.post(
        f"{base}/jobs/{{job_id}}/retry",
        auth=MANAGE,
        tags=["queues"],
        summary="Queue a job again from its record",
    )
    async def retry(ctx: HttpContext, env: str, job_id: str):
        found = await JobRun.get_or_none(id=job_id, env=env)
        if found is None:
            raise HTTPException(status_code=404, detail="no such job")
        new_id, why = await replay_one(env, found, "retry")
        if new_id is None:
            raise HTTPException(status_code=409, detail=f"this job cannot be retried: {why}")
        await audit(ctx, "job.retried", env=env, target=job_id, details={"new_job": new_id})
        return {"job_id": new_id}

    @r.post(
        f"{base}/jobs/replay",
        auth=MANAGE,
        tags=["queues"],
        request_model=ReplayBody,
        summary="Queue many recorded jobs again",
    )
    async def replay(ctx: HttpContext, env: str, body: ReplayBody):
        query = JobRun.filter(env=env)
        if body.ids:
            query = query.filter(id__in=body.ids)
        else:
            query = query.filter(status=body.status)
            if body.queue:
                query = query.filter(queue=body.queue)
            if body.job:
                query = query.filter(job=body.job)
            if body.since_minutes:
                query = query.filter(
                    created_at__gte=datetime.now(UTC) - timedelta(minutes=body.since_minutes)
                )
        matched = await query.order_by("created_at").limit(body.limit)
        queued: list[str] = []
        skipped: list[dict[str, str]] = []
        for found in matched:
            if body.dry_run:
                ok = isinstance(found.payload, dict) and not found.payload.get("truncated")
                (queued if ok else skipped).append(
                    found.id if ok else {"job": found.id, "why": "its payload was not kept in full"}
                )
                continue
            new_id, why = await replay_one(env, found, "replay")
            if new_id:
                queued.append(new_id)
            else:
                skipped.append({"job": found.id, "why": why or "not replayable"})
        if queued and not body.dry_run:
            await audit(
                ctx,
                "job.replayed",
                env=env,
                target="jobs",
                details={"count": len(queued), "status": body.status if not body.ids else "ids"},
            )
        return {
            "matched": len(matched),
            "queued": len(queued),
            "skipped": skipped[:50],
            "dry_run": body.dry_run,
        }

    @r.delete(
        f"{base}/failed-jobs/{{job_id}}",
        auth=MANAGE,
        tags=["queues"],
        summary="Dismiss a permanently failed job",
    )
    async def dismiss_failed(ctx: HttpContext, env: str, job_id: str):
        found = await JobRun.get_or_none(id=job_id, env=env)
        if found is None:
            raise HTTPException(status_code=404, detail="no such job")
        await FailedJobRecord.filter(job_id=job_id).delete()
        return no_content()

    @r.get(f"{base}/failed-jobs", auth=MANAGE, tags=["queues"], summary="Permanently failed jobs")
    async def failed_jobs(ctx: HttpContext, env: str):
        limit, offset = page_params(ctx)
        ids = await JobRun.filter(env=env, status="failed").values_list("id", flat=True)
        rows = (
            await FailedJobRecord.filter(job_id__in=list(ids))
            .order_by("-id")
            .offset(offset)
            .limit(limit)
        )
        return {"data": [dump(row) for row in rows]}

    @r.get("/workers", auth=MANAGE, tags=["queues"], summary="Workers and schedulers, as last seen")
    async def workers(ctx: HttpContext):
        cutoff = datetime.now(UTC) - timedelta(seconds=45)
        rows = await WorkerHeartbeat.all()
        data = []
        for row in rows:
            item = dump(row)
            item["alive"] = (
                row.status == "running" and row.last_seen is not None and row.last_seen >= cutoff
            )
            data.append(item)
        inline = platform.app.state.get("inline_worker")
        if inline is not None:
            data.append(
                {
                    "name": "inline (api process)",
                    "kind": "worker",
                    "status": "running",
                    "alive": True,
                    "queues": inline.worker.options.queues,
                    "processed": inline.worker._jobs_processed,
                    "concurrency": inline.worker.options.concurrency,
                }
            )
        return {"data": data}

    # ── cache ────────────────────────────────────────────────────────────

    @r.get("/cache", auth=MANAGE, tags=["cache"], summary="Cache statistics")
    async def cache_stats(ctx: HttpContext):
        stats = platform.cache.stats
        return {
            "backend": type(platform.cache).__name__,
            "stats": stats.as_dict() if hasattr(stats, "as_dict") else {},
        }

    @r.post(
        f"{base}/cache/invalidate",
        auth=MANAGE,
        tags=["cache"],
        request_model=InvalidateBody,
        summary="Invalidate cached entries by tag",
    )
    async def invalidate(ctx: HttpContext, env: str, body: InvalidateBody):
        state = await platform.state(env)
        tags = list(body.tags) + [f"resource:{name}" for name in body.resources]
        removed = await platform.cache_invalidate(state, tags)
        await audit(ctx, "cache.invalidated", env=env, details={"tags": tags})
        return {"invalidated": removed}

    # ── storage objects ──────────────────────────────────────────────────

    @r.get(
        f"{base}/buckets/{{bucket}}/objects",
        auth=MANAGE,
        tags=["storage"],
        summary="List objects in a bucket",
    )
    async def objects(ctx: HttpContext, env: str, bucket: str):
        state = await platform.state(env)
        held = platform.storage.bucket(state, bucket, credential={"is_service": True})
        page = await held.page(
            ctx.query_params.get("prefix", ""),
            cursor=ctx.query_params.get("cursor", ""),
            limit=min(int(ctx.query_params.get("limit", 100)), 1000),
        )
        return {
            "files": [
                {
                    "key": f.key,
                    "size": f.size,
                    "content_type": f.content_type,
                    "modified": f.modified,
                    "etag": f.etag,
                    "url": platform.storage.object_url(state, bucket, f.key),
                }
                for f in page.files
                if not folders.is_marker(f.key)
            ],
            "prefixes": list(page.prefixes),
            "cursor": page.cursor,
        }

    def service_bucket(state, bucket: str):
        """Studio's own authority over a bucket: the bucket's rules about types and size still apply, its access policies do not."""
        return platform.storage.bucket(state, bucket, credential={"is_service": True})

    def object_key(ctx: HttpContext) -> str:
        key = ctx.query_params.get("key", "")
        if not key:
            raise HTTPException(400, "pass the object's key as ?key=")
        return key

    def visibility(model) -> str:
        if model.public or model.read_policy == "public":
            return "public"
        return "restricted"

    @r.get(
        f"{base}/buckets/{{bucket}}/object",
        auth=MANAGE,
        tags=["storage"],
        summary="One object's details: metadata, gateway URL, visibility",
    )
    async def object_details(ctx: HttpContext, env: str, bucket: str):
        state = await platform.state(env)
        held = service_bucket(state, bucket)
        key = object_key(ctx)
        if folders.is_marker(key):
            raise HTTPException(404, "no such object")
        try:
            info = await held.stat(key)
        except (FileNotFound, UnsafeKey) as exc:
            raise HTTPException(404, "no such object") from exc
        except StorageError as exc:
            raise HTTPException(502, "the storage provider could not be reached") from exc
        model = state.buckets[bucket]
        name = info.key.rsplit("/", 1)[-1]
        live = await ObjectShare.filter(env=env, bucket=bucket, key=info.key).all()
        created = None
        local = getattr(held.driver, "_path", None)
        if local is not None:
            try:
                stats = local(info.key).stat()
                created = getattr(stats, "st_birthtime", None) or stats.st_ctime
            except OSError:
                created = None
        return {
            "bucket": bucket,
            "key": info.key,
            "name": name,
            "extension": name.rsplit(".", 1)[-1].lower() if "." in name else "",
            "size": info.size,
            "content_type": info.content_type,
            "declared_type": info.declared_type,
            "etag": info.etag,
            "modified": info.modified,
            #: Local disk only; S3-compatible services record the last modification and nothing earlier.
            "created": created,
            "provider": held.driver.name,
            "url": platform.storage.object_url(state, bucket, info.key),
            "visibility": visibility(model),
            "transforms": {**transform.settings(model.transforms)},
            "transformable": info.content_type in transform.SOURCES,
            "shares": sum(1 for row in live if object_shares.status(row) == "active"),
        }

    @r.put(
        f"{base}/buckets/{{bucket}}/object",
        auth=MANAGE,
        tags=["storage"],
        summary="Upload or replace an object as Studio (the bucket's access policies do not apply)",
    )
    async def put_object(ctx: HttpContext, env: str, bucket: str):
        state = await platform.state(env)
        held = service_bucket(state, bucket)
        key = object_key(ctx)
        limit = platform.limit(env, "MAX_UPLOAD_BYTES", platform.settings.max_upload_bytes)
        content_type = ctx.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            files = await ctx.files
            upload = files.get("file") or next(iter(files.values()), None)
            if upload is None:
                raise HTTPException(400, "send the file in a 'file' form field")
            source, content_type = stream_upload(upload), upload.content_type or ""
        else:
            source = ctx.stream()

        async def capped():
            total = 0
            async for chunk in source:
                total += len(chunk)
                if limit and total > limit:
                    raise HTTPException(413, "the upload is larger than this environment allows")
                yield chunk

        replacing = await held.driver.exists(held_normalise(key))
        try:
            stored = await held.put(key, capped(), content_type=content_type)
        except (UnsafeKey, StorageError) as exc:
            raise HTTPException(400, str(exc)) from exc
        platform.storage.variants.purge(env, bucket, stored.key)
        await audit(
            ctx,
            "storage.replaced" if replacing else "storage.uploaded",
            env=env,
            target=f"{bucket}/{stored.key}",
        )
        return {
            "bucket": bucket,
            "key": stored.key,
            "size": stored.size,
            "content_type": stored.content_type,
            "etag": stored.etag,
            "replaced": replacing,
            "url": platform.storage.object_url(state, bucket, stored.key),
        }

    @r.post(
        f"{base}/buckets/{{bucket}}/object/move",
        auth=MANAGE,
        tags=["storage"],
        request_model=MoveBody,
        summary="Rename or move an object inside its bucket",
    )
    async def move_object(ctx: HttpContext, env: str, bucket: str, body: MoveBody):
        state = await platform.state(env)
        held = service_bucket(state, bucket)
        try:
            source = held_normalise(body.source)
            target = held_normalise(body.target)
            if source == target:
                raise HTTPException(400, "the new key is the same as the old one")
            if await held.driver.exists(target):
                raise HTTPException(409, "an object with that key already exists")
            info = await held.stat(source)
            await held.put(target, held.get(source), content_type=info.content_type)
            await held.delete(source)
        except FileNotFound as exc:
            raise HTTPException(404, "no such object") from exc
        except (UnsafeKey, StorageError) as exc:
            raise HTTPException(400, str(exc)) from exc
        platform.storage.variants.purge(env, bucket, source)
        await object_shares.revoke_object(env, bucket, source)
        await audit(ctx, "storage.moved", env=env, target=f"{bucket}/{source} -> {target}")
        return {
            "bucket": bucket,
            "key": target,
            "url": platform.storage.object_url(state, bucket, target),
        }

    @r.post(
        f"{base}/buckets/{{bucket}}/folders",
        auth=MANAGE,
        tags=["storage"],
        request_model=FolderBody,
        summary="Create an empty folder (a marker object under the prefix)",
    )
    async def create_folder(ctx: HttpContext, env: str, bucket: str, body: FolderBody):
        state = await platform.state(env)
        held = service_bucket(state, bucket)
        try:
            root = folders.clean(body.prefix)
        except UnsafeKey as exc:
            raise HTTPException(400, "a folder needs a safe name") from exc
        if await held.driver.exists(root):
            raise HTTPException(409, "an object already has that name")
        marker = folders.marker_key(root)
        # Written on the driver, not held.put: a folder must not answer to the bucket's accepted types. Idempotent besides.
        if not await held.driver.exists(marker):
            await held.driver.write(
                marker,
                _folder_body(),
                content_type="application/octet-stream",
                declared_type="",
            )
        await audit(ctx, "storage.folder.created", env=env, target=f"{bucket}/{root}/")
        return {"bucket": bucket, "prefix": f"{root}/", "key": marker}

    @r.delete(
        f"{base}/buckets/{{bucket}}/folders/{{prefix:path}}",
        auth=MANAGE,
        tags=["storage"],
        summary="Delete an empty folder",
    )
    async def delete_folder(ctx: HttpContext, env: str, bucket: str, prefix: str):
        state = await platform.state(env)
        held = service_bucket(state, bucket)
        try:
            root = folders.clean(prefix)
        except UnsafeKey as exc:
            raise HTTPException(400, "a folder needs a safe name") from exc
        marker = folders.marker_key(root)
        if not await held.driver.exists(marker):
            raise HTTPException(status_code=404, detail="no such folder")
        page = await held.page(f"{root}/", limit=1000)
        occupied = [f.key for f in page.files if not folders.is_marker(f.key)]
        if occupied or page.prefixes:
            raise HTTPException(409, "the folder is not empty")
        await held.driver.delete(marker)
        await audit(ctx, "storage.folder.deleted", env=env, target=f"{bucket}/{root}/")
        return no_content()

    @r.get(
        f"{base}/buckets/{{bucket}}/shares",
        auth=MANAGE,
        tags=["storage"],
        summary="Share links for a bucket, or for one object with ?key=",
    )
    async def list_shares(ctx: HttpContext, env: str, bucket: str):
        state = await platform.state(env)
        query = ObjectShare.filter(env=env, bucket=bucket)
        if key := ctx.query_params.get("key"):
            query = query.filter(key=key)
        rows = await query.order_by("-created_at").limit(200)
        data = []
        for row in rows:
            item = object_shares.describe(row)
            token = object_shares.reveal(row, platform.box)
            item["url"] = platform.storage.share_url(state, token) if token else None
            data.append(item)
        return {"data": data}

    @r.post(
        f"{base}/buckets/{{bucket}}/shares",
        auth=MANAGE,
        tags=["storage"],
        request_model=ShareBody,
        summary="Create a temporary share link for one object",
    )
    async def create_share(ctx: HttpContext, env: str, bucket: str, body: ShareBody):
        state = await platform.state(env)
        held = service_bucket(state, bucket)
        try:
            info = await held.stat(body.key)
        except (FileNotFound, UnsafeKey) as exc:
            raise HTTPException(404, "no such object") from exc
        row, token = await object_shares.create(
            env,
            bucket,
            info.key,
            expires_in=body.expires_in,
            permissions=body.permissions,
            label=body.label,
            created_by=actor(ctx) or "",
            box=platform.box,
        )
        await audit(ctx, "storage.shared", env=env, target=f"{bucket}/{info.key}")
        return {
            **object_shares.describe(row),
            "token": token,
            "url": platform.storage.share_url(state, token),
        }

    @r.delete(
        f"{base}/buckets/{{bucket}}/shares/{{share_id}}",
        auth=MANAGE,
        tags=["storage"],
        summary="Revoke a share link",
    )
    async def revoke_share(ctx: HttpContext, env: str, bucket: str, share_id: str):
        row = await ObjectShare.get_or_none(id=share_id, env=env, bucket=bucket)
        if row is None:
            raise HTTPException(404, "no such share link")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await row.save()
        await audit(ctx, "storage.unshared", env=env, target=f"{bucket}/{row.key}")
        return object_shares.describe(row)

    @r.delete(
        f"{base}/buckets/{{bucket}}/objects/{{key:path}}",
        auth=MANAGE,
        tags=["storage"],
        summary="Delete an object",
    )
    async def delete_object(ctx: HttpContext, env: str, bucket: str, key: str):
        state = await platform.state(env)
        held = platform.storage.bucket(state, bucket, credential={"is_service": True})
        if not await held.delete(key, signed=True):
            raise HTTPException(status_code=404, detail="no such object")
        platform.storage.variants.purge(env, bucket, key)
        await object_shares.revoke_object(env, bucket, key)
        await audit(ctx, "storage.deleted", env=env, target=f"{bucket}/{key}")
        return no_content()

    @r.post(
        f"{base}/buckets/{{bucket}}/sign",
        auth=MANAGE,
        tags=["storage"],
        request_model=SignBody,
        summary="A signed URL for one object",
    )
    async def sign(ctx: HttpContext, env: str, bucket: str, body: SignBody):
        state = await platform.state(env)
        try:
            url = platform.storage.signed_url(
                state,
                bucket,
                body.key,
                method=body.method,
                expires_in=body.expires_in,
                content_type=body.content_type,
                max_bytes=body.max_bytes,
                credential={"is_service": True},
            )
        except PolicyRefused as exc:
            raise HTTPException(403, "signed access is disabled for this bucket") from exc
        except UnsafeKey as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"url": url}

    @r.get(
        f"{base}/configuration",
        auth=MANAGE,
        tags=["environments"],
        summary="The infrastructure and limits an environment runs under, and which variable each comes from",
    )
    async def configuration(ctx: HttpContext, env: str):
        await get_environment(env)
        return configuration_report(platform, await platform.state(env))

    # ── observability ────────────────────────────────────────────────────

    @r.get(
        f"{base}/requests",
        auth=MANAGE,
        tags=["observability"],
        summary="Persisted request history with correlation ids",
    )
    async def requests(ctx: HttpContext, env: str):
        await get_environment(env)
        await platform.app.state["request_rollup"].flush()
        limit, offset = page_params(ctx)
        query = RequestLog.filter(env=env)
        if ctx.query_params.get("request_id"):
            query = query.filter(request_id=ctx.query_params["request_id"])
        if ctx.query_params.get("method"):
            query = query.filter(method=ctx.query_params["method"].upper())
        if ctx.query_params.get("status"):
            status = ctx.query_params["status"]
            if status.endswith("xx") and status[0].isdigit():
                lower = int(status[0]) * 100
                query = query.filter(status__gte=lower, status__lt=lower + 100)
            elif status.isdigit():
                query = query.filter(status=int(status))
        if ctx.query_params.get("search"):
            query = query.filter(path__icontains=ctx.query_params["search"])
        if ctx.query_params.get("minutes"):
            try:
                minutes = max(1, min(int(ctx.query_params["minutes"]), 43200))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="minutes must be an integer") from exc
            query = query.filter(
                started_at__gte=(datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
            )
        rows = await query.order_by("-id").offset(offset).limit(limit)
        return {"data": [dump(row) for row in rows]}

    @r.get(
        f"{base}/requests/{{request_id}}",
        auth=MANAGE,
        tags=["observability"],
        summary="One correlated request with flows, events and user logs",
    )
    async def request_trace(ctx: HttpContext, env: str, request_id: str):
        await get_environment(env)
        await platform.app.state["request_rollup"].flush()
        request_rows = await RequestLog.filter(env=env, request_id=request_id).order_by("id")
        runs = await FlowRun.filter(env=env, request_id=request_id).order_by("created_at")
        events = await EventLog.filter(env=env, request_id=request_id).order_by("id")
        jobs = await JobRun.filter(env=env, request_id=request_id).order_by("created_at")
        function_runs = await FunctionRun.filter(env=env, request_id=request_id).order_by(
            "created_at"
        )
        if not (request_rows or runs or events or jobs or function_runs):
            raise HTTPException(status_code=404, detail="no such request trace")
        logs, seen = [], set()

        def add_log(entry: Any, source: str, **extra: Any) -> None:
            item = dict(entry) if isinstance(entry, dict) else {"message": str(entry)}
            item.setdefault("request_id", request_id)
            item.update({k: v for k, v in extra.items() if v is not None})
            item["source"] = source
            stamp = (
                item.get("timestamp") or item.get("at"),
                item.get("level"),
                item.get("message"),
            )
            if stamp in seen:  # a function's own log is also noted on the request it ran in
                return
            seen.add(stamp)
            logs.append(item)

        for row in request_rows:
            for entry in (row.notes or {}).get("logs", []):
                add_log(entry, "request")
        for run in function_runs:
            for entry in run.logs or []:
                add_log(entry, f"function:{run.function}", function=run.function, run_id=run.id)
        for run in runs:
            for entry in run.logs or []:
                add_log(entry, f"flow:{run.flow}", flow=run.flow, run_id=run.id)
        logs.sort(key=lambda item: str(item.get("timestamp") or item.get("at") or ""))
        spans = []
        for row in request_rows:
            for item in (row.notes or {}).get("spans", []):
                spans.append({**item, "service": row.service})
        db = [item for item in spans if item.get("kind") == "db"]
        failed_spans = [item for item in spans if item.get("status") == "error"]
        primary = request_rows[0] if request_rows else None
        return {
            "request_id": request_id,
            "requests": [dump(row) for row in request_rows],
            "spans": spans,
            "flow_runs": [dump(run) for run in runs],
            "function_runs": [dump(run) for run in function_runs],
            "events": [dump(event) for event in events],
            "jobs": [dump(job) for job in jobs],
            "logs": logs,
            "error": primary.error if primary else None,
            "traceback": (primary.notes or {}).get("traceback") if primary else None,
            "summary": {
                "services": sorted({row.service for row in request_rows}),
                "duration_ms": round(sum(row.duration_ms for row in request_rows), 3),
                "flows": len(runs),
                "functions": len(function_runs),
                "events": len(events),
                "jobs": len(jobs),
                "logs": len(logs),
                "spans": len(spans),
                "db_queries": len(db),
                "db_ms": round(
                    sum(item.get("duration_ms", 0) for item in db if item.get("parent") is None), 3
                ),
                "slowest_span": max(
                    spans, key=lambda item: item.get("duration_ms", 0), default=None
                ),
                "failed_spans": len(failed_spans),
                "failed": any(row.status >= 500 for row in request_rows)
                or any(run.status == "failed" for run in runs)
                or any(run.status == "failed" for run in function_runs),
            },
        }

    def minutes_param(ctx: HttpContext, default: int = 60) -> int:
        try:
            return max(1, min(int(ctx.query_params.get("minutes", default)), 30 * 24 * 60))
        except ValueError:
            raise HTTPException(status_code=422, detail="minutes must be a whole number") from None

    @r.get(
        f"{base}/observability/routes",
        auth=MANAGE,
        tags=["observability"],
        summary="Routes ranked by failures, error rate, latency, traffic or time spent",
    )
    async def route_ranking(ctx: HttpContext, env: str):
        await get_environment(env)
        await platform.app.state["request_rollup"].flush()
        try:
            limit = max(1, min(int(ctx.query_params.get("limit", 50)), 200))
        except ValueError:
            raise HTTPException(status_code=422, detail="limit must be a whole number") from None
        return await route_stats.routes_for(
            env,
            minutes=minutes_param(ctx),
            sort=ctx.query_params.get("sort", "errors"),
            limit=limit,
        )

    @r.get(
        f"{base}/observability/routes/detail",
        auth=MANAGE,
        tags=["observability"],
        summary="One route: timeline, statuses, errors, slowest requests, time by step kind",
    )
    async def route_detail(ctx: HttpContext, env: str):
        await get_environment(env)
        await platform.app.state["request_rollup"].flush()
        method, route = ctx.query_params.get("method"), ctx.query_params.get("route")
        if not method or not route:
            raise HTTPException(status_code=422, detail="method and route are required")
        return await route_stats.route_detail_for(env, method, route, minutes=minutes_param(ctx))

    @r.get(
        f"{base}/observability/errors",
        auth=MANAGE,
        tags=["observability"],
        summary="Failures grouped by what went wrong and where",
    )
    async def error_groups(ctx: HttpContext, env: str):
        await get_environment(env)
        await platform.app.state["request_rollup"].flush()
        return await route_stats.errors_for(env, minutes=minutes_param(ctx))

    @r.get(f"{base}/metrics", auth=MANAGE, tags=["observability"], summary="Counters per minute")
    async def metrics(ctx: HttpContext, env: str):
        minutes = max(1, min(int(ctx.query_params.get("minutes", 60)), 7 * 24 * 60))
        since = datetime.now(UTC) - timedelta(minutes=minutes)
        query = MetricCounter.filter(env=env, window__gte=since)
        if ctx.query_params.get("name"):
            query = query.filter(name=ctx.query_params["name"])
        rows = await query.order_by("window")
        return {
            "data": [
                {
                    "name": r.name,
                    "tags": r.tags,
                    "window": r.window.isoformat(),
                    "value": r.value,
                    "count": r.count,
                }
                for r in rows
            ]
        }

    @r.get("/audit", auth=MANAGE, tags=["observability"], summary="Management-plane audit log")
    async def audit_log(ctx: HttpContext):
        """Filter with ``env``, ``action`` (``webhook.*`` for a service), ``actor``, ``target``, ``since``, ``until``."""
        limit, offset = page_params(ctx)
        try:
            query = audit_log_store.query(ctx.query_params)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"since/until must be ISO times: {exc}"
            ) from exc
        return {
            "data": [dump(row) for row in await query.order_by("-id").offset(offset).limit(limit)]
        }

    @r.get(
        "/audit/export",
        auth=MANAGE,
        tags=["observability"],
        summary="Download the audit log (JSON lines or CSV)",
    )
    async def audit_export(ctx: HttpContext):
        fmt = ctx.query_params.get("format") or "jsonl"
        if fmt not in ("jsonl", "csv"):
            raise HTTPException(status_code=400, detail="format is jsonl or csv")
        try:
            body, content_type = await audit_log_store.export(ctx.query_params, fmt)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"since/until must be ISO times: {exc}"
            ) from exc
        name = f"pawabase-audit-{datetime.now(UTC):%Y%m%d}.{fmt}"
        return PlainTextResponse(
            body,
            headers={"content-disposition": f'attachment; filename="{name}"'},
            content_type=content_type,
        )

    @r.get(
        f"{base}/overview",
        auth=MANAGE,
        tags=["observability"],
        summary="Everything about an environment at a glance",
    )
    async def overview(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        state = await platform.state(env)
        day = datetime.now(UTC) - timedelta(days=1)
        compiled = await state.compiled()
        telemetry = platform.app.state.get("pawabase.telemetry")
        return {
            "env": env,
            "version": environment.version,
            "counts": {
                "resources": len(state.resources),
                "routes": len(state.routes),
                "flows": len(state.flows),
                "policies": len(state.policies),
                "schemas": len(state.schemas),
                "buckets": len(state.buckets),
                "webhooks": len(state.webhooks),
                "subscriptions": len(state.subscriptions),
                "schedules": len(state.schedules),
                "secrets": len(state.secret_values),
                "keys": await ApiKey.filter(environment=environment, revoked_at=None).count(),
            },
            "last_24h": {
                "events": await EventLog.filter(env=env, created_at__gte=day).count(),
                "flow_runs": await FlowRun.filter(env=env, created_at__gte=day).count(),
                "flow_failures": await FlowRun.filter(
                    env=env, created_at__gte=day, status="failed"
                ).count(),
                "jobs": await JobRun.filter(env=env, created_at__gte=day).count(),
                "job_failures": await JobRun.filter(
                    env=env, created_at__gte=day, status="failed"
                ).count(),
                "mail": await MailLog.filter(env=env, created_at__gte=day).count(),
            },
            "requests": telemetry.summary() if telemetry else None,
            "analytics": await environment_analytics(
                env, ctx.query_params.get("range", DEFAULT_RANGE)
            ),
            "problems": compiled.state.get("problems", []),
            "infrastructure": {
                "database": "platform default"
                if state.database_source() == "default"
                else "configured",
                "storage": platform.storage._config(state).get("driver", "local"),
                "mail": "configured"
                if platform.mail.describe(state)["configured"]
                else "suppressed (not configured)",
                "cache": type(platform.cache).__name__,
                "queue": type(platform.queue).__name__,
                "events": platform.bus.backend,
            },
        }

    @r.get(
        f"{base}/advisor",
        auth=MANAGE,
        tags=["observability"],
        summary="What needs the application owner's attention: failing requests and work, and limits close to full",
    )
    async def advisor(ctx: HttpContext, env: str):
        await get_environment(env)
        try:
            minutes = max(5, min(int(ctx.query_params.get("minutes", 60)), 1440))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="minutes must be an integer") from exc
        return await advise(platform, env, minutes)

    @r.get(
        f"{base}/observability/system",
        auth=MANAGE,
        tags=["observability"],
        summary="Queues, workers, schedules, flows, events, webhooks and mail over a window",
    )
    async def system(ctx: HttpContext, env: str):
        await get_environment(env)
        try:
            minutes = max(5, min(int(ctx.query_params.get("minutes", 60)), 43200))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="minutes must be an integer") from exc
        return await system_report(platform, env, minutes)

    @r.get(
        "/capacity",
        auth=MANAGE,
        tags=["runtime"],
        summary="What this deployment is using: process memory, and per environment its database, storage and keys",
    )
    async def capacity(ctx: HttpContext):
        return await capacity_report(platform)

    @r.get(
        f"{base}/analytics",
        auth=MANAGE,
        tags=["observability"],
        summary="Requests, errors, latency, events and flow runs over time",
    )
    async def analytics(ctx: HttpContext, env: str):
        await get_environment(env)
        window = _custom_window(ctx.query_params.get("from"), ctx.query_params.get("to"))
        return await environment_analytics(
            env, ctx.query_params.get("range", DEFAULT_RANGE), window
        )

    @r.get("/overview", auth=MANAGE, tags=["observability"], summary="Runtime overview")
    async def installation(ctx: HttpContext):
        return {
            "name": platform.settings.project_name,
            "environments": await Environment.all().count(),
            "started_at": platform.started_at.isoformat(),
            "events_backend": platform.bus.backend,
            "queue_backend": type(platform.queue).__name__,
            "cache_backend": type(platform.cache).__name__,
            "data_sources": platform.sources.stats(),
        }


__all__ = ["json", "register"]
