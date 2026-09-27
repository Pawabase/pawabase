"""Custom routes compiled into Sillo routes.

A custom route names a method, a path (Sillo syntax, ``/orders/{id}/pay``), a
policy, optional input and response schemas, and a handler: a Flow or a Python
function. The handler receives::

    {"params": {"id": "42"}, "query": {...}, "body": {...validated...}}

A flow answers with ``response.return``; otherwise its result, or the
function's return value, is sent as JSON.
"""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING, Any

from sillo import HttpContext
from sillo import json as json_response
from sillo.auth.exceptions import AuthenticationFailed, PermissionDenied
from sillo.helpers.strings import pascal_case

from app.compiler.common import PlanGate, cache_key
from pawabase_kit.flows import FlowError
from pawabase_kit.policies import build_policy_context
from pawabase_kit.ratelimit import rate_limit_middleware
from pawabase_kit.schemas import compile_model
from pawabase_kit.telemetry import note
from pawabase_kit.transformers import apply_transformer

if TYPE_CHECKING:
    from sillo import SilloApp

    from app.state import EnvironmentState

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _model_name(route: Any, suffix: str) -> str:
    base = pascal_case(
        route.name
        or route.path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
        or "root"
    )
    return f"{base}{suffix}"


def register_route(app: SilloApp, state: EnvironmentState, route: Any) -> str:
    method = route.method.upper()
    if method not in METHODS:
        raise ValueError(f"unsupported method {method}")
    platform = state.platform
    request_model = None
    if method in ("POST", "PUT", "PATCH"):
        if route.input_fields:
            request_model = compile_model(
                _model_name(route, "Input"),
                route.input_fields,
                mode="create",
                registry=state.compiled_schemas,
            )
        elif route.input_schema:
            request_model = state.compiled_schemas.get(route.input_schema)
    response_model = (
        state.compiled_schemas.get(route.response_schema)
        if route.response_schema and not route.transformer
        else None
    )
    limits = rate_limit_middleware(
        route.rate_limit,
        f"rl:{state.project_ref}:{state.env_name}:route:{route.id}",
        platform.settings.redis_url,
    )
    policy = route.policy or "authenticated"
    # The gate decides what it can before the body is read; conditions on
    # $input are checked in the handler, once the body has been validated.
    gate = PlanGate(policy, engine=state.engine, scope="routes:invoke")

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
                return json_response(
                    cached["body"], status_code=cached["status"], headers=cached["headers"]
                )
        decision = await state.engine.check(policy, build_policy_context(ctx, input=payload["body"]))
        if not decision:
            user = ctx.scope.get("user")
            if user is None or not getattr(user, "is_authenticated", False):
                raise AuthenticationFailed("Authentication required")
            raise PermissionDenied(decision.reason)
        context = build_policy_context(ctx)
        try:
            request_id = ctx.state.request_id
        except Exception:
            request_id = None
        status, headers = 200, {}
        if route.handler_type == "function":
            body_value = payload["body"]
            function_input = (
                {**payload["params"], **body_value}
                if isinstance(body_value, dict)
                else (body_value if body_value is not None else dict(payload["params"]))
            )
            result = await call_function(
                platform,
                state,
                route.handler,
                function_input,
                trigger="http",
                auth=context["auth"],
                request_id=request_id,
                request=payload,
            )
        else:
            entry = _http_entry(state, route)
            run = await run_flow(
                platform,
                state,
                route.handler,
                payload,
                trigger="http",
                auth=context["auth"],
                credential=context["credential"],
                request_id=request_id,
                entry=entry,
            )
            if run.response is not None:
                status, headers, result = (
                    run.response.status,
                    run.response.headers,
                    run.response.body,
                )
            else:
                result = run.result()
        note("handler", f"{route.handler_type}:{route.handler}")
        if route.transformer:
            result = await apply_transformer(
                result, route.transformer, context=context, registry=state.transformers
            )
        if key and status < 400:
            await platform.cache_set(
                state,
                key,
                {"body": result, "status": status, "headers": headers},
                ttl=route.cache_ttl,
            )
        return json_response(result, status_code=status, headers=headers or None)

    handler = _make_handler(
        route.path,
        run_handler,
        with_body=request_model is not None,
        reads_json=method in ("POST", "PUT", "PATCH"),
    )

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


PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[A-Za-z]+)?\}")


def _make_handler(path: str, run_handler: Any, *, with_body: bool, reads_json: bool) -> Any:
    """A handler whose signature names the route's path parameters.

    Sillo resolves handler arguments from the signature: path parameters by
    name, and the ``request_model`` body into the first remaining parameter.
    A compiled route's parameters are only known at runtime, so the signature
    is built here, the same way ``sillo-inertia`` builds its page handlers'.
    """
    names = PATH_PARAM.findall(path)

    async def handler(ctx: HttpContext, *args: Any, **kwargs: Any):
        body = kwargs.pop("body", None)
        if not with_body and reads_json:
            try:
                body = await ctx.json
            except Exception:
                body = None
        return await run_handler(ctx, body)

    parameters = [
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=HttpContext)
    ]
    parameters += [
        inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)
        for name in names
    ]
    if with_body:
        parameters.append(inspect.Parameter("body", inspect.Parameter.POSITIONAL_OR_KEYWORD))
    handler.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    return handler


def _http_entry(state: EnvironmentState, route: Any) -> str | None:
    """The trigger node a route should start its flow at.

    A flow may carry several HTTP triggers; the one whose method and path
    match the route wins, else the first HTTP trigger, else the default entry.
    """
    flow = state.flows.get(route.handler)
    if flow is None:
        raise FlowError(
            f"route handler flow {route.handler!r} does not exist", status=500, code="missing_flow"
        )
    first = None
    for node in flow.definition.get("nodes", []):
        data = node.get("data") or {}
        if data.get("block") != "trigger.http":
            continue
        config = data.get("config") or {}
        if first is None:
            first = node["id"]
        if (
            config.get("path") == route.path
            and (config.get("method") or "POST").upper() == route.method.upper()
        ):
            return node["id"]
    return first
