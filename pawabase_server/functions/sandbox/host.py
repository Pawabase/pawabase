"""The API's side of the function sandbox: a pool of worker processes per deployment.

A worker is started the first time a deployment's function is called (importing the deployment takes a second or so), reused for the calls after that, and
stopped when it has been idle for a while or when the deployment is replaced. One worker does one call at a time; more concurrent calls start more workers,
up to a limit, and the rest wait. A call that runs past its timeout, or is cancelled, kills its worker: nothing in Python can stop a function that is stuck
in a loop, but the operating system can stop its process, and the next call gets a fresh one.

While a call runs, the worker asks for what ``ctx.runtime`` needs (data, events, secrets, outbound HTTP) and this side answers from the real
:class:`~pawabase_server.functions.runtime.ApiRuntime` of that invocation: the identity and the environment are the host's, never the worker's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawabase.codec import decode, encode
from sillo.exceptions import HTTPException

from pawabase_server.core.failures import Failure, Where
from pawabase_server.flows import FlowError
from pawabase_server.functions.registry import FunctionError, deployment_key
from pawabase_server.functions.runtime_rpc import METHODS
from pawabase_server.functions.sandbox import typescript
from pawabase_server.functions.service.bridge import failure_reply

logger = logging.getLogger("pawabase.sandbox")

#: What a function may do through the runtime when it is sandboxed: the HTTP runtime's methods, plus outbound requests, which are made from the platform
#: (and through its guard) because the worker has no network of its own.
ALLOWED = METHODS | {"http_request"}
API_ROOT = Path(__file__).resolve().parents[3]
MAX_MESSAGE = 64 * 1024 * 1024
STDERR_LINES = 20
REAP_SECONDS = 30.0


def resolve_confinement(setting: str) -> str:
    """``required`` or ``off`` from the setting: ``auto`` requires confinement where it exists (Linux) and warns where it cannot."""
    if setting != "auto":
        return setting
    if sys.platform == "linux":
        return "required"
    logger.warning(
        "function sandbox: %s has no Landlock or seccomp; functions run in worker processes without filesystem or network "
        "confinement. Run Pawabase on Linux for confined functions.",
        sys.platform,
    )
    return "off"


class SandboxFailure(Exception):
    """A sandboxed function failed in a way it did not choose (it raised, or its process stopped). ``failure`` says what and where."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.summary)
        self.failure = failure


@dataclass(frozen=True)
class Limits:
    memory_mb: int = 1024
    files: int = 256
    file_mb: int = 64
    workers: int = 4
    idle_seconds: int = 300
    start_seconds: int = 30
    #: ``required`` or ``off`` (see :func:`resolve_confinement`).
    confinement: str = "off"

    def for_worker(self) -> dict[str, Any]:
        return {"memory_mb": self.memory_mb, "files": self.files, "file_mb": self.file_mb}


class Worker:
    """One worker process, running one deployment."""

    def __init__(
        self,
        key: tuple[str, str, str],
        artifact: Path,
        libs: list[Path],
        limits: Limits,
        language: str = typescript.PYTHON,
        cache: Path | None = None,
    ) -> None:
        self.key = key
        #: The deployment's vendored npm/jsr/URL modules (TypeScript), or ``None``.
        self.cache = cache
        #: ``python`` or ``typescript``: which worker program runs this deployment.
        self.language = language
        self.artifact = artifact
        self.libs = libs
        self.limits = limits
        self.process: asyncio.subprocess.Process | None = None
        self.functions: set[str] = set()
        self.errors: list[str] = []
        self.last_used = time.monotonic()
        self.stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._drain: asyncio.Task | None = None
        self._tmp = Path(tempfile.mkdtemp(prefix="pawabase-sandbox-"))
        #: What the worker reported confining itself with (see ``confine.confine``).
        self.confinement: dict[str, Any] = {}

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def _environment(self) -> dict[str, str]:
        """What the worker is given to run with: no secrets, no database address, nothing of the API's own environment."""
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8",
            # glibc reserves 64 MB of address space per thread arena; under the address-space limit that stops threads early.
            "MALLOC_ARENA_MAX": "2",
            "TMPDIR": str(self._tmp),
            "HOME": str(self._tmp),
        }

    async def start(self) -> None:
        env, branch, _ = self.key
        command = (
            typescript.launcher(
                "serve",
                self.artifact,
                self._tmp,
                memory_mb=self.limits.memory_mb,
                confinement=self.limits.confinement,
                cache=self.cache,
            )
            if self.language == typescript.TYPESCRIPT
            else [sys.executable, "-m", "pawabase_server.functions.sandbox.worker"]
        )
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(API_ROOT),
            env=self._environment(),
            limit=MAX_MESSAGE,
            # Its own process group: nothing it signals as a group reaches the server.
            start_new_session=True,
        )
        self._drain = asyncio.create_task(self._drain_stderr())
        await self.send(
            {
                "t": "init",
                "artifact": str(self.artifact),
                "libs": [str(p) for p in self.libs],
                "key": deployment_key(env, branch),
                "env": env,
                "branch": branch,
                "limits": self.limits.for_worker(),
                "confinement": self.limits.confinement,
            }
        )
        try:
            ready = await asyncio.wait_for(self.read(), self.limits.start_seconds)
        except (TimeoutError, EOFError) as exc:
            await self.kill()
            raise SandboxFailure(
                Failure(
                    "SandboxStartFailed",
                    f"the function process did not start{': ' + self.stderr[-1] if self.stderr else ''}",
                    hint="Check the deployment's libraries and that it imports without errors.",
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
        self.functions = set(ready.get("functions") or [])
        self.errors = list(ready.get("errors") or [])
        if self.errors and not self.functions:
            await self.kill()
            raise SandboxFailure(
                Failure(
                    "DeploymentDidNotLoad",
                    f"the deployment loaded no functions: {self.errors[0]}",
                    hint="Fix the first error (see Studio -> Functions) and deploy again.",
                )
            )
        self.confinement = ready.get("confinement") or {}

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        async for raw in self.process.stderr:
            line = raw.decode(errors="replace").rstrip()
            if line:
                self.stderr.append(line)
                logger.info("[%s/%s] %s", self.key[0], self.key[1], line[:500])

    async def send(self, message: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write((json.dumps(message, default=str) + "\n").encode())
        await self.process.stdin.drain()

    async def read(self) -> dict[str, Any]:
        assert self.process is not None and self.process.stdout is not None
        line = await self.process.stdout.readline()
        if not line:
            raise EOFError
        return json.loads(line)

    async def kill(self) -> None:
        if self.process is not None and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.process.wait(), 5)
        if self._drain is not None:
            self._drain.cancel()
        shutil.rmtree(self._tmp, ignore_errors=True)

    async def run(self, invocation: dict[str, Any], bridge: Any) -> dict[str, Any]:
        """Send one invocation and answer the worker's runtime calls through *bridge* until it reports ``done``."""
        serving: set[asyncio.Task] = set()
        try:
            await self.send({"t": "invoke", **invocation})
            while True:
                message = await self.read()
                if message["t"] == "done":
                    return message
                task = asyncio.create_task(self._serve(message, bridge))
                serving.add(task)
                task.add_done_callback(serving.discard)
        finally:
            for task in serving:
                task.cancel()
            with contextlib.suppress(Exception):
                await bridge.close()  # whatever the call left open is not committed

    async def _serve(self, message: dict[str, Any], bridge: Any) -> None:
        kind, number = message["t"], message["id"]
        try:
            if kind == "call":
                body = await bridge.call(
                    message["method"], message.get("args") or [], message.get("kwargs") or {}
                )
            else:
                body = await bridge.db(message["op"], message.get("fields") or {})
            reply: dict[str, Any] = {"t": "reply", "id": number, "ok": True, "body": body}
        except Exception as exc:  # noqa: BLE001 - the function is told what failed, typed
            status, payload = failure_reply(exc)
            reply = {"t": "reply", "id": number, "ok": False, "status": status, "body": payload}
        with contextlib.suppress(Exception):
            await self.send(reply)


def invocation_of(context: Any, function: str) -> dict[str, Any]:
    """The ``invoke`` message for *function* called with *context* (a ``FunctionContext``)."""
    return {
        "function": function,
        "input": encode(context.input),
        "auth": context.auth,
        "project": context.project,
        "trigger": context.trigger,
        "request": encode(context.request),
    }


class SandboxPool:
    """Workers for every deployment this process runs sandboxed."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self.idle: dict[tuple[str, str, str], list[Worker]] = {}
        self.all: set[Worker] = set()
        self._slots: dict[tuple[str, str, str], asyncio.Semaphore] = {}
        self._reaper: asyncio.Task | None = None
        self.started = 0

    async def invoke(
        self,
        deployments: Any,
        *,
        env: str,
        branch: str,
        function: str,
        context: Any,
        bridge: Any,
    ) -> Any:
        """Run *function* of the deployment live for *env*/*branch* and return what it returned (or raise what it raised)."""
        done = await self.run_raw(
            deployments,
            env=env,
            branch=branch,
            function=function,
            invocation=invocation_of(context, function),
            bridge=bridge,
        )
        context.logs.extend(decode(done.get("logs") or []))
        return self._result(done)

    async def run_raw(
        self,
        deployments: Any,
        *,
        env: str,
        branch: str,
        function: str,
        invocation: dict[str, Any],
        bridge: Any,
    ) -> dict[str, Any]:
        """Run one invocation and return the worker's ``done`` message as it is (what the runtime service sends back to the server)."""
        stamp = deployments.stamp(env, branch)
        if stamp is None:
            raise FunctionError(f"no deployment for {env!r}", status=404, code="not_found")
        key = (env, branch, stamp["id"])
        await self._retire_stale(env, branch, stamp["id"])
        slot = self._slots.setdefault(key, asyncio.Semaphore(self.limits.workers))
        async with slot:
            worker = await self._take(key, deployments, env, branch)
            try:
                done = await worker.run({**invocation, "id": 1, "function": function}, bridge)
            except EOFError:
                await self._drop(worker)  # first: it waits for the process, whose exit code says why it stopped
                crash = self._crashed(worker)
                return {"outcome": "crashed", "kind": crash.kind, "message": crash.message, "hint": crash.hint}
            except BaseException:  # a timeout or cancellation: the process may be stuck, so it does not get another call
                await self._drop(worker)
                raise
            worker.last_used = time.monotonic()
            self.idle.setdefault(key, []).append(worker)
        return done

    def _crashed(self, worker: Worker) -> Failure:
        code = worker.process.returncode if worker.process is not None else None
        tail = f" ({worker.stderr[-1][:200]})" if worker.stderr else ""
        if code == -signal.SIGXCPU:
            return Failure(
                "CpuLimitExceeded",
                "the function used more CPU time than its limit and was stopped",
                hint="A call may use as much CPU time as its timeout allows. Move heavy work to a job, or raise the timeout.",
            )
        return Failure(
            "SandboxCrashed",
            f"the function's process stopped (exit code {code}){tail}",
            hint=f"It may have run out of memory (the limit is {self.limits.memory_mb} MB) or called exit.",
        )

    @staticmethod
    def _result(done: dict[str, Any]) -> Any:
        outcome = done.get("outcome")
        if outcome == "ok":
            return decode(done.get("result"))
        if outcome == "timeout":
            raise TimeoutError
        if outcome == "error":
            raise FunctionError(
                done.get("message", ""),
                status=int(done.get("status", 500)),
                code=done.get("code", "error"),
                details=decode(done.get("details")),
            )
        if outcome == "http":
            raise HTTPException(status_code=int(done["status"]), detail=decode(done.get("detail")))
        if outcome == "crashed":
            raise SandboxFailure(
                Failure(done.get("kind") or "SandboxCrashed", done.get("message") or "", hint=done.get("hint"))
            )
        if outcome == "missing":
            raise SandboxFailure(
                Failure(
                    "FunctionMissing",
                    "the function is not in the running deployment",
                    hint="Deploy again.",
                )
            )
        raise SandboxFailure(
            Failure(
                done.get("kind", "Error"),
                done.get("message", ""),
                [
                    Where(p["file"], p["line"], p["function"], p.get("code"))
                    for p in done.get("where") or []
                ],
                done.get("hint"),
            )
        )

    async def _take(
        self, key: tuple[str, str, str], deployments: Any, env: str, branch: str
    ) -> Worker:
        while self.idle.get(key):
            worker = self.idle[key].pop()
            if worker.alive:
                return worker
            await self._drop(worker)
        worker = Worker(
            key,
            deployments.current(env, branch),
            deployments.libraries(env, branch),
            self.limits,
            deployments.language(env, branch),
            deployments.vendor_cache(env, branch) if hasattr(deployments, "vendor_cache") else None,
        )
        self.all.add(worker)
        self.started += 1
        try:
            await worker.start()
        except BaseException:
            await self._drop(worker)
            raise
        self._ensure_reaper()
        return worker

    async def _retire_stale(self, env: str, branch: str, current: str) -> None:
        """A replaced deployment's workers are stopped; the next call starts the new one."""
        for key in [k for k in self.idle if k[0] == env and k[1] == branch and k[2] != current]:
            for worker in self.idle.pop(key):
                await self._drop(worker)

    async def _drop(self, worker: Worker) -> None:
        self.all.discard(worker)
        await worker.kill()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        while self.all:
            await asyncio.sleep(REAP_SECONDS)
            now = time.monotonic()
            for workers in self.idle.values():
                for worker in [
                    w
                    for w in workers
                    if not w.alive or now - w.last_used > self.limits.idle_seconds
                ]:
                    workers.remove(worker)
                    await self._drop(worker)

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for worker in list(self.all):
            await self._drop(worker)
        self.idle.clear()
