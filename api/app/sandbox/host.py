"""The API's side of the function sandbox: a pool of worker processes per deployment.

A worker is started the first time a deployment's function is called (importing the deployment takes a second or so), reused for the calls after that, and
stopped when it has been idle for a while or when the deployment is replaced. A worker runs many calls at once, as the event loop did before, up to
``concurrency``; a busier deployment starts more workers, up to ``workers``, and the rest wait. A call that runs past its timeout is first cancelled
politely, which is enough for a function that is waiting on something. Only a worker that does not answer (a function stuck in a loop) is killed, since
nothing in Python can stop that but the operating system, and the next call gets a fresh one.

While a call runs, the worker asks for what ``ctx.runtime`` needs (data, events, secrets, outbound HTTP) and this side answers from the real
:class:`~app.runtime.ApiRuntime` of that invocation: the identity and the environment are the host's, never the worker's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pawabase.codec import decode, encode
from sillo.exceptions import HTTPException

from app.runtime_rpc import METHODS, Transactions, failure_payload, serve_call, serve_db
from pawabase_core.failures import Failure, Where
from pawabase_core.flows import FlowError
from pawabase_core.functions import FunctionError, deployment_key

logger = logging.getLogger("pawabase.sandbox")

#: What a function may do through the runtime when it is sandboxed: the HTTP runtime's methods, plus outbound requests, which are made from the platform
#: (and through its guard), so that a request made through the runtime is checked.
ALLOWED = METHODS | {"http_request"}
API_ROOT = Path(__file__).resolve().parents[2]
MAX_MESSAGE = 64 * 1024 * 1024
STDERR_LINES = 20
REAP_SECONDS = 30.0


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
    #: Calls one worker runs at once.
    concurrency: int = 16
    #: Calls a worker carries before another is started for the next one (a function that computes, not only waits, gains from more workers).
    spread: int = 4
    idle_seconds: int = 300
    start_seconds: int = 30

    def for_worker(self) -> dict[str, int]:
        return {"memory_mb": self.memory_mb, "files": self.files, "file_mb": self.file_mb}


#: How long a cancelled call has to answer before its worker is taken to be stuck.
CANCEL_GRACE = 1.0


class Worker:
    """One worker process, running one deployment, with many calls in flight."""

    def __init__(
        self, key: tuple[str, str, str], artifact: Path, libs: list[Path], limits: Limits
    ) -> None:
        self.key = key
        self.artifact = artifact
        self.libs = libs
        self.limits = limits
        self.process: asyncio.subprocess.Process | None = None
        self.functions: set[str] = set()
        self.errors: list[str] = []
        self.last_used = time.monotonic()
        self.stderr: deque[str] = deque(maxlen=STDERR_LINES)
        #: Calls running or about to (counted by the pool before they start), and the messages of each, by call number.
        self.busy = 0
        self.retiring = False
        #: Set when a cancelled call did not answer in time: the worker is stuck and must be killed, not reused.
        self.stuck = False
        self.starting: asyncio.Task | None = None
        self.calls: dict[int, asyncio.Queue] = {}
        self._counter = 0
        self._drain: asyncio.Task | None = None
        self._pump_task: asyncio.Task | None = None
        self._tmp = Path(tempfile.mkdtemp(prefix="pawabase-sandbox-"))

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    @property
    def dead(self) -> bool:
        """Gone: it exited, or never started. A worker that is still starting is neither."""
        return (self.process is not None and self.process.returncode is not None) or (
            self.starting is not None
            and self.starting.done()
            and self.starting.exception() is not None
        )

    async def wait_ready(self) -> None:
        """Wait for the worker to finish starting (the calls that arrive meanwhile share one start); re-raises a failed start."""
        if self.starting is not None:
            await asyncio.shield(self.starting)

    def _environment(self) -> dict[str, str]:
        """What the worker is given to run with: no secrets, no database address, nothing of the API's own environment."""
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "LANG": "C.UTF-8",
            "TMPDIR": str(self._tmp),
            "HOME": str(self._tmp),
        }

    async def start(self) -> None:
        env, branch, _ = self.key
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.sandbox.worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(API_ROOT),
            env=self._environment(),
            limit=MAX_MESSAGE,
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
        self.functions = set(ready.get("functions") or [])
        self.errors = list(ready.get("errors") or [])
        self._pump_task = asyncio.create_task(self._pump())

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

    async def _pump(self) -> None:
        """Hand each message from the worker to the call it belongs to; when the worker is gone, tell every call."""
        try:
            while True:
                message = await self.read()
                number = message["id"] if message["t"] == "done" else message.get("inv")
                queue = self.calls.get(number)
                if queue is not None:
                    queue.put_nowait(message)
        except (EOFError, ValueError, ConnectionError):
            pass
        finally:
            for queue in self.calls.values():
                queue.put_nowait(None)

    async def kill(self) -> None:
        if self.process is not None and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.process.wait(), 5)
        for task in (self._pump_task, self._drain):
            if task is not None:
                task.cancel()
        for queue in self.calls.values():
            queue.put_nowait(None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    async def run(
        self, invocation: dict[str, Any], runtime: Any, transactions: Transactions
    ) -> dict[str, Any]:
        """Send one invocation and serve the worker's runtime calls until it reports ``done``.

        If this task is cancelled (the call's time ran out) the worker is asked to cancel the call too; ``healthy`` is whether it answered.
        """
        self._counter += 1
        number = self._counter
        queue: asyncio.Queue = asyncio.Queue()
        self.calls[number] = queue
        owned: set[str] = set()
        serving: set[asyncio.Task] = set()
        try:
            await self.send({"t": "invoke", **invocation, "id": number})
            while True:
                message = await queue.get()
                if message is None:
                    raise EOFError
                if message["t"] == "done":
                    return message
                task = asyncio.create_task(self._serve(message, runtime, transactions, owned))
                serving.add(task)
                task.add_done_callback(serving.discard)
        except asyncio.CancelledError:
            if not await self._cancel(number, queue):
                self.stuck = True
            raise
        finally:
            self.calls.pop(number, None)
            for task in serving:
                task.cancel()
            for tx in list(owned):  # whatever the call left open is not committed
                with contextlib.suppress(Exception):
                    await transactions.finish(tx, commit=False)

    async def _cancel(self, number: int, queue: asyncio.Queue) -> bool:
        """Ask the worker to cancel call *number*; true if it answered in time (so it is not stuck)."""
        try:
            await self.send({"t": "cancel", "id": number})

            async def answered() -> bool:
                while True:
                    message = await queue.get()
                    if message is None:
                        return False
                    if message["t"] == "done":
                        return True

            return await asyncio.wait_for(answered(), CANCEL_GRACE)
        except (Exception, asyncio.CancelledError):
            return False

    async def _serve(
        self, message: dict[str, Any], runtime: Any, transactions: Transactions, owned: set[str]
    ) -> None:
        kind, number = message["t"], message["id"]
        try:
            if kind == "call":
                body: dict[str, Any] = {
                    "result": await serve_call(
                        runtime,
                        message["method"],
                        message.get("args") or [],
                        message.get("kwargs") or {},
                        allowed=ALLOWED,
                    )
                }
            else:
                fields = message.get("fields") or {}
                op = message["op"]
                if op != "begin" and fields.get("tx") not in owned and fields.get("tx") is not None:
                    raise FlowError(
                        "That transaction belongs to another call.",
                        status=409,
                        code="no_transaction",
                    )
                body = await serve_db(runtime, transactions, op, fields)
                if op == "begin":
                    owned.add(body["tx"])
                elif op in ("commit", "rollback"):
                    owned.discard(fields.get("tx"))
            reply: dict[str, Any] = {"t": "reply", "id": number, "ok": True, "body": body}
        except Exception as exc:  # noqa: BLE001 - the function is told what failed, typed
            status, payload = failure_payload(exc)
            reply = {"t": "reply", "id": number, "ok": False, "status": status, "body": payload}
        with contextlib.suppress(Exception):
            await self.send(reply)


class SandboxPool:
    """Workers for every deployment this process runs sandboxed."""

    def __init__(self, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()
        self.workers: dict[tuple[str, str, str], list[Worker]] = {}
        self._slots: dict[tuple[str, str, str], asyncio.Semaphore] = {}
        self._reaper: asyncio.Task | None = None
        self.started = 0

    @property
    def all(self) -> list[Worker]:
        return [w for workers in self.workers.values() for w in workers]

    async def invoke(
        self,
        deployments: Any,
        *,
        env: str,
        branch: str,
        function: str,
        context: Any,
        runtime: Any,
        transactions: Transactions,
    ) -> Any:
        """Run *function* of the deployment live for *env*/*branch* and return what it returned (or raise what it raised)."""
        stamp = deployments.stamp(env, branch)
        if stamp is None:
            raise FunctionError(f"no deployment for {env!r}", status=404, code="not_found")
        key = (env, branch, stamp["id"])
        await self._retire_stale(env, branch, stamp["id"])
        capacity = self.limits.workers * self.limits.concurrency
        async with self._slots.setdefault(key, asyncio.Semaphore(capacity)):
            worker = await self._take(key, deployments, env, branch)
            try:
                await worker.wait_ready()
            except BaseException:
                worker.busy -= 1
                await self._drop(worker)
                raise
            invocation = {
                "function": function,
                "input": encode(context.input),
                "auth": context.auth,
                "project": context.project,
                "trigger": context.trigger,
                "request": encode(context.request),
            }
            try:
                done = await worker.run(invocation, runtime, transactions)
            except EOFError as exc:
                await self._drop(worker)
                raise SandboxFailure(self._crashed(worker)) from exc
            except BaseException:
                # A timeout or cancellation. A worker that answered the cancel is fine and keeps its other calls; one that did not is stuck and is killed.
                if worker.stuck:
                    await self._drop(worker)
                raise
            finally:
                worker.busy -= 1
                worker.last_used = time.monotonic()
                if worker.retiring and worker.busy == 0:
                    await self._drop(worker)
        context.logs.extend(decode(done.get("logs") or []))
        return self._result(done)

    def _crashed(self, worker: Worker) -> Failure:
        code = worker.process.returncode if worker.process is not None else None
        tail = f" ({worker.stderr[-1][:200]})" if worker.stderr else ""
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
        if outcome in ("timeout", "cancelled"):
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
        """A worker with room for one more call: the least busy one, or a new one when all are full and the limit allows."""
        workers = self.workers.setdefault(key, [])
        for dead in [w for w in workers if w.dead]:
            workers.remove(dead)
            await self._drop(dead)
        room = [
            w
            for w in workers
            if w.busy < self.limits.concurrency and not w.retiring and not w.stuck
        ]
        if (
            room
            and min(w.busy for w in room) >= self.limits.spread
            and len(workers) < self.limits.workers
        ):
            room = []  # the least busy worker already has a handful of calls and another may start: spread the load
        if room:
            worker = min(room, key=lambda w: w.busy)
            worker.busy += (
                1  # reserved before anything is awaited, so two calls cannot take the last place
            )
            return worker
        worker = Worker(
            key, deployments.current(env, branch), deployments.libraries(env, branch), self.limits
        )
        worker.busy = 1
        workers.append(worker)
        self.started += 1
        worker.starting = asyncio.create_task(
            worker.start()
        )  # calls that arrive while it starts wait for this one start
        self._ensure_reaper()
        return worker

    async def _retire_stale(self, env: str, branch: str, current: str) -> None:
        """A replaced deployment's workers are stopped when their calls end; the next call starts the new one."""
        for key in [k for k in self.workers if k[0] == env and k[1] == branch and k[2] != current]:
            for worker in list(self.workers[key]):
                worker.retiring = True
                if worker.busy == 0:
                    await self._drop(worker)

    async def _drop(self, worker: Worker) -> None:
        workers = self.workers.get(worker.key)
        if workers is not None and worker in workers:
            workers.remove(worker)
        if workers is not None and not workers:
            self.workers.pop(worker.key, None)
        await worker.kill()

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        while self.all:
            await asyncio.sleep(REAP_SECONDS)
            now = time.monotonic()
            for worker in [
                w
                for w in self.all
                if not w.alive or (w.busy == 0 and now - w.last_used > self.limits.idle_seconds)
            ]:
                await self._drop(worker)

    async def close(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for worker in self.all:
            await self._drop(worker)
