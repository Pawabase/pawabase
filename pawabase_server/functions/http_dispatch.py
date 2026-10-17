"""Serving HTTP functions: ``/functions/v1/<name>/…`` and, without a key, ``/functions/v1/public/<env>/<name>/…``.

This ASGI middleware runs inside the platform context (after the gateway resolved the key and applied the firewall, the key's rules,
rate limits and the body-size limit). For a path that names an HTTP function of the environment it:

1. checks the function's ``config``: the methods it answers, the origins it is readable from, and **who may call it** —
   ``public`` (anyone, also without a key), ``key`` (any key of the environment: the default), ``user`` (a signed-in user), ``service``
   (a secret key), or any policy, evaluated like a resource's;
2. checks the key's ``functions:invoke`` scope, counts the invocation against the plan, and takes a project execution slot;
3. signs a grant for the invocation and hands the request to the functions runtime (in this process or the runtime service), streaming
   the body in and the response out, with the caller's ``Authorization`` and ``apikey`` headers passed through so the function can act
   as the caller with a Pawabase client;
4. stops the function when the caller goes away.

A path that names no HTTP function goes on to the ``fn()`` routes (``POST /functions/v1/<name>``), unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from sillo.exceptions import HTTPException

from pawabase_server.core.context import SCOPE_KEY
from pawabase_server.core.principal import ANONYMOUS_POLICY_CONTEXT, Principal
from pawabase_server.core.telemetry import note
from pawabase_server.core.tokens import TokenInvalid, verify_user_token
from pawabase_server.flows import FlowError
from pawabase_server.functions.execution import metered
from pawabase_server.functions.http_functions import (
    AUTH_KEY,
    AUTH_PUBLIC,
    AUTH_SERVICE,
    AUTH_USER,
    HttpFunctionSpec,
)
from pawabase_server.functions.limits import ConcurrencyLimitReached
from pawabase_server.functions.registry import MAIN
from pawabase_server.functions.runtime import ApiRuntime
from pawabase_server.functions.service.grants import Grant, new_invocation_id
from pawabase_server.functions.service.runtime import HttpRequest, variables_version

logger = logging.getLogger("pawabase.functions")

PREFIX = "/functions/v1/"
PUBLIC_PREFIX = "/functions/v1/public/"
BRANCH_HEADER = "x-pawabase-branch"


async def _send_json(send: Any, status: int, code: str, message: str, details: Any = None, headers: list | None = None) -> None:
    payload = json.dumps({"error": code, "message": message, "details": details}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *(headers or []),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def split_path(path: str) -> tuple[str | None, str | None, str, bool] | None:
    """``(env or None, name, rest, keyless)`` for a functions path, or ``None`` when it is not one."""
    if path.startswith(PUBLIC_PREFIX):
        parts = path[len(PUBLIC_PREFIX) :].split("/", 2)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return None
        rest = "/" + parts[2] if len(parts) > 2 else "/"
        return parts[0], parts[1], rest, True
    if path.startswith(PREFIX):
        parts = path[len(PREFIX) :].split("/", 1)
        if not parts[0]:
            return None
        rest = "/" + parts[1] if len(parts) > 1 else "/"
        return None, parts[0], rest, False
    return None


class HttpFunctionDispatcher:
    def __init__(self, platform: Any) -> None:
        self.platform = platform
        self.app: Any = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        parsed = split_path(scope.get("path", ""))
        if parsed is None:
            await self.app(scope, receive, send)
            return
        path_env, name, rest, keyless = parsed
        context = scope.get(SCOPE_KEY)
        env = path_env if keyless else (context.env if context is not None else None)
        if env is None:
            await self.app(scope, receive, send)  # no key: the fn() route answers 401 as it always has
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        branch = headers.get(BRANCH_HEADER) or None
        try:
            spec = self.platform.http_function(env, name, branch)
        except Exception:  # noqa: BLE001 - a deployment that cannot be read must not take the fn() routes down
            logger.exception("could not resolve HTTP function %s/%s", env, name)
            spec = None
        if spec is None:
            if keyless:
                await _send_json(send, 404, "not_found", f"There is no public function {name!r} in {env!r}.")
                return
            await self.app(scope, receive, send)
            return
        note("function", name)
        await self._serve(scope, receive, send, spec, rest, headers, context, keyless)

    # ── who may call ─────────────────────────────────────────────────────

    def _principal(self, env: str, headers: dict[str, str], *, lenient: bool) -> tuple[Principal | None, str | None]:
        """The signed-in user of the request, if any. A bad token is a refusal unless *lenient* (public functions ignore it)."""
        value = headers.get("authorization") or ""
        if value[:7].lower() != "bearer ":
            return None, None
        token = value[7:].strip()
        try:
            claims = verify_user_token(token, self.platform.settings.jwt_master_secret, env=env)
        except TokenInvalid as exc:
            return None, None if lenient else f"The access token is not valid here: {exc}"
        return Principal("user", claims), None

    async def _authorize(
        self, spec: HttpFunctionSpec, scope: dict[str, Any], headers: dict[str, str], context: Any, keyless: bool, state: Any
    ) -> tuple[dict[str, Any], tuple[int, str, str] | None]:
        """The caller's ``auth`` as policies see it, or a refusal ``(status, code, message)``."""
        auth = spec.config.auth
        if keyless and auth != AUTH_PUBLIC:
            return {}, (401, "missing_api_key", "This function needs an API key; call it at /functions/v1/<name> with one.")
        if context is not None and not context.allows_scope("functions:invoke"):
            return {}, (403, "forbidden", "This API key lacks the 'functions:invoke' scope.")
        principal, problem = self._principal(spec.env, headers, lenient=auth == AUTH_PUBLIC)
        if problem:
            return {}, (401, "invalid_token", problem)
        caller = principal.as_policy_context() if principal is not None else dict(ANONYMOUS_POLICY_CONTEXT)
        if auth in (AUTH_PUBLIC, AUTH_KEY):
            return caller, None
        if auth == AUTH_USER:
            if principal is None:
                return caller, (401, "unauthenticated", "Sign in first: this function needs a user's access token.")
            return caller, None
        if auth == AUTH_SERVICE:
            if context is None or not context.is_service:
                return caller, (403, "forbidden", "This function is for servers: call it with a secret key.")
            return caller, None
        client = scope.get("client")
        policy_context = {
            "auth": caller,
            "credential": {
                "role": context.role if context is not None else "none",
                "is_service": bool(context is not None and context.is_service),
                "scopes": list(context.scopes) if context is not None else [],
                "key_id": context.key_id if context is not None else None,
            },
            "request": {
                "method": scope.get("method"),
                "path": scope.get("path"),
                "ip": client[0] if client else None,
                "params": {},
                "query": {},
            },
            "env": spec.env,
            "record": None,
            "input": None,
        }
        decision = await state.engine.check(auth, policy_context)
        if not decision:
            if not caller.get("authenticated"):
                return caller, (401, "unauthenticated", "Sign in first.")
            return caller, (403, "forbidden", getattr(decision, "reason", None) or "Not allowed.")
        return caller, None

    # ── serving ──────────────────────────────────────────────────────────

    async def _serve(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
        spec: HttpFunctionSpec,
        rest: str,
        headers: dict[str, str],
        context: Any,
        keyless: bool,
    ) -> None:
        platform = self.platform
        method = scope.get("method", "GET").upper()
        if method not in spec.config.methods:
            allow = ", ".join(sorted(spec.config.methods))
            await _send_json(send, 405, "method_not_allowed", f"{spec.name} answers {allow}.", headers=[(b"allow", allow.encode())])
            return
        origin = headers.get("origin")
        if spec.config.cors is not None and origin and spec.config.cors.allows(origin) is None:
            await _send_json(send, 403, "origin_not_allowed", f"{spec.name} is not readable from {origin}.")
            return
        try:
            state = await platform.state(spec.env)
        except HTTPException as exc:
            await _send_json(send, exc.status_code, "environment_unavailable", str(exc.detail))
            return
        auth, refusal = await self._authorize(spec, scope, headers, context, keyless, state)
        if refusal is not None:
            await _send_json(send, *refusal)
            return
        limits = await platform.run_limits(state, spec, kind="http")
        max_body = limits.max_body * 1024 * 1024
        try:
            declared = int(headers.get("content-length") or 0)
        except ValueError:
            declared = 0
        if max_body and declared > max_body:
            await _send_json(
                send, 413, "payload_too_large", f"{spec.name} accepts at most {limits.max_body} MB.", {"limit": limits.max_body}
            )
            return
        try:
            await metered(platform, state, "max_function_invocations_per_month")
        except FlowError as exc:
            await _send_json(send, exc.status, exc.code, exc.message, exc.details)
            return
        request_id = headers.get("x-request-id")
        invocation = new_invocation_id()
        branch = spec.branch or MAIN
        runtime = ApiRuntime(platform, state, auth=auth, request_id=request_id, depth=0, branch=branch)
        grant = Grant(
            env=spec.env,
            branch=branch,
            function=spec.name,
            invocation=invocation,
            auth=auth,
            request_id=request_id,
            depth=0,
            kind="http",
            deployment_id=spec.deployment_id,
        )
        request = HttpRequest(
            method=method,
            url=self._public_url(scope, headers),
            path=rest,
            headers=[(k.decode("latin-1"), v.decode("latin-1")) for k, v in scope.get("headers", [])],
            body=self._body(receive, max_body) if self._has_body(method, headers) else None,
            invocation=invocation,
            request_id=request_id,
            auth=auth,
            project=str(state.project_id or ""),
            client_ip=(scope.get("client") or (None,))[0],
            variables_version=variables_version(dict(state.secret_values)),
            limits=limits,
        )
        async with contextlib.AsyncExitStack() as stack:
            try:
                await stack.enter_async_context(
                    platform.project_execution.acquire(
                        state.project_id,
                        max_concurrent=int(state.project_limits.get("max_function_concurrency") or 0),
                        max_queued=int(state.project_limits.get("max_queued_jobs") or 0),
                    )
                )
                await stack.enter_async_context(
                    platform.function_gates.slot(
                        spec.env, spec.name, limits.concurrency, wait=platform.settings.functions_queue_seconds
                    )
                )
            except ConcurrencyLimitReached as exc:
                await _send_json(
                    send, 429, "too_many_runs", str(exc), {"limit": exc.limit}, headers=[(b"retry-after", b"1")]
                )
                return
            except Exception as exc:  # noqa: BLE001 - the plan's concurrency limit, in the platform's error shape
                await _send_json(send, getattr(exc, "status", 429), getattr(exc, "code", "too_many_invocations"), str(exc))
                return
            reply = await platform.functions.http(spec, request, runtime=runtime, grant=grant)
            await self._respond(send, receive, reply, spec, origin, request.body is not None)

    @staticmethod
    def _has_body(method: str, headers: dict[str, str]) -> bool:
        if method in ("GET", "HEAD"):
            return False
        if headers.get("transfer-encoding", "").lower() == "chunked":
            return True
        try:
            return int(headers.get("content-length") or 0) > 0
        except ValueError:
            return False

    @staticmethod
    async def _body(receive: Any, limit: int = 0) -> AsyncIterator[bytes]:
        """The request body as it arrives; one that runs past *limit* bytes is broken off (the function sees an aborted body)."""
        received = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise ConnectionResetError("the caller went away while sending the body")
            chunk = message.get("body", b"")
            received += len(chunk)
            if limit and received > limit:
                raise ValueError(f"the request body is larger than the function's {limit // (1024 * 1024)} MB limit")
            if chunk:
                yield chunk
            if not message.get("more_body", False):
                return

    def _public_url(self, scope: dict[str, Any], headers: dict[str, str]) -> str:
        """The URL as the caller used it: what the function sees as ``req.url``."""
        host = headers.get("x-forwarded-host") or headers.get("host")
        scheme = headers.get("x-forwarded-proto") or scope.get("scheme") or "http"
        base = f"{scheme}://{host}" if host else self.platform.settings.public_url.rstrip("/")
        query = scope.get("query_string", b"").decode("latin-1")
        path = quote(scope.get("path", "/"), safe="/:@!$&'()*+,;=-._~")
        return f"{base}{path}" + (f"?{query}" if query else "")

    async def _respond(
        self, send: Any, receive: Any, reply: Any, spec: HttpFunctionSpec, origin: str | None, body_read_by_runtime: bool
    ) -> None:
        headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in reply.headers]
        if spec.config.cors is not None:
            existing = {k for k, _ in headers}
            for key, value in spec.config.cors.response_headers(origin):
                if key.encode() not in existing:
                    headers.append((key.encode(), value.encode()))
        headers.append((b"x-pawabase-function", spec.name.encode()))
        if reply.cold_start:
            headers.append((b"x-pawabase-cold-start", b"1"))
        await send({"type": "http.response.start", "status": reply.status, "headers": headers})
        disconnected = asyncio.Event()

        async def watch() -> None:
            # Once the body has been read, the only thing left to receive is the caller going away.
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected.set()
                    return

        watcher = None if body_read_by_runtime else asyncio.create_task(watch())
        body = reply.body
        try:
            async for chunk in body:
                if disconnected.is_set():
                    break
                try:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                except (OSError, RuntimeError):
                    break  # the caller went away
            with contextlib.suppress(OSError, RuntimeError):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            with contextlib.suppress(Exception):
                await body.aclose()
            if watcher is not None:
                watcher.cancel()
