"""The bridge: how the browser reaches the platform's own services through Studio, and Studio's API explorer.

Studio holds the credentials. The browser never sees a service token or the internal secret: it calls ``/studio/api/<target>/...``
and Studio forwards that call, confined to one path prefix per target, as itself.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import re
import time
from typing import Any

from sillo import HttpContext, SilloApp
from sillo.responses import JSONResponse, stream

from pawabase_server.core.clients import ServiceError
from pawabase_server.core.context import PlatformContext
from studio.scope import body_allowed, narrow_runtime
from studio.support import Deps, error_response, failure_response, json_body

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
PUBLIC_PREFIXES = (
    "/functions/v1/",
    "/flows/v1/",
    "/auth/v1/",
    "/storage/v1/",
    "/files/v1/",
    "/realtime/v1/",
    "/health/v1",
    "/v1/status",
    "/docs/v1/",
    "/hooks/v1/",
)
ENV_NAME = re.compile(r"[a-z][a-z0-9_-]{0,62}")
API_VERSION = re.compile(r"v[1-9][0-9]*")
EXPLORER_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
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


async def _once(chunk: bytes):
    """Carry bytes that were already read in full as a one-item stream."""
    yield chunk


def _without_path_parameters(path_item: dict[str, Any], names: set[str]) -> dict[str, Any]:
    """Copy an OpenAPI path item after concrete values replaced placeholders."""
    result = copy.deepcopy(path_item)
    for value in [result, *(item for item in result.values() if isinstance(item, dict))]:
        if "parameters" in value:
            value["parameters"] = [
                parameter
                for parameter in value.get("parameters", [])
                if not (
                    isinstance(parameter, dict)
                    and parameter.get("in") == "path"
                    and parameter.get("name") in names
                )
            ]
    return result


def _function_body(path_item: dict[str, Any], function: dict[str, Any]) -> None:
    """Document a deployed function's declared input fields on its concrete operation."""
    fields = function.get("input_fields") or []
    if not fields:
        return
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in fields:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        name = str(field["name"])
        schema = {
            key: value
            for key, value in field.items()
            if key not in {"name", "required", "read_only", "write_only"}
        }
        properties[name] = schema
        if field.get("required"):
            required.append(name)
    body_schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        body_schema["required"] = required
    for operation in path_item.values():
        if isinstance(operation, dict) and operation.get("operationId"):
            operation["summary"] = function.get("description") or f"Invoke {function['name']}"
            operation["requestBody"] = {
                "required": bool(required),
                "content": {"application/json": {"schema": body_schema}},
            }
            operation["x-pawabase-inferred"] = "function"


def _document_manual_bodies(path: str, path_item: dict[str, Any]) -> None:
    """Fill the intentional OpenAPI gaps for handlers that parse streams or arbitrary JSON."""
    if path.startswith(("/flows/v1/", "/hooks/v1/")):
        operation = path_item.get("post")
        if isinstance(operation, dict) and "requestBody" not in operation:
            operation["requestBody"] = {
                "required": False,
                "content": {
                    "application/json": {
                        "schema": {"type": "object", "additionalProperties": True},
                        "example": {},
                    }
                },
            }
    if path.startswith("/storage/v1/object/") and isinstance(path_item.get("put"), dict):
        upload = path_item["put"]
        upload.setdefault(
            "requestBody",
            {
                "required": True,
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "properties": {"file": {"type": "string", "format": "binary"}},
                            "required": ["file"],
                        }
                    },
                },
            },
        )
        if "post" not in path_item:
            path_item["post"] = copy.deepcopy(upload)
            path_item["post"]["summary"] = "Upload an object (multipart or raw body)"
    if (
        path.startswith(("/storage/v1/object/", "/storage/v1/files/", "/storage/v1/share/"))
        and isinstance(path_item.get("get"), dict)
        and "head" not in path_item
    ):
        path_item["head"] = copy.deepcopy(path_item["get"])
        path_item["head"]["summary"] = "Read response headers"


def _expand_runtime_paths(
    paths: dict[str, Any], env: str, definitions: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    """Turn name templates into the concrete endpoints present in one environment."""
    expanded: dict[str, Any] = {}
    functions = definitions.get("functions", [])
    flows = definitions.get("flows", [])
    hooks = definitions.get("hooks", [])
    buckets = definitions.get("buckets", [])
    for path, path_item in paths.items():
        variants: list[tuple[str, dict[str, Any]]] = []
        if path == "/functions/v1/{name}" and functions:
            for function in functions:
                name = str(function.get("name") or "")
                if name:
                    item = _without_path_parameters(path_item, {"name"})
                    _function_body(item, function)
                    variants.append((path.replace("{name}", name), item))
        elif path == "/flows/v1/{name}" and flows:
            for flow in flows:
                definition = flow.get("definition") or {}
                direct = any(
                    (node.get("data") or {}).get("block") == "trigger.http"
                    and not ((node.get("data") or {}).get("config") or {}).get("path")
                    for node in definition.get("nodes", [])
                )
                if direct and flow.get("name"):
                    variants.append(
                        (
                            path.replace("{name}", str(flow["name"])),
                            _without_path_parameters(path_item, {"name"}),
                        )
                    )
        elif path == "/hooks/v1/{env}/{slug}" and hooks:
            for hook in hooks:
                if hook.get("slug"):
                    concrete = path.replace("{env}", env).replace("{slug}", str(hook["slug"]))
                    variants.append(
                        (concrete, _without_path_parameters(path_item, {"env", "slug"}))
                    )
        elif "{bucket}" in path and path.startswith("/storage/v1/") and buckets:
            for bucket in buckets:
                if bucket.get("name"):
                    variants.append(
                        (
                            path.replace("{bucket}", str(bucket["name"])),
                            _without_path_parameters(path_item, {"bucket"}),
                        )
                    )
        if variants:
            for concrete, item in variants:
                _document_manual_bodies(concrete, item)
                expanded[concrete] = item
        else:
            _document_manual_bodies(path, path_item)
            expanded[path] = path_item
    return expanded


#: Management sections a person's own environment has but that reach into another (copying, previews): never listed.
CROSS_ENVIRONMENT = {"promote", "previews"}


def _for_the_application(path: str) -> bool:
    """Whether the explorer lists *path*: the public data plane, and management of *this* environment only.

    Installation-wide routes (other environments, workers, the runtime agent, projects, the audit log) are the operator's, not the
    application developer's, and internal service routes are implementation details; neither is listed.
    """
    if path.startswith("/internal/"):
        return False
    if path.startswith(("/platform/", "/admin/")):
        segments = [s for s in path.split("/") if s]
        if "envs" not in segments or segments.index("envs") + 1 >= len(segments):
            return False
        if segments[segments.index("envs") + 1] != "{env}":
            return False
        return not CROSS_ENVIRONMENT.intersection(segments)
    return True


def register(app: SilloApp, deps: Deps) -> None:
    core = deps.core

    @app.get("/studio/api/explorer/openapi", exclude_from_schema=True)
    async def explorer_openapi(ctx: HttpContext):
        """Return every documented API surface alongside the compiled app API.

        The runtime document describes auth, functions, flows, storage,
        realtime and the management plane.  The environment document is built
        separately because resources and custom routes are compiled per
        environment and per released API version.
        """
        env = str(ctx.query_params.get("env") or "")
        version = str(ctx.query_params.get("version") or "v1")
        if not ENV_NAME.fullmatch(env):
            return JSONResponse({"detail": "invalid environment"}, status_code=400)
        if not outside_free(ctx, env):
            return JSONResponse(
                {"detail": "that environment is not part of your project"}, status_code=403
            )
        if not API_VERSION.fullmatch(version):
            return JSONResponse({"detail": "invalid API version"}, status_code=400)
        try:
            application = await core.request(
                "GET", f"/platform/v1/envs/{env}/openapi", params={"version": version}
            )
            runtime = await core.request("GET", "/openapi.json")
        except ServiceError as exc:
            return error_response(exc)
        # Internal service routes are implementation details, not endpoints an
        # application developer can call. Platform and admin routes remain in
        # the catalog: Studio executes those through its already-scoped bridge.
        public_paths = {
            path: item
            for path, item in (runtime.get("paths") or {}).items()
            if _for_the_application(path)
        }
        definition_paths = {
            "functions": f"/platform/v1/envs/{env}/functions",
            "flows": f"/platform/v1/envs/{env}/flows",
            "hooks": f"/platform/v1/envs/{env}/inbound-hooks",
            "buckets": f"/platform/v1/envs/{env}/buckets",
        }

        async def definitions(path: str) -> list[dict[str, Any]]:
            try:
                answer = await core.request("GET", path)
                return answer.get("data", []) if isinstance(answer, dict) else []
            except ServiceError:
                # The catalog is still useful if one optional definition source
                # is temporarily unavailable.
                return []

        values = await asyncio.gather(*(definitions(path) for path in definition_paths.values()))
        inferred = dict(zip(definition_paths, values, strict=True))
        public_paths = _expand_runtime_paths(public_paths, env, inferred)
        runtime = {**runtime, "paths": public_paths}
        return JSONResponse(
            {
                "openapi": "3.1.0",
                "environment": env,
                "version": version,
                "inferred": {name: len(rows) for name, rows in inferred.items()},
                "documents": [
                    {"id": "application", "title": "Application API", "document": application},
                    {"id": "runtime", "title": "Platform APIs", "document": runtime},
                ],
            }
        )

    @app.get("/studio/api/project/usage", exclude_from_schema=True)
    async def project_usage(ctx: HttpContext):
        """Usage for the confined project, never installation-wide counters."""
        project = ctx.scope.get("studio_project")
        if not project:
            return JSONResponse(
                {"detail": "project-scoped Studio access is required"}, status_code=403
            )
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
            return JSONResponse(
                {"detail": "that environment is not part of your project"}, status_code=403
            )
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
            return JSONResponse(
                {"detail": "that environment is not part of your project"}, status_code=403
            )
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
                return JSONResponse(
                    {"detail": "that is not a public endpoint path"}, status_code=400
                )
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
        payload = body.get("body")
        body_present = bool(body.get("body_present")) or (
            method in {"POST", "PUT", "PATCH", "DELETE"} and "body" in body
        )
        content_type = str(body.get("content_type") or "application/json")
        raw_content = None
        json_payload = payload if body_present and "json" in content_type.lower() else None
        if body_present and json_payload is None:
            try:
                if body.get("body_encoding") == "base64":
                    raw_content = base64.b64decode(str(payload or ""), validate=True)
                else:
                    raw_content = str(payload or "").encode()
            except (ValueError, TypeError):
                return JSONResponse({"detail": "invalid encoded request body"}, status_code=400)
            forwarded.setdefault("Content-Type", content_type)
        started = time.perf_counter()
        try:
            result = await core.request_raw(
                method,
                target_path,
                json=json_payload,
                content=raw_content,
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
                "size": len(json.dumps(result.get("body"), default=str))
                if result.get("body") is not None
                else 0,
            }
        )

    def object_guard(ctx: HttpContext, env: str, key: str) -> JSONResponse | None:
        if not ENV_NAME.fullmatch(env) or not outside_free(ctx, env):
            return JSONResponse(
                {"detail": "that environment is not part of your project"}, status_code=403
            )
        if not key or key.startswith("/") or ".." in key.split("/"):
            return JSONResponse({"detail": "bad object key"}, status_code=400)
        return None

    @app.get("/studio/api/object/{env}/{bucket}/{key:path}", exclude_from_schema=True)
    async def object_bytes(ctx: HttpContext, env: str, bucket: str, key: str):
        """Read one stored object as Studio, for previewing or saving it.

        Studio reads with its own service credential, so it never needs a link: the bucket's application policies are about
        the application's callers, not about the operator who administers the bucket. A ``Range`` header is passed through so
        video and audio can seek.
        """
        if (refusal := object_guard(ctx, env, key)) is not None:
            return refusal
        forwarded = {
            name: ctx.headers[name]
            for name in ("range", "if-none-match", "if-range", "accept")
            if name in ctx.headers
        }
        try:
            result = await core.request_raw(
                "GET",
                f"/storage/v1/object/{bucket}/{key}",
                params=dict(ctx.query_params),
                context=PlatformContext(env=env, role="service", key_id="studio"),
                headers=forwarded,
            )
        except ServiceError as exc:
            return error_response(exc)
        except Exception as exc:
            return failure_response(exc, "storage")
        if result["status"] >= 400:
            return JSONResponse(result.get("body"), status_code=result["status"])
        got = result.get("headers", {})
        headers = {
            name: got[name]
            for name in (
                "etag",
                "content-range",
                "accept-ranges",
                "content-disposition",
                "content-encoding",
                "last-modified",
            )
            if name in got
        }
        if "content-disposition" not in headers:
            name = re.sub(r'[\x00-\x1f"\\/]', "_", key.rsplit("/", 1)[-1])
            headers["content-disposition"] = f'inline; filename="{name}"'
        headers.update(
            {
                "x-content-type-options": "nosniff",
                "referrer-policy": "no-referrer",
                "cache-control": "private, max-age=0, must-revalidate",
            }
        )
        if got.get("content-type", "") != "application/pdf":
            headers["content-security-policy"] = "default-src 'none'; sandbox"
        return stream(
            _once(result["content"]),
            status_code=result["status"],
            content_type=got.get("content-type", "application/octet-stream"),
            headers=headers,
        )

    @app.put("/studio/api/object/{env}/{bucket}/{key:path}", exclude_from_schema=True)
    async def object_upload(ctx: HttpContext, env: str, bucket: str, key: str):
        """Upload or replace an object as Studio.

        This is a management operation: it is authorised by the operator's Studio session and the service credential,
        not by the bucket's application policies, so an administrator can always upload. The bucket's accepted types and size
        limits still apply, because those protect the data, not the application's callers.
        """
        if (refusal := object_guard(ctx, env, key)) is not None:
            return refusal
        try:
            result = await core.request_raw(
                "PUT",
                f"/platform/v1/envs/{env}/buckets/{bucket}/object",
                params={"key": key},
                content=await ctx.body,
                headers={
                    "content-type": ctx.headers.get("content-type", "application/octet-stream")
                },
            )
        except ServiceError as exc:
            return error_response(exc)
        except Exception as exc:
            return failure_response(exc, "storage")
        return JSONResponse(result.get("body"), status_code=result["status"])

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
            return JSONResponse(
                {"detail": "this setting is managed by your hosting service"}, status_code=403
            )
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
