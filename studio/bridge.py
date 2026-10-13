"""The bridge: how the browser reaches the platform's own services through Studio, and Studio's API explorer.

Studio holds the credentials. The browser never sees a service token or the internal secret: it calls ``/studio/api/<target>/...``
and Studio forwards that call, confined to one path prefix per target, as itself.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sillo import HttpContext, SilloApp
from sillo.responses import JSONResponse

from pawabase_server.core.clients import ServiceError
from pawabase_server.core.context import PlatformContext
from studio.scope import body_allowed, narrow_runtime
from studio.support import Deps, error_response, json_body

#: Bridge targets and the path prefix calls are confined to.
BRIDGE: dict[str, str] = {
    "platform": "/platform/v1/",
    "auth": "/admin/v1/",
    "realtime": "/internal/v1/realtime/",
    "telemetry": "/internal/v1/telemetry/",
    "gateway": "/internal/v1/gateway/",
}

#: The public data-plane prefixes the explorer and the terminal's ``http`` command may call, beyond ``/rest``. Never ``/platform``,
#: ``/admin`` or ``/internal``: those are management, and this request carries no operator.
PUBLIC_PREFIXES = ("/functions/v1/", "/flows/v1/", "/auth/v1/", "/storage/v1/", "/health/v1", "/docs/v1/", "/hooks/v1/")
ENV_NAME = re.compile(r"[a-z][a-z0-9_-]{0,62}")
API_VERSION = re.compile(r"v[1-9][0-9]*")
EXPLORER_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
#: Headers the explorer may not set: they would let a request borrow another identity or reach another host.
BLOCKED_HEADERS = {
    "apikey",
    "authorization",
    "cookie",
    "host",
    "x-pawabase-context",
    "x-pawabase-service",
}
#: Response headers worth showing the person exploring.
VISIBLE_HEADERS = {
    "cache-control",
    "content-length",
    "content-type",
    "deprecation",
    "etag",
    "location",
    "retry-after",
    "sunset",
    "x-request-id",
}


def outside_free(ctx: HttpContext, env: str) -> bool:
    """Whether *env* may be used by this request: always, unless the session is confined to a project that does not have it."""
    project = ctx.scope.get("studio_project")
    return project is None or env in ctx.scope.get("studio_envs", ())


def register(app: SilloApp, deps: Deps) -> None:
    core = deps.core

    @app.get("/studio/api/project/usage", exclude_from_schema=True)
    async def project_usage(ctx: HttpContext):
        """Usage for the confined project, never installation-wide counters."""
        project = ctx.scope.get("studio_project")
        if not project:
            return JSONResponse({"detail": "project-scoped Studio access is required"}, status_code=403)
        try:
            result = await core.request(
                "GET", "/internal/v1/gateway/usage", params={"project": project}
            )
        except ServiceError as exc:
            return error_response(exc)
        return JSONResponse(result)

    @app.post("/studio/api/explorer/sign-in", exclude_from_schema=True)
    async def explorer_sign_in(ctx: HttpContext):
        """Obtain an application-user token for Explorer without an API key."""
        body = await json_body(ctx)
        env = str(body.get("env") or "")
        if not ENV_NAME.fullmatch(env):
            return JSONResponse({"detail": "invalid environment"}, status_code=400)
        if not outside_free(ctx, env):
            return JSONResponse({"detail": "that environment is not part of your project"}, status_code=403)
        if body.get("mfa_token"):
            payload = {
                "grant_type": "mfa",
                "mfa_token": str(body["mfa_token"]),
                "code": str(body.get("code") or ""),
            }
        else:
            payload = {
                "grant_type": "password",
                "email": str(body.get("email") or ""),
                "password": str(body.get("password") or ""),
            }
        try:
            result = await core.request(
                "POST",
                "/auth/v1/token",
                json=payload,
                context=PlatformContext(env=env, role="anon", key_id="studio-explorer"),
            )
        except ServiceError as exc:
            return error_response(exc)
        visible = {
            key: result[key]
            for key in ("access_token", "token_type", "expires_in", "mfa_required", "mfa_token")
            if key in result
        }
        return JSONResponse(visible)

    @app.post("/studio/api/explorer/request", exclude_from_schema=True)
    async def explorer_request(ctx: HttpContext):
        """Run one data-plane request with a signed anonymous context.

        Studio deliberately uses an ``anon`` context for the explored request. This keeps policy behavior honest: public endpoints work
        immediately and authenticated endpoints only work when an application-user token is supplied.
        """
        body = await json_body(ctx)
        env = str(body.get("env") or "")
        version = str(body.get("version") or "v1")
        method = str(body.get("method") or "GET").upper()
        requested_path = str(body.get("path") or "/")
        if not ENV_NAME.fullmatch(env):
            return JSONResponse({"detail": "invalid environment"}, status_code=400)
        if not outside_free(ctx, env):
            return JSONResponse({"detail": "that environment is not part of your project"}, status_code=403)
        if not API_VERSION.fullmatch(version):
            return JSONResponse({"detail": "invalid API version"}, status_code=400)
        if method not in EXPLORER_METHODS:
            return JSONResponse({"detail": "unsupported method"}, status_code=400)
        endpoint = str(body.get("endpoint") or "")
        if endpoint:
            # A full public path, for anything beyond resources: functions, flows, sign-in, files, health.
            clean = endpoint.split("?", 1)[0]
            segments = clean.split("/")[1:]
            if (
                not clean.startswith("/")
                or "\\" in clean
                or any(segment in ("", ".", "..") for segment in segments[:-1])
                or segments[-1:] == [".."]
                or any(ord(c) < 0x20 for c in clean)
                or not (clean.startswith("/rest/v") or clean.startswith(PUBLIC_PREFIXES))
            ):
                return JSONResponse({"detail": "that is not a public endpoint path"}, status_code=400)
            target_path = endpoint
        else:
            expected_prefix = f"/rest/{version}"
            if requested_path == expected_prefix:
                requested_path = "/"
            elif requested_path.startswith(expected_prefix + "/"):
                requested_path = requested_path[len(expected_prefix) :]
            if not requested_path.startswith("/"):
                requested_path = "/" + requested_path
            if ".." in requested_path.split("/") or requested_path.startswith("/rest/"):
                return JSONResponse({"detail": "invalid endpoint path"}, status_code=400)
            target_path = expected_prefix + requested_path

        supplied = body.get("headers") if isinstance(body.get("headers"), dict) else {}
        forwarded = {
            str(key): str(value)
            for key, value in supplied.items()
            if str(key).lower() not in BLOCKED_HEADERS and value not in (None, "")
        }
        access_token = str(body.get("access_token") or "").strip()
        if access_token.lower().startswith("bearer "):
            access_token = access_token[7:].strip()
        if access_token:
            forwarded["Authorization"] = f"Bearer {access_token}"
        query = body.get("query") if isinstance(body.get("query"), dict) else None
        payload = body.get("body") if method in {"POST", "PUT", "PATCH", "DELETE"} else None
        started = time.perf_counter()
        try:
            result = await core.request_raw(
                method,
                target_path,
                json=payload,
                params=query,
                context=PlatformContext(env=env, role="anon", key_id="studio-explorer"),
                headers=forwarded,
            )
        except Exception as exc:
            return JSONResponse(
                {
                    "detail": f"the API could not be reached: {type(exc).__name__}",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                status_code=502,
            )
        return JSONResponse(
            {
                "status": result["status"],
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "headers": {
                    key: value
                    for key, value in result.get("headers", {}).items()
                    if key.lower() in VISIBLE_HEADERS
                },
                "body": result.get("body"),
                "size": len(json.dumps(result.get("body"), default=str)) if result.get("body") is not None else 0,
            }
        )

    async def bridge(ctx: HttpContext, target: str, path: str):
        if target not in BRIDGE:
            return JSONResponse({"detail": "unknown service"}, status_code=404)
        prefix = BRIDGE[target]
        clean = path.lstrip("/")
        if ".." in clean.split("/"):
            return JSONResponse({"detail": "bad path"}, status_code=400)
        body = None
        if ctx.method in ("POST", "PUT", "PATCH", "DELETE"):
            raw = await ctx.body
            if raw:
                try:
                    body = json.loads(raw)
                except ValueError:
                    return JSONResponse({"detail": "send JSON"}, status_code=400)
        confined = ctx.scope.get("studio_project")
        if confined and not body_allowed("/" + clean, ctx.method, body):
            return JSONResponse({"detail": "this setting is managed by your hosting service"}, status_code=403)
        params = dict(ctx.query_params)
        # A tab that loaded the old Usage page asks the generic gateway bridge.
        # Keep it working, but narrow the answer exactly as the dedicated route
        # above does—never return the installation's other project counters.
        if confined and target == "gateway" and clean == "usage" and ctx.method == "GET":
            try:
                scoped = await core.request(
                    "GET", "/internal/v1/gateway/usage", params={"project": confined}
                )
            except ServiceError as exc:
                return error_response(exc)
            return JSONResponse((scoped.get("project") or {}).get("usage") or {})
        call: dict[str, Any] = {"json": body, "params": params or None}
        segments = clean.split("/")
        if (
            target == "realtime"
            and ctx.method == "POST"
            and len(segments) == 2
            and segments[1] == "publish"
        ):
            # Broadcasting acts inside one environment, which realtime reads from the platform context rather than from the path.
            prefix, clean = "/internal/v1/publish", ""
            call["context"] = PlatformContext(env=segments[0], role="service", key_id="studio")
        try:
            result = await core.request(ctx.method, prefix + clean, **call)
        except ServiceError as exc:
            return error_response(exc)
        if result in ({}, None, "") and ctx.method == "DELETE":
            return JSONResponse(None, status_code=204)
        if confined and target == "platform" and clean == "runtime":
            result = narrow_runtime(result, confined)
        return JSONResponse(result)

    app.route(
        "/studio/api/{target}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        handler=bridge,
        name="bridge",
        exclude_from_schema=True,
    )
