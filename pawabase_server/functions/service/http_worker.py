"""One HTTP function's worker: a confined Deno process (``deno/http_worker.ts``) answering many requests at once.

The worker has no socket. Each request is an :class:`Exchange`: a ``request`` message, the body as ``body`` messages as it arrives, then
the response coming back as ``head``, ``chunk``… ``end``. While it runs, the function's ``ctx.runtime`` calls and ``fetch()``es arrive
as messages too and are answered here — the runtime calls through the invocation's :class:`~.bridge.Bridge`, the fetches through the
guarded :class:`~.egress.Egress` — so everything that leaves the function goes through the platform.

A worker that exits while requests are in flight fails those requests (``WorkerCrashed``) and is never used again; the pool starts
another for the next request.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import secrets
import shutil
import signal
import sys
import tempfile
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pawabase_server.core.failures import Failure, Where
from pawabase_server.functions.sandbox import typescript
from pawabase_server.functions.sandbox.host import SandboxFailure
from pawabase_server.functions.service.bridge import Bridge, failure_reply
from pawabase_server.functions.service import watchdog
from pawabase_server.functions.service.egress import Budget, Egress, EgressRefused

logger = logging.getLogger("pawabase.functions")

API_ROOT = Path(__file__).resolve().parents[3]
MAX_MESSAGE = 64 * 1024 * 1024
CHUNK = 64 * 1024
STDERR_LINES = 40


class WorkerCrashed(Exception):
    """The worker exited while a request was in flight."""


class HandlerFailed(Exception):
    """The function's handler threw (or returned something that is not a ``Response``)."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.summary)
        self.failure = failure


@dataclass(frozen=True)
class WorkerSpec:
    """Everything a worker is started with. Workers are reused only for requests whose spec is the same."""

    env: str
    branch: str
    function: str
    entry: str
    artifact: Path
    deployment_id: str | None
    vendor_cache: Path | None
    memory_mb: int
    concurrency: int
    network: str
    confinement: str
    #: The environment variables the function reads with ``Deno.env``: its environment's secrets and a few built-ins.
    variables: tuple[tuple[str, str], ...] = ()
    #: Changes when the secrets do, so workers started with old values are replaced.
    variables_version: str = ""
    start_seconds: float = 30.0
    #: The longest the worker may compute without yielding while it has requests (the project's ``cpu`` limit); ``0``: not watched.
    stall_seconds: float = 0.0
    #: Workers this function may run at once (the project's ``max_function_workers``); not part of the key: it changes no worker.
    max_workers: int = 0

    @property
    def key(self) -> tuple[Any, ...]:
        # Memory and the stall limit are applied when the process starts: a changed limit needs new workers.
        return (self.env, self.branch, self.function, self.deployment_id, self.variables_version, self.memory_mb, self.stall_seconds)


@dataclass
class Exchange:
    """One request on a worker, from the moment it is sent until its response and its ``waitUntil`` work are over."""

    id: str
    method: str
    path: str
    bridge: Bridge
    budget: Budget
    started: float = field(default_factory=time.monotonic)
    head: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    chunks: asyncio.Queue = field(default_factory=asyncio.Queue)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    background_done: asyncio.Event = field(default_factory=asyncio.Event)
    logs: list[dict[str, Any]] = field(default_factory=list)
    background: int = 0
    background_started: float | None = None
    background_ms: float | None = None
    request_bytes: int = 0
    response_bytes: int = 0
    status: int | None = None
    error: Failure | None = None
    aborted: str | None = None
    #: CPU time the worker used while this was its only request (``None`` when it shared the worker, or where it cannot be measured).
    cpu_ms: float | None = None
    memory_peak_mb: float | None = None
    shared: bool = False
    cpu_start: float | None = None

    def __post_init__(self) -> None:
        self.background_done.set()  # nothing handed to waitUntil yet


class HttpWorker:
    def __init__(self, spec: WorkerSpec, *, egress: Egress, on_exit: Callable[[HttpWorker], None] | None = None) -> None:
        self.spec = spec
        self.id = secrets.token_hex(4)
        self.egress = egress
        self.on_exit = on_exit
        self.process: asyncio.subprocess.Process | None = None
        self.exchanges: dict[str, Exchange] = {}
        self.stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self.started_at = time.time()
        self.last_used = time.monotonic()
        self.served = 0
        self.failed = 0
        self.draining = False
        self.ready_info: dict[str, Any] = {}
        self.memory: dict[str, int] = {}
        self._tmp = Path(tempfile.mkdtemp(prefix="pawabase-http-"))
        self._tasks: set[asyncio.Task] = set()
        self._fetches: dict[int, dict[str, Any]] = {}
        self._write_lock = asyncio.Lock()
        self._reader: asyncio.Task | None = None
        self._drain: asyncio.Task | None = None
        self._monitor: asyncio.Task | None = None
        self._dead = False
        self.last_heard = time.monotonic()
        #: The limit that stopped this worker (``memory`` or ``cpu``), and why.
        self.limit_hit: str | None = None
        self.limit_reason: str | None = None

    # ── state ────────────────────────────────────────────────────────────

    @property
    def alive(self) -> bool:
        return not self._dead and self.process is not None and self.process.returncode is None

    @property
    def active(self) -> int:
        """Requests whose response is not finished."""
        return sum(1 for ex in self.exchanges.values() if not ex.finished.is_set())

    @property
    def busy(self) -> bool:
        """Whether anything still runs here: a response, or work handed to ``waitUntil``."""
        return any(not ex.finished.is_set() or not ex.background_done.is_set() for ex in self.exchanges.values())

    @property
    def has_room(self) -> bool:
        return self.alive and not self.draining and self.active < self.spec.concurrency

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "function": self.spec.function,
            "env": self.spec.env,
            "branch": self.spec.branch,
            "deployment_id": self.spec.deployment_id,
            "pid": self.process.pid if self.process else None,
            "alive": self.alive,
            "draining": self.draining,
            "active": self.active,
            "background": sum(ex.background for ex in self.exchanges.values()),
            "served": self.served,
            "failed": self.failed,
            "concurrency": self.spec.concurrency,
            "memory_mb": self.spec.memory_mb,
            "heap_bytes": self.memory.get("heap"),
            "rss_bytes": self.memory.get("rss"),
            "started_at": self.started_at,
            "idle_seconds": round(time.monotonic() - self.last_used, 1),
            "runtime": self.ready_info.get("runtime") or {},
        }

    # ── starting and stopping ────────────────────────────────────────────

    def _environment(self) -> dict[str, str]:
        """The function's variables, then what the launcher needs. Nothing of the runtime's own environment."""
        base = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            "TMPDIR": str(self._tmp),
            "HOME": str(self._tmp),
        }
        if os.environ.get("PAWABASE_DENO_PATH"):
            base["PAWABASE_DENO_PATH"] = os.environ["PAWABASE_DENO_PATH"]
        return {**dict(self.spec.variables), **base}

    async def start(self) -> None:
        spec = self.spec
        command = typescript.launcher(
            "http",
            spec.artifact,
            self._tmp,
            memory_mb=spec.memory_mb,
            confinement=spec.confinement,
            function=spec.function,
            cache=spec.vendor_cache,
            network=spec.network,
        )
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(API_ROOT),
            env=self._environment(),
            limit=MAX_MESSAGE,
            start_new_session=True,  # its own process group: nothing it signals reaches the runtime
        )
        self._drain = asyncio.create_task(self._drain_stderr())
        await self._send(
            {
                "t": "init",
                "artifact": str(spec.artifact),
                "function": spec.function,
                "entry": spec.entry,
                "env": spec.env,
                "branch": spec.branch,
                "deployment": spec.deployment_id,
                "network": spec.network,
            }
        )
        try:
            ready = await asyncio.wait_for(self._await_ready(), spec.start_seconds)
        except (TimeoutError, EOFError) as exc:
            await self.kill()
            tail = f": {self.stderr[-1]}" if self.stderr else ""
            raise SandboxFailure(
                Failure(
                    "SandboxStartFailed",
                    f"the function's worker did not start{tail}",
                    hint="Check that the function imports without errors (Studio → Functions shows its load errors).",
                )
            ) from exc
        if ready.get("unconfined"):
            await self.kill()
            raise SandboxFailure(
                Failure(
                    "SandboxConfinementUnavailable",
                    f"the function process could not be confined: {ready['unconfined']}",
                    hint="Functions run only confined on this server (PAWABASE_SANDBOX_CONFINEMENT). Use a Linux kernel with "
                    "Landlock (5.13+) and seccomp, or set PAWABASE_SANDBOX_CONFINEMENT=off for code you trust.",
                )
            )
        errors = list(ready.get("errors") or [])
        if errors:
            await self.kill()
            raise SandboxFailure(
                Failure("DeploymentDidNotLoad", f"the function did not load: {errors[0]}", hint="Fix it and deploy again.")
            )
        self.ready_info = ready
        self.last_heard = time.monotonic()
        self._reader = asyncio.create_task(self._read_loop())
        if watchdog.supported() or self.spec.stall_seconds:
            self._monitor = asyncio.create_task(self._watch())

    async def _await_ready(self) -> dict[str, Any]:
        while True:
            message = await self._read()
            if message.get("t") == "ready":
                return message
            if message.get("t") == "log":
                logger.info("[%s %s] %s", self.spec.env, self.spec.function, str(message.get("message"))[:500])

    async def stop(self) -> None:
        """Ask the worker to exit; kill it if it does not."""
        if self.alive:
            with contextlib.suppress(Exception):
                await self._send({"t": "stop"})
            with contextlib.suppress(Exception):
                assert self.process is not None
                await asyncio.wait_for(self.process.wait(), 3)
        await self.kill()

    async def kill(self) -> None:
        self._dead = True
        if self.process is not None and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.process.wait(), 5)
        for task in list(self._tasks):
            task.cancel()
        for state in list(self._fetches.values()):
            response = state.get("response")
            if response is not None:
                with contextlib.suppress(Exception):
                    await response.close()
        self._fetches.clear()
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
        if self._drain is not None:
            self._drain.cancel()
        if self._monitor is not None and self._monitor is not asyncio.current_task():
            self._monitor.cancel()
        self._fail_all(WorkerCrashed(self.limit_reason or "the worker was stopped"))
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── the pipes ────────────────────────────────────────────────────────

    async def _send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or (self._dead and message.get("t") != "stop"):
            raise WorkerCrashed("the worker is not running")
        data = (json.dumps(message, default=str, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        line = await self.process.stdout.readline()
        if not line:
            raise EOFError
        return json.loads(line)

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        async for raw in self.process.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                self.stderr.append(line)
                logger.info("[%s %s stderr] %s", self.spec.env, self.spec.function, line[:500])

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read_loop(self) -> None:
        try:
            while True:
                message = await self._read()
                self.last_heard = time.monotonic()
                self._dispatch(message)
        except (EOFError, asyncio.IncompleteReadError, ValueError):
            pass
        except asyncio.CancelledError:
            return
        code = self.process.returncode if self.process else None
        if self.process is not None and code is None:
            with contextlib.suppress(Exception):
                code = await asyncio.wait_for(self.process.wait(), 2)
        reason = self._exit_reason(code)
        if self.exchanges:
            logger.warning("worker %s of %s/%s exited: %s", self.id, self.spec.env, self.spec.function, reason)
        self._dead = True
        self._fail_all(WorkerCrashed(reason))
        if self.on_exit is not None:
            self.on_exit(self)
        await self.kill()

    def _exit_reason(self, code: int | None) -> str:
        if self.limit_reason:
            return self.limit_reason
        tail = f" ({self.stderr[-1][:200]})" if self.stderr else ""
        if code is not None and code < 0 and -code == signal.SIGKILL:
            return f"the worker was stopped (it may have run out of memory: the limit is {self.spec.memory_mb} MB){tail}"
        if self.memory.get("heap") and self.memory["heap"] > self.spec.memory_mb * 1024 * 1024 * 0.9:
            return f"the worker ran out of memory (the limit is {self.spec.memory_mb} MB){tail}"
        return f"the worker exited (code {code}){tail}"

    def _fail_all(self, error: Exception) -> None:
        for ex in list(self.exchanges.values()):
            if not ex.head.done():
                ex.head.set_exception(error)
            elif not ex.finished.is_set():
                ex.chunks.put_nowait(error)
            if ex.error is None and not ex.finished.is_set():
                ex.error = Failure("WorkerCrashed", str(error))
            ex.finished.set()
            ex.background = 0
            ex.background_done.set()
        self.exchanges.clear()

    # ── limits ───────────────────────────────────────────────────────────

    async def _watch(self) -> None:
        """Stop the worker when it holds more memory than its limit, or computes past its stall limit while serving."""
        stall = self.spec.stall_seconds
        try:
            while not self._dead:
                await asyncio.sleep(0.25)
                usage = watchdog.read(self.process.pid) if self.process is not None else None
                if usage is not None:
                    for ex in self.exchanges.values():
                        if not ex.finished.is_set() or not ex.background_done.is_set():
                            ex.memory_peak_mb = max(ex.memory_peak_mb or 0.0, usage.rss_mb)
                    if self.spec.memory_mb and usage.rss_mb > self.spec.memory_mb:
                        await self._stop_for(
                            "memory", f"the function's worker used more than its {self.spec.memory_mb} MB memory limit"
                        )
                        return
                busy = any(not ex.finished.is_set() for ex in self.exchanges.values())
                # The worker reports every second; silence while it has requests means its event loop is blocked.
                if stall and busy and time.monotonic() - self.last_heard > max(stall, 1.5):
                    await self._stop_for(
                        "cpu", f"the function computed for more than {stall:g}s without yielding (its cpu limit)"
                    )
                    return
        except asyncio.CancelledError:
            return

    async def _stop_for(self, limit: str, reason: str) -> None:
        logger.warning("worker %s of %s/%s stopped: %s", self.id, self.spec.env, self.spec.function, reason)
        self.limit_hit, self.limit_reason = limit, reason
        for ex in self.exchanges.values():
            if ex.error is None and (not ex.finished.is_set() or not ex.background_done.is_set()):
                ex.error = Failure(
                    {"cpu": "CpuLimitExceeded", "memory": "MemoryLimitExceeded"}.get(limit, "LimitExceeded"), reason
                )
        await self.kill()

    def _usage_start(self, ex: Exchange) -> None:
        active = [other for other in self.exchanges.values() if other is not ex and not other.finished.is_set()]
        for other in active:
            other.shared = True
        ex.shared = bool(active)
        usage = watchdog.read(self.process.pid) if self.process is not None else None
        ex.cpu_start = usage.cpu_seconds if usage else None
        if usage:
            ex.memory_peak_mb = usage.rss_mb

    def _usage_end(self, ex: Exchange) -> None:
        if ex.cpu_start is None or ex.shared or self.process is None:
            return
        usage = watchdog.read(self.process.pid)
        if usage is not None:
            ex.cpu_ms = round(max(0.0, usage.cpu_seconds - ex.cpu_start) * 1000, 2)
            ex.memory_peak_mb = max(ex.memory_peak_mb or 0.0, usage.rss_mb)

    # ── what the worker says ─────────────────────────────────────────────

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("t")
        if kind == "stats":
            self.memory = {"heap": int(message.get("heap") or 0), "rss": int(message.get("rss") or 0)}
            return
        if kind == "log":
            self._log(message)
            return
        if kind == "call":
            self._spawn(self._serve_call(message))
            return
        if kind in ("fetch", "fetch_body", "fetch_body_end", "fetch_abort"):
            self._fetch_message(message)
            return
        ex = self.exchanges.get(str(message.get("id")))
        if ex is None:
            return
        if kind == "head":
            ex.status = int(message["status"])
            if not ex.head.done():
                ex.head.set_result((ex.status, [(str(k), str(v)) for k, v in message.get("headers") or []]))
        elif kind == "chunk":
            data = base64.b64decode(message.get("data") or "")
            ex.response_bytes += len(data)
            ex.chunks.put_nowait(data)
        elif kind == "end":
            ex.chunks.put_nowait(None)
            self._finish(ex)
        elif kind == "failed":
            failure = Failure(
                str(message.get("kind") or "Error"),
                str(message.get("message") or ""),
                [
                    Where(p.get("file"), p.get("line"), p.get("function"), p.get("code"))
                    for p in message.get("where") or []
                ],
            )
            ex.error = failure
            if not ex.head.done():
                ex.head.set_exception(HandlerFailed(failure))
            else:
                ex.chunks.put_nowait(HandlerFailed(failure))
            self.failed += 1
            self._finish(ex)
        elif kind == "background":
            pending = int(message.get("pending") or 0)
            if pending and ex.background_started is None:
                ex.background_started = time.monotonic()
            ex.background = pending
            if pending:
                ex.background_done.clear()
            else:
                if ex.background_started is not None:
                    ex.background_ms = round((time.monotonic() - ex.background_started) * 1000, 3)
                ex.background_done.set()
                self._forget_if_over(ex)

    def _log(self, message: dict[str, Any]) -> None:
        line = {
            "level": str(message.get("level") or "info"),
            "message": str(message.get("message") or ""),
            "at": time.time(),
        }
        ex = self.exchanges.get(str(message.get("id"))) if message.get("id") else None
        if ex is not None:
            ex.logs.append(line)
        else:
            logger.info("[%s %s] %s", self.spec.env, self.spec.function, line["message"][:500])

    def _finish(self, ex: Exchange) -> None:
        if not ex.finished.is_set():
            self._usage_end(ex)
            ex.finished.set()
            self.served += 1
            self.last_used = time.monotonic()
        self._forget_if_over(ex)

    def _forget_if_over(self, ex: Exchange) -> None:
        if ex.finished.is_set() and ex.background_done.is_set():
            self.exchanges.pop(ex.id, None)
            self.last_used = time.monotonic()

    # ── ctx.runtime ──────────────────────────────────────────────────────

    async def _serve_call(self, message: dict[str, Any]) -> None:
        cid = message.get("cid")
        ex = self.exchanges.get(str(message.get("id")))
        if ex is None:
            reply: dict[str, Any] = {
                "t": "reply",
                "cid": cid,
                "ok": False,
                "status": 409,
                "body": {"error": "request_over", "message": "that request is over; its runtime is gone", "details": None},
            }
        else:
            try:
                body = await ex.bridge.call(
                    str(message.get("method")), message.get("args") or [], message.get("kwargs") or {}
                )
                reply = {"t": "reply", "cid": cid, "ok": True, "body": body}
            except Exception as exc:  # noqa: BLE001 - the function is told what failed, typed
                status, payload = failure_reply(exc)
                reply = {"t": "reply", "cid": cid, "ok": False, "status": status, "body": payload}
        with contextlib.suppress(WorkerCrashed, ConnectionError, RuntimeError):
            await self._send(reply)

    # ── fetch() ──────────────────────────────────────────────────────────

    def _fetch_message(self, message: dict[str, Any]) -> None:
        kind = message["t"]
        fid = int(message.get("fid") or 0)
        if kind == "fetch":
            state: dict[str, Any] = {
                "message": message,
                "body": bytearray(),
                "body_done": asyncio.Event(),
                "response": None,
                "task": None,
            }
            if not message.get("body"):
                state["body_done"].set()
            self._fetches[fid] = state
            task = asyncio.create_task(self._fetch(fid, state))
            state["task"] = task
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return
        state = self._fetches.get(fid)
        if state is None:
            return
        if kind == "fetch_body":
            state["body"].extend(base64.b64decode(message.get("data") or ""))
        elif kind == "fetch_body_end":
            state["body_done"].set()
        elif kind == "fetch_abort":
            task = state.get("task")
            if task is not None and not task.done():
                task.cancel()
            self._fetches.pop(fid, None)

    async def _fetch(self, fid: int, state: dict[str, Any]) -> None:
        message = state["message"]
        ex = self.exchanges.get(str(message.get("id")))
        try:
            if ex is None:
                raise EgressRefused("that request is over; it can no longer fetch", "request_over")
            await state["body_done"].wait()
            body = bytes(state["body"]) if message.get("body") else None
            response = await self.egress.open(
                str(message.get("method") or "GET"),
                str(message.get("url")),
                [(str(k), str(v)) for k, v in message.get("headers") or []],
                body,
                redirect=str(message.get("redirect") or "follow"),
                budget=ex.budget,
            )
            state["response"] = response
            ex.logs.append(
                {"level": "debug", "message": f"fetch {message.get('method')} {response.url} → {response.status}", "at": time.time()}
            )
            await self._send(
                {
                    "t": "fetch_head",
                    "fid": fid,
                    "status": response.status,
                    "status_text": response.reason,
                    "headers": response.headers,
                    "url": response.url,
                    "redirected": response.redirected,
                }
            )
            async for chunk in response.chunks():
                for at in range(0, len(chunk), CHUNK):
                    await self._send({"t": "fetch_body", "fid": fid, "data": base64.b64encode(chunk[at : at + CHUNK]).decode()})
            await self._send({"t": "fetch_end", "fid": fid})
        except asyncio.CancelledError:
            pass
        except EgressRefused as exc:
            if ex is not None:
                ex.logs.append({"level": "warning", "message": f"fetch {message.get('url')} refused: {exc}", "at": time.time()})
            with contextlib.suppress(Exception):
                await self._send({"t": "fetch_error", "fid": fid, "message": str(exc), "code": exc.code})
        except Exception as exc:  # noqa: BLE001 - the function sees a failed fetch, as it would on a network error
            with contextlib.suppress(Exception):
                await self._send({"t": "fetch_error", "fid": fid, "message": f"fetch failed: {exc}", "code": "network"})
        finally:
            response = state.get("response")
            if response is not None:
                with contextlib.suppress(Exception):
                    await response.close()
            self._fetches.pop(fid, None)

    # ── requests ─────────────────────────────────────────────────────────

    async def open(
        self,
        *,
        method: str,
        url: str,
        path: str,
        headers: list[tuple[str, str]],
        body: AsyncIterator[bytes] | None,
        context: dict[str, Any],
        bridge: Bridge,
        budget: Budget,
    ) -> Exchange:
        """Send a request to the worker. Its body is streamed as it arrives; await ``exchange.head`` for the response's start."""
        ex = Exchange(id=secrets.token_hex(8), method=method, path=path, bridge=bridge, budget=budget)
        self.exchanges[ex.id] = ex
        self._usage_start(ex)
        self.last_used = time.monotonic()
        await self._send(
            {
                "t": "request",
                "id": ex.id,
                "method": method,
                "url": url,
                "headers": headers,
                "body": body is not None,
                "context": {**context, "path": path},
            }
        )
        if body is not None:
            self._spawn(self._pump(ex, body))
        return ex

    async def _pump(self, ex: Exchange, body: AsyncIterator[bytes]) -> None:
        try:
            async for chunk in body:
                if ex.finished.is_set() or not chunk:
                    continue
                ex.request_bytes += len(chunk)
                for at in range(0, len(chunk), CHUNK):
                    await self._send({"t": "body", "id": ex.id, "data": base64.b64encode(chunk[at : at + CHUNK]).decode()})
            await self._send({"t": "body_end", "id": ex.id})
        except (WorkerCrashed, ConnectionError, RuntimeError):
            pass
        except Exception:  # noqa: BLE001 - the caller's body broke off: the function sees an aborted request
            await self.abort(ex, "the request body was not received completely")

    async def abort(self, ex: Exchange, reason: str) -> None:
        """Tell the function its request is over (the caller left, or the time ran out)."""
        if ex.aborted is None:
            ex.aborted = reason
        with contextlib.suppress(WorkerCrashed, ConnectionError, RuntimeError):
            await self._send({"t": "abort", "id": ex.id, "reason": reason})

    async def body(self, ex: Exchange) -> AsyncIterator[bytes]:
        """The response body, chunk by chunk, until the function ends it. A failure after the head raises here."""
        while True:
            item = await ex.chunks.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

