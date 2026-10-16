"""Flows, functions, events, webhooks and schedules: running and inspecting them."""

from __future__ import annotations

import fnmatch
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo.exceptions import HTTPException

from pawabase_server.automation.event_processor import flow_event_entries
from pawabase_server.automation.jobs import (
    ProcessEventJob,
    PurgeEnvironmentJob,
    RunFlowJob,
    RunFunctionJob,
    SendMailJob,
)
from pawabase_server.automation.jobs.webhooks import DeliverWebhookJob
from pawabase_server.db.models import (
    EventLog,
    FlowRun,
    InboundHook,
    Schedule,
    WebhookDelivery,
    WebhookEndpoint,
)
from pawabase_server.flows import FlowError, default_registry, validate_flow
from pawabase_server.functions.execution import NotFound, call_function, run_flow
from pawabase_server.observability import service_metrics
from pawabase_server.platform import Platform
from pawabase_server.projects import quotas
from pawabase_server.routes.common import MANAGE, audit, dump, get_environment, page_params


class RunBody(BaseModel):
    input: Any = None
    entry: str | None = Field(default=None, description="Trigger node to start from")
    as_user: dict[str, Any] | None = Field(
        default=None, description="Run as if this auth context called it"
    )
    branch: str | None = Field(
        default=None, description="Run the code deployed on this branch (functions only)"
    )


def _source_of(key: str) -> str:
    """Where a function came from: a branch deployment, the environment's deployment, or code mounted for the project."""
    return "branch" if "@" in key else "deployment" if "/" in key else "mounted"


class BulkRedeliver(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)
    status: Literal["failed", "retrying", "delivered", "pending"] = "failed"
    endpoint: str | None = None
    event: str | None = None
    since_minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 90)
    limit: int = Field(default=100, ge=1, le=500)
    dry_run: bool = False


class EmitBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    payload: Any = None


def _caller_auth(ctx: HttpContext) -> dict[str, Any]:
    from pawabase_server.core.principal import policy_auth

    return policy_auth(ctx.scope.get("user"))


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    @r.get(
        f"{base}/plan-usage",
        auth=MANAGE,
        tags=["usage"],
        summary="The project's plan limits and how much of each is used",
    )
    async def plan_usage(ctx: HttpContext, env: str):
        state = await platform.state(env)
        return {"project_id": state.project_id, "data": await quotas.usage_report(platform, state)}

    @r.get(
        f"{base}/service-health",
        auth=MANAGE,
        tags=["observability"],
        summary="Every background service's health, in one shape",
    )
    async def service_health(ctx: HttpContext, env: str):
        await get_environment(env)
        minutes = max(1, min(int(ctx.query_params.get("minutes") or 60), 7 * 24 * 60))
        return {"minutes": minutes, "data": await service_metrics.service_health(env, minutes)}

    @r.get(
        "/retry-policies",
        auth=MANAGE,
        tags=["jobs"],
        summary="How each kind of background work is retried",
    )
    async def retry_policies(ctx: HttpContext):
        """The retry policy of every job the platform runs: attempts and the wait before each retry. A webhook endpoint's own
        ``max_attempts`` replaces the deliveries' attempt count."""
        kinds = (
            ProcessEventJob,
            RunFlowJob,
            RunFunctionJob,
            DeliverWebhookJob,
            SendMailJob,
            PurgeEnvironmentJob,
        )
        return {
            "data": [
                {"job": kind.__name__, "queue": kind.queue, **kind.policy().describe()}
                for kind in kinds
            ]
        }

    # ── blocks ───────────────────────────────────────────────────────────

    @r.get("/blocks", auth=MANAGE, tags=["flows"], summary="The block catalogue")
    async def blocks(ctx: HttpContext):
        catalogue = default_registry().catalogue()
        return {"count": len(catalogue), "data": catalogue}

    # ── flows ────────────────────────────────────────────────────────────

    @r.post(
        "/flows/validate",
        auth=MANAGE,
        tags=["flows"],
        summary="Check a flow definition without saving it",
    )
    async def validate(ctx: HttpContext):
        definition = await ctx.json
        return {"problems": validate_flow(definition or {})}

    @r.post(
        f"{base}/flows/{{name}}/run",
        auth=MANAGE,
        tags=["flows"],
        request_model=RunBody,
        summary="Run a flow now and return its trace",
    )
    async def run_now(ctx: HttpContext, env: str, name: str, body: RunBody):
        state = await platform.state(env)
        auth = body.as_user or {"authenticated": False, "kind": "service"}
        request_id = getattr(ctx.state, "request_id", None)
        try:
            run = await run_flow(
                platform,
                state,
                name,
                body.input,
                trigger="manual",
                auth=auth,
                credential={"is_service": body.as_user is None, "role": "service"},
                request_id=request_id,
                entry=body.entry,
            )
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FlowError as exc:
            record = await FlowRun.filter(env=env, flow=name).order_by("-created_at").first()
            return {
                "status": "failed",
                "error": exc.message,
                "code": exc.code,
                "node": exc.node,
                "run_id": record.id if record else None,
                "trace": record.trace if record else [],
                "logs": record.logs if record else [],
            }
        await audit(ctx, "flow.run", env=env, target=name)
        response = (
            {
                "status": run.response.status,
                "body": run.response.body,
                "headers": run.response.headers,
            }
            if run.response
            else None
        )
        return {
            "status": "succeeded",
            "run_id": run.id,
            "result": run.result(),
            "response": response,
            "trace": run.trace(),
            "logs": run.logs,
        }

    @r.get(f"{base}/flow-runs", auth=MANAGE, tags=["flows"], summary="Recent flow runs")
    async def flow_runs(ctx: HttpContext, env: str):
        limit, offset = page_params(ctx)
        query = FlowRun.filter(env=env)
        if ctx.query_params.get("flow"):
            query = query.filter(flow=ctx.query_params["flow"])
        if ctx.query_params.get("status"):
            query = query.filter(status=ctx.query_params["status"])
        runs = await query.order_by("-created_at").offset(offset).limit(limit)
        return {"data": [dump(run, exclude=("trace", "logs")) for run in runs]}

    @r.get(
        f"{base}/flow-runs/{{run_id}}",
        auth=MANAGE,
        tags=["flows"],
        summary="One flow run with its trace",
    )
    async def flow_run(ctx: HttpContext, env: str, run_id: str):
        run = await FlowRun.get_or_none(id=run_id, env=env)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return dump(run)

    # ── functions ────────────────────────────────────────────────────────

    @r.get(
        f"{base}/functions",
        auth=MANAGE,
        tags=["functions"],
        summary="Functions loaded for the project",
    )
    async def functions(ctx: HttpContext, env: str):
        await platform.state(env)
        code = platform.ensure_code()
        branch = ctx.query_params.get("branch") or None
        roots = [
            platform.deployments.current(env, branch or "main").resolve(),
            Path(platform.settings.code_path).resolve(),
        ]

        def where(handler: Any) -> dict[str, Any]:
            try:
                file = Path(inspect.getsourcefile(handler) or "").resolve()
                line = inspect.getsourcelines(handler)[1]
            except (OSError, TypeError):
                return {"file": None, "line": None}
            for root in roots:
                if root in file.parents:
                    return {"file": file.relative_to(root).as_posix(), "line": line}
            return {"file": None, "line": None}

        return {
            "data": [
                {**spec.describe(), "source": _source_of(spec.project), **where(spec.handler)}
                for spec in platform.function_specs(env, branch)
            ],
            "branch": branch or "main",
            "modules": code.modules,
            "errors": code.errors,
            "router": code.router is not None,
        }

    @r.post(
        f"{base}/functions/{{name}}/invoke",
        auth=MANAGE,
        tags=["functions"],
        request_model=RunBody,
        summary="Invoke a function now",
    )
    async def invoke(ctx: HttpContext, env: str, name: str, body: RunBody):
        state = await platform.state(env)
        try:
            result = await call_function(
                platform,
                state,
                name,
                body.input,
                trigger="manual",
                auth=body.as_user or _caller_auth(ctx),
                branch=body.branch,
            )
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FlowError as exc:
            return {
                "status": "failed",
                "error": exc.message,
                "code": exc.code,
                "details": exc.details,
            }
        except Exception as exc:
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        return {"status": "succeeded", "result": result}

    # ── events ───────────────────────────────────────────────────────────

    @r.get(
        f"{base}/events",
        auth=MANAGE,
        tags=["events"],
        summary="Recent events and their consumers",
    )
    async def events(ctx: HttpContext, env: str):
        limit, offset = page_params(ctx)
        query = EventLog.filter(env=env)
        if ctx.query_params.get("name"):
            query = query.filter(name=ctx.query_params["name"])
        if ctx.query_params.get("source"):
            query = query.filter(source=ctx.query_params["source"])
        return {"data": [dump(e) for e in await query.order_by("-id").offset(offset).limit(limit)]}

    @r.get(f"{base}/events/{{event_id}}", auth=MANAGE, tags=["events"], summary="One event")
    async def event(ctx: HttpContext, env: str, event_id: str):
        found = await EventLog.get_or_none(event_id=event_id, env=env)
        if found is None:
            raise HTTPException(status_code=404, detail="no such event")
        return dump(found)

    @r.post(
        f"{base}/events",
        auth=MANAGE,
        tags=["events"],
        request_model=EmitBody,
        summary="Publish an event (for testing consumers)",
    )
    async def emit(ctx: HttpContext, env: str, body: EmitBody):
        state = await platform.state(env)
        event_id = await platform.emit(state, body.name, body.payload, actor="studio")
        await audit(ctx, "event.emitted", env=env, target=body.name)
        return {"event_id": event_id}

    @r.get(
        f"{base}/events-graph",
        auth=MANAGE,
        tags=["events"],
        summary="Where events come from and what consumes them",
    )
    async def events_graph(ctx: HttpContext, env: str):
        state = await platform.state(env)
        producers: dict[str, set[str]] = {}

        def produce(name: str, source: str) -> None:
            producers.setdefault(name, set()).add(source)

        for resource in state.resources.values():
            if resource.events:
                for change in ("created", "updated", "deleted"):
                    produce(f"{resource.name}.{change}", f"resource:{resource.name}")
        for flow in state.flows.values():
            for node in flow.definition.get("nodes", []):
                data = node.get("data") or {}
                if data.get("block") == "event.emit" and (data.get("config") or {}).get("event"):
                    produce(data["config"]["event"], f"flow:{flow.name}")
        for hook in state.inbound_hooks.values():
            if hook.target_type == "event":
                produce(hook.target, f"inbound-hook:{hook.slug}")
        for schedule in state.schedules:
            if schedule.target_type == "event":
                produce(schedule.target, f"schedule:{schedule.name}")
        for name in (
            "user.created",
            "user.signed_in",
            "user.verified",
            "user.deleted",
            "user.password_reset",
            "session.revoked",
            "organization.created",
            "invitation.accepted",
        ):
            produce(name, "service:akountz")
        for name in ("file.uploaded", "file.deleted"):
            produce(name, "service:storage")
        # EventLog orders by -id by default, and Tortoise falls back to that
        # Meta ordering whenever order_by() gets no arguments — an empty list
        # is still "no ordering given". Postgres then refuses DISTINCT with an
        # ORDER BY column outside the select list, so order by a column that
        # actually is selected instead of trying to clear the ordering.
        seen = (
            await EventLog.filter(env=env).order_by("name").distinct().values_list("name", "source")
        )
        for name, source in seen:
            produce(name, f"service:{source}")

        edges = []
        for name, sources in sorted(producers.items()):
            consumers = []
            for subscription in state.subscriptions:
                if fnmatch.fnmatchcase(name, subscription.event):
                    consumers.append(
                        {
                            "type": subscription.target_type,
                            "target": subscription.target,
                            "via": f"subscription:{subscription.name}",
                        }
                    )
            from pawabase_server.core.events import PlatformEvent

            for flow_name, node in flow_event_entries(state, PlatformEvent(name=name, env=env)):
                consumers.append({"type": "flow", "target": flow_name, "via": f"trigger:{node}"})
            for endpoint in state.webhooks:
                if any(fnmatch.fnmatchcase(name, pattern) for pattern in endpoint.events or ["*"]):
                    consumers.append(
                        {"type": "webhook", "target": endpoint.name, "via": endpoint.url}
                    )
            edges.append({"event": name, "producers": sorted(sources), "consumers": consumers})
        return {"data": edges}

    # ── webhooks ─────────────────────────────────────────────────────────

    @r.get(
        f"{base}/webhook-deliveries",
        auth=MANAGE,
        tags=["webhooks"],
        summary="Outbound delivery history",
    )
    async def deliveries(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        limit, offset = page_params(ctx)
        query = WebhookDelivery.filter(endpoint__environment=environment)
        if ctx.query_params.get("endpoint"):
            query = query.filter(endpoint__name=ctx.query_params["endpoint"])
        if ctx.query_params.get("status"):
            query = query.filter(status=ctx.query_params["status"])
        rows = await query.select_related("endpoint").order_by("-id").offset(offset).limit(limit)
        return {"data": [{**dump(row), "endpoint": row.endpoint.name} for row in rows]}

    @r.post(
        f"{base}/webhook-deliveries/{{delivery_id}}/redeliver",
        auth=MANAGE,
        tags=["webhooks"],
        summary="Deliver again",
    )
    async def redeliver(ctx: HttpContext, env: str, delivery_id: str):
        delivery_id = delivery_id.upper()  # ULIDs are case-insensitive
        environment = await get_environment(env)
        delivery = (
            await WebhookDelivery.filter(id=delivery_id, endpoint__environment=environment)
            .select_related("endpoint")
            .first()
        )
        if delivery is None:
            raise HTTPException(status_code=404, detail="no such delivery")
        copy_id, job_id = await deliver_again(env, delivery, "studio")
        await audit(
            ctx, "webhook.redelivered", env=env, target=delivery_id, details={"copy": copy_id}
        )
        return {"delivery_id": copy_id, "job_id": job_id}

    async def deliver_again(env: str, delivery: WebhookDelivery, source: str) -> tuple[str, str]:
        """A new delivery of the same event to the same endpoint, with the endpoint's full retry budget."""
        copy = await WebhookDelivery.create(
            endpoint=delivery.endpoint,
            event_id=delivery.event_id,
            event=delivery.event,
            payload=delivery.payload,
            status="pending",
        )
        job_id = await platform.dispatch(
            DeliverWebhookJob,
            env=env,
            target=delivery.endpoint.name,
            source=source,
            delivery_id=copy.id,
            max_attempts=delivery.endpoint.max_attempts,
        )
        return copy.id, job_id

    @r.post(
        f"{base}/webhook-deliveries/redeliver",
        auth=MANAGE,
        tags=["webhooks"],
        request_model=BulkRedeliver,
        summary="Deliver many deliveries again: by id, or every failed one matching a filter",
    )
    async def redeliver_many(ctx: HttpContext, env: str, body: BulkRedeliver):
        environment = await get_environment(env)
        query = WebhookDelivery.filter(endpoint__environment=environment)
        if body.ids:
            query = query.filter(id__in=[i.upper() for i in body.ids])
        else:
            query = query.filter(status=body.status)
            if body.endpoint:
                query = query.filter(endpoint__name=body.endpoint)
            if body.event:
                query = query.filter(event=body.event)
            if body.since_minutes:
                query = query.filter(
                    created_at__gte=datetime.now(UTC) - timedelta(minutes=body.since_minutes)
                )
        matched = await query.select_related("endpoint").order_by("id").limit(body.limit)
        if body.dry_run:
            return {"matched": len(matched), "queued": 0, "dry_run": True}
        queued = [await deliver_again(env, delivery, "bulk") for delivery in matched]
        if queued:
            await audit(
                ctx,
                "webhook.bulk_redelivered",
                env=env,
                target="deliveries",
                details={"count": len(queued), "filter": body.model_dump(exclude={"ids"})},
            )
        return {
            "matched": len(matched),
            "queued": len(queued),
            "deliveries": [copy_id for copy_id, _ in queued],
            "dry_run": False,
        }

    @r.post(
        f"{base}/webhooks/{{name}}/test",
        auth=MANAGE,
        tags=["webhooks"],
        summary="Send a test event to one endpoint",
    )
    async def test_webhook(ctx: HttpContext, env: str, name: str):
        environment = await get_environment(env)
        endpoint = await WebhookEndpoint.get_or_none(environment=environment, name=name)
        if endpoint is None:
            raise HTTPException(status_code=404, detail="no such webhook")
        delivery = await WebhookDelivery.create(
            endpoint=endpoint,
            event_id=f"test-{endpoint.id}",
            event="pawabase.test",
            payload={"message": "Test delivery from Pawabase Studio"},
            status="pending",
        )
        job_id = await platform.dispatch(
            DeliverWebhookJob,
            env=env,
            target=name,
            source="studio",
            delivery_id=delivery.id,
            max_attempts=1,  # a test shows whether the endpoint answers now; it does not retry for days
        )
        return {"delivery_id": delivery.id, "job_id": job_id}

    @r.get(
        f"{base}/inbound-hooks/{{slug}}/url",
        auth=MANAGE,
        tags=["inbound hooks"],
        summary="The public URL of an inbound hook",
    )
    async def inbound_url(ctx: HttpContext, env: str, slug: str):
        environment = await get_environment(env)
        if not await InboundHook.filter(environment=environment, slug=slug).exists():
            raise HTTPException(status_code=404, detail="no such hook")
        return {"url": f"{platform.settings.public_url.rstrip('/')}/hooks/v1/{env}/{slug}"}

    # ── schedules ────────────────────────────────────────────────────────

    @r.post(
        f"{base}/schedules/{{name}}/run",
        auth=MANAGE,
        tags=["schedules"],
        summary="Fire a schedule now",
    )
    async def run_schedule(ctx: HttpContext, env: str, name: str):
        from pawabase_server.automation.scheduler import PlatformScheduler

        environment = await get_environment(env)
        schedule = await Schedule.get_or_none(environment=environment, name=name)
        if schedule is None:
            raise HTTPException(status_code=404, detail="no such schedule")
        spec = {
            "kind": "schedule",
            "id": schedule.id,
            "env": env,
            "cron": schedule.cron,
            "every": schedule.interval_seconds,
            "target_type": schedule.target_type,
            "target": schedule.target,
            "payload": schedule.payload,
            "name": f"{env}/{schedule.name}",
        }
        await PlatformScheduler(platform).fire(spec, manual=True)
        await schedule.refresh_from_db()
        return {"last_status": schedule.last_status, "run_count": schedule.run_count}
