"""Queues, jobs, workers, cache, storage objects, mail and observability."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, no_content
from sillo.exceptions import HTTPException
from tortoise.functions import Count

from app import route_stats
from app.analytics import DEFAULT_RANGE, environment_analytics
from app.platform import PLATFORM_QUEUES, Platform
from database.models import (
    ApiKey,
    AuditEntry,
    Environment,
    EventLog,
    FailedJobRecord,
    FlowRun,
    FunctionRun,
    JobRun,
    MailLog,
    MetricCounter,
    RequestLog,
    WorkerHeartbeat,
)
from routes.common import (
    MANAGE,
    audit,
    dump,
    get_environment,
    page_params,
)


class InvalidateBody(BaseModel):
    tags: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)


class SignBody(BaseModel):
    key: str
    method: str = "GET"
    expires_in: int = Field(default=300, ge=1, le=7 * 24 * 3600)


class MailTestBody(BaseModel):
    to: list[str]
    subject: str = "Pawabase test message"
    text: str = "This is a test message sent from Pawabase Studio."
    template: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


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

    @r.post(
        f"{base}/jobs/{{job_id}}/retry",
        auth=MANAGE,
        tags=["queues"],
        summary="Queue a failed job again",
    )
    async def retry(ctx: HttpContext, env: str, job_id: str):
        import app.jobs as job_classes

        found = await JobRun.get_or_none(id=job_id, env=env)
        if found is None:
            raise HTTPException(status_code=404, detail="no such job")
        job_class = getattr(job_classes, found.job, None)
        if (
            job_class is None
            or not isinstance(found.payload, dict)
            or found.payload.get("truncated")
        ):
            raise HTTPException(
                status_code=409, detail="this job cannot be retried from its record"
            )
        new_id = await platform.dispatch(
            job_class,
            env=env,
            queue=found.queue,
            target=found.target,
            source="retry",
            **found.payload,
        )
        await audit(
            ctx, "job.retried", env=env, target=job_id, details={"new_job": new_id}
        )
        return {"job_id": new_id}

    @r.get(f"{base}/failed-jobs", auth=MANAGE, tags=["queues"], summary="Permanently failed jobs")
    async def failed_jobs(ctx: HttpContext, env: str):
        limit, offset = page_params(ctx)
        ids = await JobRun.filter(env=env, status="failed").values_list(
            "id", flat=True
        )
        rows = (
            await FailedJobRecord.filter(job_id__in=list(ids))
            .order_by("-id")
            .offset(offset)
            .limit(limit)
        )
        return {"data": [dump(row) for row in rows]}

    @r.get(
        "/workers", auth=MANAGE, tags=["queues"], summary="Workers and schedulers, as last seen"
    )
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
                }
                for f in page.files
            ],
            "prefixes": list(page.prefixes),
            "cursor": page.cursor,
        }

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
        held = platform.storage.bucket(state, bucket, credential={"is_service": True})
        return {
            "url": held.signed_url(body.key, method=body.method.upper(), expires_in=body.expires_in)
        }

    # ── mail ─────────────────────────────────────────────────────────────

    @r.get(f"{base}/mail/log", auth=MANAGE, tags=["mail"], summary="Messages sent or suppressed")
    async def mail_log(ctx: HttpContext, env: str):
        limit, offset = page_params(ctx)
        rows = (
            await MailLog.filter(env=env).order_by("-id").offset(offset).limit(limit)
        )
        return {"data": [dump(row) for row in rows]}

    @r.post(
        f"{base}/mail/test",
        auth=MANAGE,
        tags=["mail"],
        request_model=MailTestBody,
        summary="Send a message now",
    )
    async def mail_test(ctx: HttpContext, env: str, body: MailTestBody):
        state = await platform.state(env)
        try:
            return await platform.mail.send(
                state,
                body.to,
                body.subject,
                text=body.text,
                template=body.template,
                data=body.data,
                source="studio",
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

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
        request_rows = await RequestLog.filter(
            env=env, request_id=request_id
        ).order_by("id")
        runs = await FlowRun.filter(env=env, request_id=request_id).order_by(
            "created_at"
        )
        events = await EventLog.filter(env=env, request_id=request_id).order_by(
            "id"
        )
        jobs = await JobRun.filter(env=env, request_id=request_id).order_by(
            "created_at"
        )
        function_runs = await FunctionRun.filter(
            env=env, request_id=request_id
        ).order_by("created_at")
        if not (request_rows or runs or events or jobs or function_runs):
            raise HTTPException(status_code=404, detail="no such request trace")
        logs, seen = [], set()

        def add_log(entry: Any, source: str, **extra: Any) -> None:
            item = dict(entry) if isinstance(entry, dict) else {"message": str(entry)}
            item.setdefault("request_id", request_id)
            item.update({k: v for k, v in extra.items() if v is not None})
            item["source"] = source
            stamp = (item.get("timestamp") or item.get("at"), item.get("level"), item.get("message"))
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
                "db_ms": round(sum(item.get("duration_ms", 0) for item in db if item.get("parent") is None), 3),
                "slowest_span": max(spans, key=lambda item: item.get("duration_ms", 0), default=None),
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
            env, minutes=minutes_param(ctx), sort=ctx.query_params.get("sort", "errors"), limit=limit
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
        limit, offset = page_params(ctx)
        query = AuditEntry.all()
        if ctx.query_params.get("env"):
            query = query.filter(env=ctx.query_params["env"])
        return {
            "data": [dump(row) for row in await query.order_by("-id").offset(offset).limit(limit)]
        }

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
                "flow_runs": await FlowRun.filter(
                    env=env, created_at__gte=day
                ).count(),
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
                "database": "configured" if state.infra.get("database_url") else "platform default",
                "storage": (state.infra.get("storage") or {}).get("driver", "local"),
                "mail": "configured"
                if (state.infra.get("mail") or {}).get("host")
                else "suppressed (not configured)",
                "cache": type(platform.cache).__name__,
                "queue": type(platform.queue).__name__,
                "events": platform.bus.backend,
            },
        }

    @r.get(
        f"{base}/analytics",
        auth=MANAGE,
        tags=["observability"],
        summary="Requests, errors, latency, events and flow runs over time",
    )
    async def analytics(ctx: HttpContext, env: str):
        await get_environment(env)
        return await environment_analytics(env, ctx.query_params.get("range", DEFAULT_RANGE))

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
