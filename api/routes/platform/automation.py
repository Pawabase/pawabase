"""Flows, functions, events, webhooks and schedules: running and inspecting them."""

from __future__ import annotations

import fnmatch
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo.exceptions import HTTPException

from app.events import flow_event_entries
from app.execution import NotFound, call_function, run_flow
from app.platform import Platform
from database.models import (
    EventLog,
    FlowRun,
    InboundHook,
    Schedule,
    WebhookDelivery,
    WebhookEndpoint,
)
from pawabase_core.flows import FlowError, default_registry, validate_flow
from routes.common import OPERATOR, audit, dump, get_environment, page_params


class RunBody(BaseModel):
    input: Any = None
    entry: str | None = Field(default=None, description="Trigger node to start from")
    as_user: dict[str, Any] | None = Field(
        default=None, description="Run as if this auth context called it"
    )
    branch: str | None = Field(default=None, description="Run the code deployed on this branch (functions only)")


def _source_of(key: str) -> str:
    """Where a function came from: a branch deployment, the environment's deployment, or code mounted for the project."""
    return "branch" if "@" in key else "deployment" if "/" in key else "project"


class EmitBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    payload: Any = None


def _operator_auth(ctx: HttpContext) -> dict[str, Any]:
    from pawabase_core.principal import policy_auth

    return policy_auth(ctx.scope.get("user"))


def register(r: Router, platform: Platform) -> None:
    base = "/projects/{ref}/envs/{env}"

    # ── blocks ───────────────────────────────────────────────────────────

    @r.get("/blocks", auth=OPERATOR, tags=["flows"], summary="The block catalogue")
    async def blocks(ctx: HttpContext):
        catalogue = default_registry().catalogue()
        return {"count": len(catalogue), "data": catalogue}

    # ── flows ────────────────────────────────────────────────────────────

    @r.post(
        "/flows/validate",
        auth=OPERATOR,
        tags=["flows"],
        summary="Check a flow definition without saving it",
    )
    async def validate(ctx: HttpContext):
        definition = await ctx.json
        return {"problems": validate_flow(definition or {})}

    @r.post(
        f"{base}/flows/{{name}}/run",
        auth=OPERATOR,
        tags=["flows"],
        request_model=RunBody,
        summary="Run a flow now and return its trace",
    )
    async def run_now(ctx: HttpContext, ref: str, env: str, name: str, body: RunBody):
        state = await platform.state(ref, env)
        auth = body.as_user or {"authenticated": False, "kind": "operator"}
        request_id = getattr(ctx.state, "request_id", None)
        try:
            run = await run_flow(
                platform,
                state,
                name,
                body.input,
                trigger="manual",
                auth=auth,
                credential={"is_service": body.as_user is None, "role": "operator"},
                request_id=request_id,
                entry=body.entry,
            )
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FlowError as exc:
            record = (
                await FlowRun.filter(project=ref, env=env, flow=name)
                .order_by("-created_at")
                .first()
            )
            return {
                "status": "failed",
                "error": exc.message,
                "code": exc.code,
                "node": exc.node,
                "run_id": record.id if record else None,
                "trace": record.trace if record else [],
                "logs": record.logs if record else [],
            }
        await audit(ctx, "flow.run", project=ref, env=env, target=name)
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

    @r.get(f"{base}/flow-runs", auth=OPERATOR, tags=["flows"], summary="Recent flow runs")
    async def flow_runs(ctx: HttpContext, ref: str, env: str):
        limit, offset = page_params(ctx)
        query = FlowRun.filter(project=ref, env=env)
        if ctx.query_params.get("flow"):
            query = query.filter(flow=ctx.query_params["flow"])
        if ctx.query_params.get("status"):
            query = query.filter(status=ctx.query_params["status"])
        runs = await query.order_by("-created_at").offset(offset).limit(limit)
        return {"data": [dump(run, exclude=("trace", "logs")) for run in runs]}

    @r.get(
        f"{base}/flow-runs/{{run_id}}",
        auth=OPERATOR,
        tags=["flows"],
        summary="One flow run with its trace",
    )
    async def flow_run(ctx: HttpContext, ref: str, env: str, run_id: str):
        run = await FlowRun.get_or_none(id=run_id, project=ref, env=env)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return dump(run)

    # ── functions ────────────────────────────────────────────────────────

    @r.get(
        f"{base}/functions",
        auth=OPERATOR,
        tags=["functions"],
        summary="Functions loaded for the project",
    )
    async def functions(ctx: HttpContext, ref: str, env: str):
        await platform.state(ref, env)
        code = platform.ensure_code(ref)
        branch = ctx.query_params.get("branch") or None
        return {
            "data": [{**spec.describe(), "source": _source_of(spec.project)} for spec in platform.function_specs(ref, env, branch)],
            "branch": branch or "main",
            "modules": code.modules,
            "errors": code.errors,
            "router": code.router is not None,
        }

    @r.post(
        f"{base}/functions/{{name}}/invoke",
        auth=OPERATOR,
        tags=["functions"],
        request_model=RunBody,
        summary="Invoke a function now",
    )
    async def invoke(ctx: HttpContext, ref: str, env: str, name: str, body: RunBody):
        state = await platform.state(ref, env)
        try:
            result = await call_function(
                platform,
                state,
                name,
                body.input,
                trigger="manual",
                auth=body.as_user or _operator_auth(ctx),
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
        auth=OPERATOR,
        tags=["events"],
        summary="Recent events and their consumers",
    )
    async def events(ctx: HttpContext, ref: str, env: str):
        limit, offset = page_params(ctx)
        query = EventLog.filter(project=ref, env=env)
        if ctx.query_params.get("name"):
            query = query.filter(name=ctx.query_params["name"])
        if ctx.query_params.get("source"):
            query = query.filter(source=ctx.query_params["source"])
        return {"data": [dump(e) for e in await query.order_by("-id").offset(offset).limit(limit)]}

    @r.get(f"{base}/events/{{event_id}}", auth=OPERATOR, tags=["events"], summary="One event")
    async def event(ctx: HttpContext, ref: str, env: str, event_id: str):
        found = await EventLog.get_or_none(event_id=event_id, project=ref, env=env)
        if found is None:
            raise HTTPException(status_code=404, detail="no such event")
        return dump(found)

    @r.post(
        f"{base}/events",
        auth=OPERATOR,
        tags=["events"],
        request_model=EmitBody,
        summary="Publish an event (for testing consumers)",
    )
    async def emit(ctx: HttpContext, ref: str, env: str, body: EmitBody):
        state = await platform.state(ref, env)
        event_id = await platform.emit(state, body.name, body.payload, actor="studio")
        await audit(ctx, "event.emitted", project=ref, env=env, target=body.name)
        return {"event_id": event_id}

    @r.get(
        f"{base}/events-graph",
        auth=OPERATOR,
        tags=["events"],
        summary="Where events come from and what consumes them",
    )
    async def events_graph(ctx: HttpContext, ref: str, env: str):
        state = await platform.state(ref, env)
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
        seen = await EventLog.filter(project=ref, env=env).order_by("name").distinct().values_list("name", "source")
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
            from pawabase_core.events import PlatformEvent

            for flow_name, node in flow_event_entries(
                state, PlatformEvent(name=name, project=ref, env=env)
            ):
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
        auth=OPERATOR,
        tags=["webhooks"],
        summary="Outbound delivery history",
    )
    async def deliveries(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
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
        auth=OPERATOR,
        tags=["webhooks"],
        summary="Deliver again",
    )
    async def redeliver(ctx: HttpContext, ref: str, env: str, delivery_id: int):
        from app.jobs.webhooks import DeliverWebhookJob

        environment = await get_environment(ref, env)
        delivery = (
            await WebhookDelivery.filter(id=delivery_id, endpoint__environment=environment)
            .select_related("endpoint")
            .first()
        )
        if delivery is None:
            raise HTTPException(status_code=404, detail="no such delivery")
        copy = await WebhookDelivery.create(
            endpoint=delivery.endpoint,
            event_id=delivery.event_id,
            event=delivery.event,
            payload=delivery.payload,
            status="pending",
        )
        job_id = await platform.dispatch(
            DeliverWebhookJob,
            project=ref,
            env=env,
            target=delivery.endpoint.name,
            source="studio",
            delivery_id=copy.id,
        )
        return {"delivery_id": copy.id, "job_id": job_id}

    @r.post(
        f"{base}/webhooks/{{name}}/test",
        auth=OPERATOR,
        tags=["webhooks"],
        summary="Send a test event to one endpoint",
    )
    async def test_webhook(ctx: HttpContext, ref: str, env: str, name: str):
        from app.jobs.webhooks import DeliverWebhookJob

        environment = await get_environment(ref, env)
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
            project=ref,
            env=env,
            target=name,
            source="studio",
            delivery_id=delivery.id,
        )
        return {"delivery_id": delivery.id, "job_id": job_id}

    @r.get(
        f"{base}/inbound-hooks/{{slug}}/url",
        auth=OPERATOR,
        tags=["inbound hooks"],
        summary="The public URL of an inbound hook",
    )
    async def inbound_url(ctx: HttpContext, ref: str, env: str, slug: str):
        environment = await get_environment(ref, env)
        if not await InboundHook.filter(environment=environment, slug=slug).exists():
            raise HTTPException(status_code=404, detail="no such hook")
        return {"url": f"{platform.settings.public_url.rstrip('/')}/hooks/v1/{ref}/{env}/{slug}"}

    # ── schedules ────────────────────────────────────────────────────────

    @r.post(
        f"{base}/schedules/{{name}}/run",
        auth=OPERATOR,
        tags=["schedules"],
        summary="Fire a schedule now",
    )
    async def run_schedule(ctx: HttpContext, ref: str, env: str, name: str):
        from app.scheduler import PlatformScheduler

        environment = await get_environment(ref, env)
        schedule = await Schedule.get_or_none(environment=environment, name=name)
        if schedule is None:
            raise HTTPException(status_code=404, detail="no such schedule")
        spec = {
            "kind": "schedule",
            "id": schedule.id,
            "project": ref,
            "env": env,
            "cron": schedule.cron,
            "every": schedule.interval_seconds,
            "target_type": schedule.target_type,
            "target": schedule.target,
            "payload": schedule.payload,
            "name": f"{ref}/{env}/{schedule.name}",
        }
        await PlatformScheduler(platform).fire(spec)
        await schedule.refresh_from_db()
        return {"last_status": schedule.last_status, "run_count": schedule.run_count}
