"""The functions runtime service: the runtime engine behind a small internal HTTP interface.

It is started with ``python -m pawabase_server.functions.service`` (the ``functions`` container) and is reached only by the server, on
an internal network. It reads the deployments volume (read-only is enough) and needs Deno; it needs **no database, no Redis and no
secrets of its own**: a function's secrets come from the server when its workers start, every ``ctx.runtime`` call is made by the server
with the invocation's grant, and finished runs are reported to the server, which records them.

Endpoints (each needs a service token addressed to ``functions``, minted with the installation's internal secret):

``POST /v1/call``      an ``fn()`` call: ``{env, branch, function, invocation, grant}`` → the worker's ``done`` message
``POST /v1/http``      an HTTP function request: its description in ``x-pawabase-function`` (base64 JSON), its grant in
                       ``x-pawabase-grant``, its body as the body; the response is the function's, streamed
``POST /v1/warm``      start a function's warm workers
``GET  /v1/status``    workers, pools and per-function statistics
``POST /v1/restart``   drain workers so the next request starts fresh ones
``GET  /health``       liveness, without a token
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from pawabase_server.core.tokens import TokenInvalid, issue_service_token, verify_service_token
from pawabase_server.functions.deployments import Deployments
from pawabase_server.functions.http_functions import HttpFunctionSpec
from pawabase_server.functions.service import grants
from pawabase_server.functions.service.bridge import RemoteBridge
from pawabase_server.functions.service.gateway import GRANT_HEADER, META_HEADER, SERVICE_AUDIENCE
from pawabase_server.functions.service.reports import RunReport
from pawabase_server.functions.service.runtime import FunctionsRuntime, HttpRequest, error_body
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.functions")

SERVICE_HEADER = "x-pawabase-service"
#: How long a function's secrets are trusted before they are fetched again (a request naming a newer version refetches at once).
VARIABLES_SECONDS = 60.0


class ServerVariables:
    """A function's environment variables, fetched from the server and kept per environment."""

    def __init__(self, client: httpx.AsyncClient, token: Any) -> None:
        self.client = client
        self.token = token
        self.cache: dict[str, tuple[float, str, dict[str, str]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def variables(self, env: str, *, version: str | None = None) -> tuple[str, dict[str, str]]:
        cached = self.cache.get(env)
        fresh = cached is not None and time.monotonic() - cached[0] < VARIABLES_SECONDS
        if cached is not None and fresh and (version is None or version == cached[1]):
            return cached[1], cached[2]
        async with self._locks.setdefault(env, asyncio.Lock()):
            cached = self.cache.get(env)
            if cached is not None and (version is not None and version == cached[1]):
                return cached[1], cached[2]
            response = await self.client.get("/internal/v1/functions/variables", params={"env": env}, headers=self.token())
            response.raise_for_status()
            body = response.json()
            self.cache[env] = (time.monotonic(), str(body["version"]), dict(body.get("values") or {}))
            return self.cache[env][1], self.cache[env][2]


async def _read_json(receive: Any) -> Any:
    body = b""
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ConnectionResetError
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    return json.loads(body or b"{}")


async def _send(send: Any, status: int, payload: Any, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    data = json.dumps(payload, default=str).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode()), *(headers or [])],
        }
    )
    await send({"type": "http.response.body", "body": data})


class RuntimeService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings = settings or Settings()
        self.deployments = Deployments(
            settings.deployments_path or Path(settings.code_path) / ".deployments",
            install_requirements=settings.function_install_enabled,
            index_url=settings.function_index_url,
            install_timeout=settings.function_install_timeout,
            binary_only=settings.function_install_binary_only,
            vendor=False,  # vendoring happens where deployments are activated, on the server
        )
        self.api = httpx.AsyncClient(
            base_url=settings.functions_api_url.rstrip("/"),
            timeout=httpx.Timeout(30.0, read=float(settings.functions_max_timeout_seconds)),
            limits=httpx.Limits(max_connections=512, max_keepalive_connections=64),
        )
        self.engine = FunctionsRuntime(
            settings,
            self.deployments,
            variables=ServerVariables(self.api, self._api_token),
            reporter=self._report,
        )
        #: The grant of each HTTP invocation in flight, so its report can be filed under it.
        self.grants: dict[str, str] = {}
        self.started_at = time.time()

    # ── tokens ───────────────────────────────────────────────────────────

    def _api_token(self) -> dict[str, str]:
        token = issue_service_token(self.settings.internal_secret, issuer=SERVICE_AUDIENCE, audience="pawabase", ttl=60)
        return {SERVICE_HEADER: token}

    def _authorized(self, headers: dict[str, str]) -> bool:
        token = headers.get(SERVICE_HEADER)
        if not token:
            return False
        try:
            verify_service_token(token, self.settings.internal_secret, audience=SERVICE_AUDIENCE)
        except TokenInvalid:
            return False
        return True

    def _grant(self, token: str | None, *, env: str, function: str) -> grants.Grant:
        """The grant, checked here too: it must be valid and name what the request names."""
        if not token:
            raise TokenInvalid("missing grant")
        grant = grants.verify(self.settings.internal_secret, token)
        if grant.env != env or grant.function != function:
            raise TokenInvalid("the grant is for another function")
        return grant

    # ── reports ──────────────────────────────────────────────────────────

    async def _report(self, report: RunReport) -> None:
        grant = self.grants.pop(report.invocation, None)
        if grant is None:
            logger.warning("no grant for the run %s of %s; it is not recorded", report.invocation, report.function)
            return
        response = await self.api.post(
            "/internal/v1/functions/report",
            json={"grant": grant, "report": report.as_dict()},
            headers=self._api_token(),
        )
        if response.status_code >= 400:
            logger.warning("the server refused the run report of %s: %s", report.function, response.text[:300])

    # ── ASGI ─────────────────────────────────────────────────────────────

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        path, method = scope.get("path", ""), scope.get("method", "GET")
        if path == "/health":
            await _send(send, 200, {"status": "ok", "service": "functions", "started_at": self.started_at})
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not self._authorized(headers):
            await _send(send, 401, {"error": "unauthorized", "message": "a service token for 'functions' is required"})
            return
        try:
            if path == "/v1/http" and method == "POST":
                await self._http(scope, receive, send, headers)
            elif path == "/v1/call" and method == "POST":
                await self._call(receive, send)
            elif path == "/v1/status" and method == "GET":
                query = dict(item.split("=", 1) for item in scope.get("query_string", b"").decode().split("&") if "=" in item)
                await _send(send, 200, {"service_started_at": self.started_at, **self.engine.status(query.get("env") or None)})
            elif path == "/v1/restart" and method == "POST":
                body = await _read_json(receive)
                await _send(send, 200, await self.engine.restart(body.get("env"), body.get("function")))
            elif path == "/v1/warm" and method == "POST":
                body = await _read_json(receive)
                await self.engine.warm(HttpFunctionSpec.from_wire(body["spec"]))
                await _send(send, 202, {"warming": True})
            else:
                await _send(send, 404, {"error": "not_found", "message": f"{method} {path}"})
        except TokenInvalid as exc:
            await _send(send, 403, {"error": "invalid_grant", "message": str(exc)})
        except ConnectionResetError:
            return

    async def _lifespan(self, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                logger.info(
                    "functions runtime ready: deployments %s, server %s, confinement %s, network %s",
                    self.deployments.root,
                    self.settings.functions_api_url,
                    self.engine.confinement,
                    self.settings.functions_network,
                )
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                with contextlib.suppress(Exception):
                    await self.engine.close()
                with contextlib.suppress(Exception):
                    await self.api.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _call(self, receive: Any, send: Any) -> None:
        body = await _read_json(receive)
        env, branch, function = str(body["env"]), str(body.get("branch") or "main"), str(body["function"])
        grant = self._grant(body.get("grant"), env=env, function=function)
        bridge = RemoteBridge(self.api, body["grant"], token=self._api_token)
        try:
            done = await self.engine.invoke_call(
                env=grant.env, branch=branch, function=function, invocation=body.get("invocation") or {}, bridge=bridge
            )
        except Exception as exc:  # noqa: BLE001 - the server turns this into the caller's error
            failure = getattr(exc, "failure", None)
            done = {
                "outcome": "crashed",
                "kind": getattr(failure, "kind", type(exc).__name__),
                "message": getattr(failure, "message", str(exc)),
                "hint": getattr(failure, "hint", None),
            }
            if getattr(exc, "status", None) == 404:
                done = {"outcome": "missing"}
        await _send(send, 200, done)

    async def _http(self, scope: dict[str, Any], receive: Any, send: Any, headers: dict[str, str]) -> None:
        meta = json.loads(base64.b64decode(headers.get(META_HEADER) or "e30="))
        spec = HttpFunctionSpec.from_wire(meta["spec"])
        token = headers.get(GRANT_HEADER)
        self._grant(token, env=spec.env, function=spec.name)
        stamp = self.deployments.stamp(spec.env, spec.branch) or {}
        if spec.deployment_id and stamp.get("id") != spec.deployment_id:
            # The server activated a deployment this service's volume does not show yet (or no longer shows).
            await _send(
                send,
                503,
                {
                    "error": "deployment_not_here",
                    "message": f"deployment {spec.deployment_id} is not on this runtime yet; try again",
                    "details": {"here": stamp.get("id")},
                },
                headers=[(b"retry-after", b"1")],
            )
            return

        async def body() -> AsyncIterator[bytes]:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    raise ConnectionResetError
                chunk = message.get("body", b"")
                if chunk:
                    yield chunk
                if not message.get("more_body"):
                    return

        request = HttpRequest(
            method=str(meta["method"]),
            url=str(meta["url"]),
            path=str(meta.get("path") or "/"),
            headers=[(str(k), str(v)) for k, v in meta.get("headers") or []],
            body=body() if meta.get("body") else None,
            invocation=str(meta["invocation"]),
            request_id=meta.get("request_id"),
            auth=dict(meta.get("auth") or {}),
            project=str(meta.get("project") or ""),
            client_ip=meta.get("client_ip"),
            variables_version=meta.get("variables_version"),
        )
        self.grants[request.invocation] = token or ""
        bridge = RemoteBridge(self.api, token or "", token=self._api_token)
        reply = await self.engine.serve_http(spec, request, bridge)
        response_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in reply.headers]
        response_headers.append((b"x-pawabase-status", str(reply.status).encode()))
        if reply.worker:
            response_headers.append((b"x-pawabase-runtime-worker", reply.worker.encode()))
        if reply.cold_start:
            response_headers.append((b"x-pawabase-runtime-cold", b"1"))
        await send({"type": "http.response.start", "status": reply.status, "headers": response_headers})
        stream = reply.body
        try:
            async for chunk in stream:
                try:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                except (OSError, RuntimeError):
                    break
            with contextlib.suppress(OSError, RuntimeError):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()


def create_service(settings: Settings | None = None) -> RuntimeService:
    return RuntimeService(settings)


__all__ = ["RuntimeService", "create_service", "error_body"]
