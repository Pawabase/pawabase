"""Invoking functions (``/functions/v1``) and flows (``/flows/v1``) over HTTP."""

from __future__ import annotations

from typing import Any

from sillo import HttpContext, Router
from sillo import json as json_response
from sillo.auth.exceptions import AuthenticationFailed, PermissionDenied
from sillo.exceptions import HTTPException

from app.compiler.common import error_body
from app.execution import NotFound, call_function, run_flow
from app.platform import Platform
from pawabase_kit.context import require_context
from pawabase_kit.flows import FlowError
from pawabase_kit.functions import get_function
from pawabase_kit.policies import build_policy_context


async def _json_body(ctx: HttpContext) -> Any:
    import json

    raw = await ctx.body
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="the body must be JSON") from exc


async def _enforce(ctx: HttpContext, engine: Any, policy: Any, body: Any = None) -> dict[str, Any]:
    context = build_policy_context(ctx, input=body)
    decision = await engine.check(policy, context)
    if not decision:
        if not context["auth"]["authenticated"]:
            raise AuthenticationFailed("Authentication required")
        raise PermissionDenied(decision.reason)
    return context


def _flow_http_policy(flow: Any) -> tuple[str | None, Any]:
    """The direct-invocation trigger of a flow and its policy.

    Only flows with a ``trigger.http`` node that has no ``path`` can be invoked
    directly; path-bound triggers are served by custom routes.
    """
    for node in flow.definition.get("nodes", []):
        data = node.get("data") or {}
        config = data.get("config") or {}
        if data.get("block") == "trigger.http" and not config.get("path"):
            return node["id"], config.get("policy") or "authenticated"
    return None, None


def register(app: Any, platform: Platform) -> None:
    r = Router(prefix="/functions/v1", tags=["functions"])

    @r.post("/{name}", summary="Invoke a function")
    async def invoke_function(ctx: HttpContext, name: str):
        context = require_context(ctx)
        state = await platform.state_for(context)
        spec = get_function(context.project, name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no function {name!r}")
        if not context.allows_scope("functions:invoke"):
            raise PermissionDenied("This API key lacks the 'functions:invoke' scope")
        body = await _json_body(ctx)
        policy_context = await _enforce(ctx, state.engine, spec.policy, body)
        try:
            result = await call_function(
                platform,
                state,
                name,
                body,
                trigger="http",
                auth=policy_context["auth"],
                request_id=ctx.headers.get("x-request-id"),
            )
        except FlowError as exc:
            return json_response(
                error_body(exc.code, exc.message, exc.details), status_code=exc.status
            )
        return {"data": result}

    app.mount_router(r)

    f = Router(prefix="/flows/v1", tags=["flows"])

    @f.post("/{name}", summary="Invoke a flow directly")
    async def invoke_flow(ctx: HttpContext, name: str):
        context = require_context(ctx)
        state = await platform.state_for(context)
        flow = state.flows.get(name)
        entry, policy = _flow_http_policy(flow) if flow else (None, None)
        if flow is None or entry is None:
            raise HTTPException(status_code=404, detail=f"no directly invocable flow {name!r}")
        body = await _json_body(ctx)
        policy_context = await _enforce(ctx, state.engine, policy, body)
        try:
            run = await run_flow(
                platform,
                state,
                name,
                body,
                trigger="http",
                auth=policy_context["auth"],
                credential=policy_context["credential"],
                entry=entry,
                request_id=ctx.headers.get("x-request-id"),
            )
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FlowError as exc:
            code = (
                (exc.details or {}).get("code", "error")
                if exc.code == "raised" and isinstance(exc.details, dict)
                else exc.code
            )
            return json_response(
                error_body(code, exc.message, None if exc.code == "raised" else exc.details),
                status_code=exc.status,
            )
        if run.response is not None:
            return json_response(
                run.response.body,
                status_code=run.response.status,
                headers=run.response.headers or None,
            )
        return {"data": run.result()}

    app.mount_router(f)
