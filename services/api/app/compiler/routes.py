"""Custom routes compiled into Sillo routes.

A custom route names a method, a path (Sillo syntax, ``/orders/{id}/pay``), a
policy, optional input and response schemas, and a handler: a Flow or a Python
function. The handler receives::

    {"params": {"id": "42"}, "query": {...}, "body": {...validated...}}

A flow answers with ``response.return``; otherwise its result, or the
function's return value, is sent as JSON.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pawabase_kit.flows import FlowError
from pawabase_kit.policies import PolicyGate, build_policy_context
from pawabase_kit.schemas import compile_model
from pawabase_kit.telemetry import note
from pawabase_kit.transformers import apply_transformer
from sillo import HttpContext
from sillo import json as json_response
from sillo.helpers.strings import pascal_case

from app.compiler.common import cache_key, rate_limit_middleware

if TYPE_CHECKING:
    from sillo import SilloApp

    from app.state import EnvironmentState

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _model_name(route: Any, suffix: str) -> str:
    base = pascal_case(route.name or route.path.strip("/").replace("/", "_").replace("{", "").replace("}", "") or "root")
    return f"{base}{suffix}"


def register_route(app: SilloApp, state: EnvironmentState, route: Any) -> str:
    method = route.method.upper()
    if method not in METHODS:
        raise ValueError(f"unsupported method {method}")
    platform = state.platform
    request_model = None
    if method in ("POST", "PUT", "PATCH"):
        if route.input_fields:
            request_model = compile_model(_model_name(route, "Input"), route.input_fields, mode="create", registry=state.compiled_schemas)
        elif route.input_schema:
            request_model = state.compiled_schemas.get(route.input_schema)
    response_model = state.compiled_schemas.get(route.response_schema) if route.response_schema and not route.transformer else None
    limits = rate_limit_middleware(route.rate_limit, f"rl:{state.project_ref}:{state.env_name}:route:{route.id}", platform.settings.redis_url)
    gate = PolicyGate(route.policy or "authenticated", engine=state.engine, scope="routes:invoke")

    async def run_handler(ctx: HttpContext, body: Any) -> Any:
        from app.execution import call_function, run_flow

        payload = {
            "params": {key: ctx.path_params[key] for key in ctx.path_params},
            "query": dict(ctx.query_params),
            "body": body.model_dump(mode="json") if hasattr(body, "model_dump") else body,
        }
        key = None
        if route.cache_ttl and method == "GET":
            key = f"route:{route.id}:{cache_key(ctx, payload)}"
            cached = await platform.cache_get(state, key)
            if cached is not None:
                return json_response(cached["body"], status_code=cached["status"], headers=cached["headers"])
        context = build_policy_context(ctx)
        try:
            request_id = ctx.state.request_id
        except Exception:
            request_id = None
        status, headers = 200, {}
        if route.handler_type == "function":
            result = await call_function(platform, state, route.handler, payload, trigger="http", auth=context["auth"], request_id=request_id)
        else:
            entry = _http_entry(state, route)
            run = await run_flow(
                platform, state, route.handler, payload, trigger="http", auth=context["auth"],
                credential=context["credential"], request_id=request_id, entry=entry,
            )
            if run.response is not None:
                status, headers, result = run.response.status, run.response.headers, run.response.body
            else:
                result = run.result()
        note("handler", f"{route.handler_type}:{route.handler}")
        if route.transformer:
            result = await apply_transformer(result, route.transformer, context=context, registry=state.transformers)
        if key and status < 400:
            await platform.cache_set(state, key, {"body": result, "status": status, "headers": headers}, ttl=route.cache_ttl)
        return json_response(result, status_code=status, headers=headers or None)

    if request_model is not None:

        async def handler(ctx: HttpContext, body):
            return await run_handler(ctx, body)

    else:

        async def handler(ctx: HttpContext):
            body: Any = None
            if method in ("POST", "PUT", "PATCH"):
                try:
                    body = await ctx.json
                except Exception:
                    body = None
            return await run_handler(ctx, body)

    getattr(app, method.lower())(
        route.path,
        handler=handler,
        name=f"route.{route.id}",
        summary=route.name or f"{method} {route.path}",
        description=route.description or None,
        tags=list(route.tags or ["routes"]),
        request_model=request_model,
        response_model=response_model,
        auth=gate,
        middleware=limits,
    )
    return f"{method} {route.path}"


def _http_entry(state: EnvironmentState, route: Any) -> str | None:
    """The trigger node a route should start its flow at.

    A flow may carry several HTTP triggers; the one whose method and path
    match the route wins, else the first HTTP trigger, else the default entry.
    """
    flow = state.flows.get(route.handler)
    if flow is None:
        raise FlowError(f"route handler flow {route.handler!r} does not exist", status=500, code="missing_flow")
    first = None
    for node in flow.definition.get("nodes", []):
        data = node.get("data") or {}
        if data.get("block") != "trigger.http":
            continue
        config = data.get("config") or {}
        if first is None:
            first = node["id"]
        if config.get("path") == route.path and (config.get("method") or "POST").upper() == route.method.upper():
            return node["id"]
    return first
