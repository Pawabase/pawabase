"""The functions runtime engine: both worker pools, what a function's workers are started with, and how a run ends.

:class:`FunctionsRuntime` runs inside the server (embedded) or inside the runtime service; the code is the same, only the
:class:`VariableSource` (where a function's secrets come from), the bridge (how ``ctx.runtime`` calls are answered) and the reporter
(where a finished run is recorded) differ.

An HTTP request goes through :meth:`FunctionsRuntime.serve_http`:

1. the function's worker spec: its deployment, its vendored modules, its secrets (as environment variables), its limits;
2. a worker with room (:class:`~.http_pool.HttpPool`), started if needed (a cold start);
3. the request, streamed to the worker; the response's head awaited until the function's ``timeout``;
4. the response, streamed back as it is produced; a caller that goes away, or a function that runs out of time, aborts the request
   (the function's ``ctx.signal`` fires);
5. when the response is over and the work handed to ``waitUntil`` has settled (or run out of time), the run is reported.

Every failure the caller can see has the platform's error shape (``{"error", "message", "details"}``) and the request id.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pawabase_server.functions.http_functions import HttpFunctionSpec
from pawabase_server.functions.limits import RunLimits
from pawabase_server.functions.sandbox.host import Limits, SandboxFailure, SandboxPool, resolve_confinement
from pawabase_server.functions.service.bridge import Bridge
from pawabase_server.functions.service.egress import Budget, Egress
from pawabase_server.functions.service.http_pool import HttpPool, PoolBusy
from pawabase_server.functions.service.http_worker import (
    Exchange,
    HandlerFailed,
    HttpWorker,
    WorkerCrashed,
    WorkerSpec,
)
from pawabase_server.functions.service.reports import RunReport
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.functions")

#: Request headers a function never sees: the platform's own, and what describes one connection.
HIDDEN_REQUEST_HEADERS = frozenset(
    {
        "x-pawabase-context",
        "x-pawabase-service",
        "x-pawabase-grant",
        "x-pawabase-invocation",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "upgrade",
        "proxy-authorization",
    }
)
#: Response headers the platform sets itself.
HIDDEN_RESPONSE_HEADERS = frozenset({"connection", "keep-alive", "transfer-encoding", "content-length"})
#: Built-in variables every HTTP function sees (``Deno.env.get(...)``); a secret cannot replace them.
BUILTINS = (
    "PAWABASE_URL",
    "PAWABASE_ENVIRONMENT",
    "PAWABASE_BRANCH",
    "PAWABASE_FUNCTION",
    "PAWABASE_DEPLOYMENT_ID",
    "PAWABASE_REGION",
)


class VariableSource(Protocol):
    """Where a function's environment variables (its environment's secrets) come from."""

    async def variables(self, env: str, *, version: str | None = None) -> tuple[str, dict[str, str]]:
        """``(version, {name: value})`` of *env*'s secrets. *version* is what the caller already knows is current, if anything."""


def variables_version(values: dict[str, str]) -> str:
    """A short digest of a set of secrets: workers started with other values are replaced when it changes."""
    digest = hashlib.sha256(json.dumps(sorted(values.items())).encode()).hexdigest()
    return digest[:16]


@dataclass
class HttpRequest:
    """A request for an HTTP function, as the gateway accepted it."""

    method: str
    #: The URL the function sees as ``req.url``: the public one, ``…/functions/v1/<name>/<path>?<query>``.
    url: str
    #: What follows ``/functions/v1/<name>``; ``/`` when nothing does.
    path: str
    headers: list[tuple[str, str]]
    body: AsyncIterator[bytes] | None
    invocation: str
    request_id: str | None
    auth: dict[str, Any] = field(default_factory=dict)
    project: str = ""
    client_ip: str | None = None
    #: The secrets version the server knows is current (``None``: ask the source).
    variables_version: str | None = None
    #: The limits this request runs under (resolved by the server); ``None`` takes the function's config and the installation's.
    limits: RunLimits | None = None


@dataclass
class HttpReply:
    status: int
    headers: list[tuple[str, str]]
    body: AsyncIterator[bytes]
    worker: str | None = None
    cold_start: bool = False


def error_body(code: str, message: str, details: Any = None) -> bytes:
    return json.dumps({"error": code, "message": message, "details": details}).encode()


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data


def error_reply(status: int, code: str, message: str, details: Any = None, *, retry_after: int | None = None) -> HttpReply:
    headers = [("content-type", "application/json")]
    if retry_after is not None:
        headers.append(("retry-after", str(retry_after)))
    return HttpReply(status=status, headers=headers, body=_once(error_body(code, message, details)))


class FunctionsRuntime:
    """Runs ``fn()`` calls and HTTP requests for the deployments in *deployments*."""

    def __init__(
        self,
        settings: Settings,
        deployments: Any,
        *,
        variables: VariableSource,
        reporter: Callable[[RunReport], Awaitable[None]],
        sandboxes: SandboxPool | None = None,
    ) -> None:
        self.settings = settings
        self.deployments = deployments
        self.variables = variables
        self.reporter = reporter
        self.confinement = resolve_confinement(settings.sandbox_confinement)
        self.sandboxes = sandboxes or SandboxPool(
            Limits(
                memory_mb=settings.sandbox_memory_mb,
                workers=settings.sandbox_workers,
                idle_seconds=settings.sandbox_idle_seconds,
                confinement=self.confinement,
            )
        )
        self.egress = Egress(
            allow_hosts=[h.strip() for h in settings.outbound_allow_hosts.split(",") if h.strip()]
        )
        self.http = HttpPool(
            egress=self.egress,
            max_workers=settings.functions_max_workers,
            queue_seconds=settings.functions_queue_seconds,
            idle_seconds=settings.sandbox_idle_seconds,
        )
        self._finishing: set[asyncio.Task] = set()
        self.started_at = time.time()

    # ── fn() functions ───────────────────────────────────────────────────

    async def invoke_call(
        self,
        *,
        env: str,
        branch: str,
        function: str,
        invocation: dict[str, Any],
        bridge: Bridge,
        limits: RunLimits | None = None,
    ) -> dict[str, Any]:
        """Run one ``fn()`` call under *limits* and return the worker's ``done`` message (with ``usage``)."""
        return await self.sandboxes.run_raw(
            self.deployments,
            env=env,
            branch=branch,
            function=function,
            invocation=invocation,
            bridge=bridge,
            limits=limits,
        )

    # ── HTTP functions ───────────────────────────────────────────────────

    def builtins(self, spec: HttpFunctionSpec) -> dict[str, str]:
        return {
            "PAWABASE_URL": self.settings.public_url.rstrip("/"),
            "PAWABASE_ENVIRONMENT": spec.env,
            "PAWABASE_BRANCH": spec.branch,
            "PAWABASE_FUNCTION": spec.name,
            "PAWABASE_DEPLOYMENT_ID": spec.deployment_id or "",
            "PAWABASE_REGION": getattr(self.settings, "region", "") or "",
        }

    async def worker_spec(
        self, spec: HttpFunctionSpec, *, variables_version: str | None = None, limits: RunLimits | None = None
    ) -> WorkerSpec:
        version, secrets = await self.variables.variables(spec.env, version=variables_version)
        values = {name: value for name, value in secrets.items() if name not in BUILTINS}
        values.update(self.builtins(spec))
        config = spec.config
        return WorkerSpec(
            env=spec.env,
            branch=spec.branch,
            function=spec.name,
            entry=spec.entry,
            artifact=self.deployments.current(spec.env, spec.branch),
            deployment_id=spec.deployment_id,
            vendor_cache=self.deployments.vendor_cache(spec.env, spec.branch),
            memory_mb=limits.memory if limits is not None else self.settings.function_default_memory_mb,
            max_workers=limits.workers if limits is not None else self.settings.functions_max_workers,
            stall_seconds=limits.cpu if limits is not None else 0.0,
            concurrency=config.concurrency or self.settings.functions_worker_concurrency,
            network=self.settings.functions_network,
            confinement=self.confinement,
            variables=tuple(sorted(values.items())),
            variables_version=version,
        )

    async def warm(self, spec: HttpFunctionSpec) -> None:
        """Start the warm workers a function's ``config.warm`` asks for (called when a deployment is first seen)."""
        if spec.config.warm > 0:
            self.http.keep_warm(await self.worker_spec(spec), spec.config.warm)

    def _timeout(self, spec: HttpFunctionSpec) -> float:
        return float(min(spec.config.timeout, self.settings.functions_max_timeout_seconds))

    async def serve_http(self, spec: HttpFunctionSpec, request: HttpRequest, bridge: Bridge) -> HttpReply:
        """Answer *request* with *spec*'s function. The reply's body must be iterated (or closed) by the caller."""
        started = time.monotonic()
        limits = request.limits
        timeout = float(limits.timeout) if limits is not None else self._timeout(spec)
        deadline = started + timeout
        report = RunReport(
            invocation=request.invocation,
            env=spec.env,
            branch=spec.branch,
            function=spec.name,
            deployment_id=spec.deployment_id,
            method=request.method,
            path=request.path,
            request_id=request.request_id,
            status="failed",
            status_code=None,
            duration_ms=0.0,
            limits=limits.as_dict() if limits is not None else None,
        )
        try:
            worker_spec = await self.worker_spec(spec, variables_version=request.variables_version, limits=limits)
            if spec.config.warm:
                self.http.keep_warm(worker_spec, spec.config.warm)
            worker, cold = await self.http.acquire(worker_spec)
        except PoolBusy as exc:
            return await self._refused(report, started, bridge, 503, "function_busy", str(exc), retry_after=1)
        except SandboxFailure as exc:
            return await self._refused(
                report,
                started,
                bridge,
                500,
                "function_unavailable",
                exc.failure.summary,
                details={"kind": exc.failure.kind, "hint": exc.failure.hint},
            )
        report.cold_start, report.worker = cold, worker.id
        group = HttpPool.group_of(worker_spec)
        if cold:
            self.http.stats_for(group).cold_starts += 1
        headers = [(k, v) for k, v in request.headers if k.lower() not in HIDDEN_REQUEST_HEADERS]
        context = {
            "auth": request.auth,
            "request_id": request.request_id,
            "invocation": request.invocation,
            "project": request.project,
            "client_ip": request.client_ip,
        }
        budget = Budget(self.settings.functions_fetch_limit, self.settings.functions_fetch_max_bytes)
        try:
            exchange = await worker.open(
                method=request.method,
                url=request.url,
                path=request.path,
                headers=headers,
                body=request.body,
                context=context,
                bridge=bridge,
                budget=budget,
            )
        except WorkerCrashed as exc:
            return await self._refused(report, started, bridge, 502, "function_crashed", str(exc))
        try:
            status, response_headers = await asyncio.wait_for(
                asyncio.shield(exchange.head), max(0.0, deadline - time.monotonic())
            )
        except TimeoutError:
            await worker.abort(exchange, f"the function did not answer within {timeout:g}s")
            self.http.stats_for(group).timeouts += 1
            report.limit_hit = "timeout"
            self._complete(report, exchange, worker, started, bridge, budget, error=f"timed out after {timeout:g}s")
            return error_reply(504, "function_timeout", f"The function did not answer within {timeout:g} seconds.")
        except HandlerFailed as exc:
            self._complete(report, exchange, worker, started, bridge, budget, error=exc.failure.line())
            return error_reply(
                500,
                "function_error",
                "The function failed before it answered."
                + (f" Look up request {request.request_id} in Studio." if request.request_id else ""),
                {"kind": exc.failure.kind},
            )
        except WorkerCrashed as exc:
            self._complete(report, exchange, worker, started, bridge, budget, error=str(exc))
            if worker.limit_hit:
                report.limit_hit = worker.limit_hit
                return error_reply(
                    500, f"{worker.limit_hit}_limit_exceeded", str(exc), {"limit": worker.limit_hit}
                )
            return error_reply(502, "function_crashed", str(exc))
        report.status_code = status
        cleaned = [(k, v) for k, v in response_headers if k.lower() not in HIDDEN_RESPONSE_HEADERS]
        return HttpReply(
            status=status,
            headers=cleaned,
            body=self._stream(report, exchange, worker, started, deadline, timeout, bridge, budget),
            worker=worker.id,
            cold_start=cold,
        )

    async def _stream(
        self,
        report: RunReport,
        exchange: Exchange,
        worker: HttpWorker,
        started: float,
        deadline: float,
        timeout: float,
        bridge: Bridge,
        budget: Budget,
    ) -> AsyncIterator[bytes]:
        """The response body, until the function ends it, the time runs out, or the caller stops reading."""
        error: str | None = None
        body = worker.body(exchange)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                try:
                    chunk = await asyncio.wait_for(body.__anext__(), remaining)
                except StopAsyncIteration:
                    return
                yield chunk
        except TimeoutError:
            error = f"the response was still streaming after {timeout:g}s and was cut off"
            report.limit_hit = "timeout"
            await worker.abort(exchange, error)
            self.http.stats_for(HttpPool.group_of(worker.spec)).timeouts += 1
        except HandlerFailed as exc:
            error = exc.failure.line()
        except WorkerCrashed as exc:
            error = str(exc)
            report.limit_hit = report.limit_hit or worker.limit_hit
        except (asyncio.CancelledError, GeneratorExit):
            if not exchange.finished.is_set():
                await worker.abort(exchange, "the caller went away")
                report.status = "cancelled"
            raise
        finally:
            with contextlib.suppress(Exception):
                await body.aclose()
            self._complete(report, exchange, worker, started, bridge, budget, error=error)

    async def _refused(
        self,
        report: RunReport,
        started: float,
        bridge: Bridge,
        status: int,
        code: str,
        message: str,
        *,
        details: Any = None,
        retry_after: int | None = None,
    ) -> HttpReply:
        report.status, report.status_code, report.error = "failed", status, message
        report.duration_ms = (time.monotonic() - started) * 1000
        with contextlib.suppress(Exception):
            await bridge.close()
        await self._report(report)
        return error_reply(status, code, message, details, retry_after=retry_after)

    def _complete(
        self,
        report: RunReport,
        exchange: Exchange,
        worker: HttpWorker,
        started: float,
        bridge: Bridge,
        budget: Budget,
        *,
        error: str | None,
    ) -> None:
        """The response is over: wait for its ``waitUntil`` work in the background, then report the run."""
        if report.duration_ms:
            return  # already completed (a finally after an explicit completion)
        report.duration_ms = (time.monotonic() - started) * 1000
        if error:
            report.error = error
        group = HttpPool.group_of(worker.spec)
        self.http.notify(group)
        task = asyncio.get_running_loop().create_task(
            self._finish(report, exchange, worker, bridge, budget)
        )
        self._finishing.add(task)
        task.add_done_callback(self._finishing.discard)

    async def _finish(
        self, report: RunReport, exchange: Exchange, worker: HttpWorker, bridge: Bridge, budget: Budget
    ) -> None:
        limit = float(self.settings.functions_background_seconds)
        if not exchange.background_done.is_set():
            try:
                await asyncio.wait_for(exchange.background_done.wait(), limit)
            except TimeoutError:
                await worker.abort(exchange, f"waitUntil work ran past {limit:g}s")
                exchange.logs.append(
                    {"level": "warning", "message": f"waitUntil work was still running after {limit:g}s and was stopped", "at": time.time()}
                )
                exchange.background_done.set()
        if exchange.error is not None and report.error is None:
            report.error = exchange.error.line()
        if report.status != "cancelled":
            failed = bool(report.error) or (report.status_code or 500) >= 500
            report.status = "failed" if failed else "succeeded"
        report.logs = list(exchange.logs)
        report.request_bytes = exchange.request_bytes
        report.response_bytes = exchange.response_bytes
        report.background_ms = exchange.background_ms
        report.fetches = budget.used_requests
        report.cpu_ms = exchange.cpu_ms
        report.memory_peak_mb = round(exchange.memory_peak_mb, 2) if exchange.memory_peak_mb is not None else None
        report.limit_hit = report.limit_hit or (worker.limit_hit if exchange.error is not None else None)
        self.http.stats_for(HttpPool.group_of(worker.spec)).record(
            report.duration_ms, failed=report.status == "failed", error=report.error
        )
        with contextlib.suppress(Exception):
            await bridge.close()
        await self._report(report)

    async def _report(self, report: RunReport) -> None:
        try:
            await self.reporter(report)
        except Exception:  # noqa: BLE001 - a lost run record must not fail anything
            logger.exception("could not report the run of %s/%s", report.env, report.function)

    # ── operating ────────────────────────────────────────────────────────

    def status(self, env: str | None = None) -> dict[str, Any]:
        rpc = [
            {
                "env": worker.key[0],
                "branch": worker.key[1],
                "deployment_id": worker.key[2],
                "language": worker.language,
                "alive": worker.alive,
                "functions": sorted(worker.functions),
                "pid": worker.process.pid if worker.process else None,
            }
            for worker in list(self.sandboxes.all)
            if env is None or worker.key[0] == env
        ]
        return {
            "started_at": self.started_at,
            "confinement": self.confinement,
            "network": self.settings.functions_network,
            "limits": {
                "max_workers": self.settings.functions_max_workers,
                "worker_concurrency": self.settings.functions_worker_concurrency,
                "max_timeout_seconds": self.settings.functions_max_timeout_seconds,
                "background_seconds": self.settings.functions_background_seconds,
                "memory_mb": self.settings.functions_memory_mb,
                "fetch_limit": self.settings.functions_fetch_limit,
            },
            "http": self.http.describe(env),
            "calls": {"workers": rpc, "started": self.sandboxes.started},
        }

    async def restart(self, env: str | None = None, function: str | None = None) -> dict[str, int]:
        http = await self.http.restart(env, function)
        calls = 0
        if function is None:
            for worker in list(self.sandboxes.all):
                if env is None or worker.key[0] == env:
                    await self.sandboxes._drop(worker)  # noqa: SLF001 - the pool's own way to stop a worker
                    calls += 1
            for key in [k for k in self.sandboxes.idle if env is None or k[0] == env]:
                self.sandboxes.idle.pop(key, None)
        return {"http": http, "calls": calls}

    async def close(self) -> None:
        for task in list(self._finishing):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(task, 5)
        await self.http.close()
        await self.sandboxes.close()
        await self.egress.close()
