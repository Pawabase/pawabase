"""The server's side of the functions runtime: one interface, whether the runtime runs in this process or as its own service.

The server decides **whether** a function may run (key, policy, scopes, quotas, metering) and signs a grant for the invocation. It then
hands the invocation to its :class:`FunctionsGateway`:

:class:`EmbeddedFunctions`
    the runtime engine in this process; ``ctx.runtime`` calls are answered by the invocation's own ``ApiRuntime``, runs are recorded
    directly. What a single-process installation (and the tests) use.

:class:`RemoteFunctions`
    the runtime service at ``PAWABASE_FUNCTIONS_RUNTIME_URL``; the invocation travels with its grant, the service calls back into
    ``/internal/v1/functions/*`` for ``ctx.runtime`` and to report runs (:mod:`pawabase_server.routes.data.functions_internal`).
"""

from __future__ import annotations

import base64
import contextlib
import json
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from pawabase_server.core.tokens import issue_service_token
from pawabase_server.functions.http_functions import HttpFunctionSpec
from pawabase_server.functions.sandbox.host import SandboxPool, invocation_of
from pawabase_server.functions.service import grants
from pawabase_server.functions.service.bridge import LocalBridge
from pawabase_server.functions.service.reports import RunReport
from pawabase_server.functions.service.runtime import (
    FunctionsRuntime,
    HttpReply,
    HttpRequest,
    error_reply,
    variables_version,
)

if TYPE_CHECKING:
    from pawabase_server.functions.runtime import ApiRuntime
    from pawabase_server.platform import Platform

SERVICE_AUDIENCE = "functions"
#: The header carrying an HTTP invocation's description to the runtime service (base64 JSON; the body is the request's own).
META_HEADER = "x-pawabase-function"
GRANT_HEADER = "x-pawabase-grant"


class FunctionsGateway(Protocol):
    async def call(
        self,
        *,
        env: str,
        branch: str,
        function: str,
        context: Any,
        runtime: ApiRuntime,
        grant: grants.Grant,
        limits: Any = None,
        usage: dict[str, Any] | None = None,
    ) -> Any: ...

    async def http(
        self, spec: HttpFunctionSpec, request: HttpRequest, *, runtime: ApiRuntime, grant: grants.Grant
    ) -> HttpReply: ...

    async def warm(self, spec: HttpFunctionSpec) -> None: ...

    async def status(self, env: str | None = None) -> dict[str, Any]: ...

    async def restart(self, env: str | None = None, function: str | None = None) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class PlatformVariables:
    """A function's environment variables, from the environment's secrets as this server holds them."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    async def variables(self, env: str, *, version: str | None = None) -> tuple[str, dict[str, str]]:
        state = await self.platform.envs.get(env)
        values = dict(state.secret_values)
        return variables_version(values), values


class EmbeddedFunctions:
    """The runtime engine, in this process."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.engine = FunctionsRuntime(
            platform.settings,
            platform.deployments,
            variables=PlatformVariables(platform),
            reporter=self._record,
            sandboxes=platform.sandboxes,
        )

    async def _record(self, report: RunReport) -> None:
        await self.platform.records.add(report.to_record())

    async def call(self, *, env, branch, function, context, runtime, grant, limits=None, usage=None):
        bridge = LocalBridge(runtime, self.platform.sandbox_transactions)
        return await self.engine.sandboxes.invoke(
            self.platform.deployments,
            env=env,
            branch=branch,
            function=function,
            context=context,
            bridge=bridge,
            limits=limits,
            usage=usage,
        )

    async def http(self, spec, request, *, runtime, grant):
        return await self.engine.serve_http(spec, request, LocalBridge(runtime, self.platform.sandbox_transactions))

    async def warm(self, spec):
        await self.engine.warm(spec)

    async def status(self, env=None):
        return {"mode": "embedded", **self.engine.status(env)}

    async def restart(self, env=None, function=None):
        return await self.engine.restart(env, function)

    async def close(self):
        await self.engine.close()


class RemoteFunctions:
    """The runtime service, over HTTP. Every request carries a service token addressed to it and the invocation's grant."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        settings = platform.settings
        self.url = settings.functions_runtime_url.rstrip("/")
        self.client = httpx.AsyncClient(
            base_url=self.url,
            timeout=httpx.Timeout(10.0, read=float(settings.functions_max_timeout_seconds) + 30.0),
            limits=httpx.Limits(max_connections=1024, max_keepalive_connections=128),
        )

    def _token(self) -> dict[str, str]:
        token = issue_service_token(
            self.platform.settings.internal_secret, issuer="pawabase", audience=SERVICE_AUDIENCE, ttl=60
        )
        return {"x-pawabase-service": token}

    def _grant(self, grant: grants.Grant, seconds: float) -> str:
        return grants.issue(self.platform.settings.internal_secret, grant, seconds=seconds)

    async def call(self, *, env, branch, function, context, runtime, grant, limits=None, usage=None):
        spec = self.platform.function_spec(env, function, branch)
        seconds = float(limits.timeout if limits is not None else (getattr(spec, "timeout", 30) or 30))
        try:
            response = await self.client.post(
                "/v1/call",
                json={
                    "env": env,
                    "branch": branch,
                    "function": function,
                    "invocation": invocation_of(context, function),
                    "grant": self._grant(grant, seconds),
                    "limits": limits.as_dict() if limits is not None else None,
                },
                headers=self._token(),
                timeout=httpx.Timeout(10.0, read=seconds + 30.0),
            )
        except httpx.HTTPError as exc:
            raise RuntimeServiceUnavailable(str(exc)) from exc
        if response.status_code >= 400:
            raise RuntimeServiceUnavailable(f"the functions runtime answered {response.status_code}: {response.text[:300]}")
        return SandboxPool.finish(response.json(), context, usage)

    async def http(self, spec, request, *, runtime, grant):
        seconds = float(spec.config.timeout)
        meta = {
            "spec": spec.wire(),
            "method": request.method,
            "url": request.url,
            "path": request.path,
            "headers": request.headers,
            "invocation": request.invocation,
            "request_id": request.request_id,
            "auth": request.auth,
            "project": request.project,
            "client_ip": request.client_ip,
            "variables_version": request.variables_version,
            "limits": request.limits.as_dict() if request.limits is not None else None,
            "body": request.body is not None,
        }
        headers = {
            **self._token(),
            META_HEADER: base64.b64encode(json.dumps(meta, default=str).encode()).decode(),
            GRANT_HEADER: self._grant(grant, seconds + self.platform.settings.functions_background_seconds),
        }
        upstream = self.client.build_request(
            "POST",
            "/v1/http",
            headers=headers,
            content=request.body if request.body is not None else b"",
            timeout=httpx.Timeout(10.0, read=seconds + 30.0),
        )
        try:
            response = await self.client.send(upstream, stream=True)
        except httpx.HTTPError as exc:
            return error_reply(503, "runtime_unavailable", f"The functions runtime could not be reached: {exc}", retry_after=2)

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()

        status = int(response.headers.get("x-pawabase-status") or response.status_code)
        passed = [
            (k.decode("latin-1"), v.decode("latin-1"))
            for k, v in response.headers.raw
            if not k.decode("latin-1").lower().startswith("x-pawabase-runtime")
            and k.decode("latin-1").lower() not in ("x-pawabase-status", "content-length", "transfer-encoding", "connection", "date", "server")
        ]
        return HttpReply(
            status=status,
            headers=passed,
            body=body(),
            worker=response.headers.get("x-pawabase-runtime-worker"),
            cold_start=response.headers.get("x-pawabase-runtime-cold") == "1",
        )

    async def warm(self, spec):
        with contextlib.suppress(httpx.HTTPError):
            await self.client.post("/v1/warm", json={"spec": spec.wire()}, headers=self._token())

    async def status(self, env=None):
        started = time.monotonic()
        try:
            response = await self.client.get("/v1/status", params={"env": env} if env else None, headers=self._token())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return {"mode": "service", "url": self.url, "reachable": False, "error": str(exc)}
        return {
            "mode": "service",
            "url": self.url,
            "reachable": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            **response.json(),
        }

    async def restart(self, env=None, function=None):
        response = await self.client.post("/v1/restart", json={"env": env, "function": function}, headers=self._token())
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self.client.aclose()


class RuntimeServiceUnavailable(RuntimeError):
    """The runtime service could not run an ``fn()`` call (unreachable, or it refused the request)."""


def gateway_for(platform: Platform) -> FunctionsGateway:
    return RemoteFunctions(platform) if platform.settings.functions_runtime_remote else EmbeddedFunctions(platform)
