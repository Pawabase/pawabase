"""A sandbox worker: the process one deployment's functions run in.

``python -m app.sandbox.worker`` speaks JSON lines on its pipes (the host starts it; nothing else should). The protocol:

* host → worker: ``init`` (where the deployment is, and the limits), ``invoke`` (one call), ``reply`` (the answer to a runtime call the worker made), ``stop``.
* worker → host: ``ready``, ``call`` and ``db`` (a ``ctx.runtime`` method the function used), ``done`` (the end of an invocation).

Standard output is the protocol, so it is pointed at standard error for the function's own ``print``. The worker is started with an empty environment: no
secrets, no database URL. It does one invocation at a time; the host starts more workers for more concurrency.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from pawabase.client import PawabaseError
from pawabase.codec import decode, encode
from pawabase.functions import FunctionContext, FunctionError, get_exact, load_code_dir
from pawabase.runtime import RemoteRuntime

from pawabase_core.failures import report

try:
    import resource
except ImportError:  # not on Windows
    resource = None  # type: ignore[assignment]

MAX_MESSAGE = 64 * 1024 * 1024


def apply_limits(limits: dict[str, Any]) -> None:
    """Cap what this process can take. Quietly skips a limit the system does not support (macOS has no address-space limit)."""
    if resource is None:
        return
    wanted = {
        "RLIMIT_AS": limits.get("memory_mb", 0) * 1024 * 1024,
        "RLIMIT_NOFILE": limits.get("files", 0),
        "RLIMIT_FSIZE": limits.get("file_mb", 0) * 1024 * 1024,
        "RLIMIT_CORE": 0,
    }
    for name, value in wanted.items():
        number = getattr(resource, name, None)
        if number is None or (value == 0 and name != "RLIMIT_CORE"):
            continue
        try:
            resource.setrlimit(number, (value, value))
        except (ValueError, OSError):
            pass


class Channel:
    """The protocol side of the pipes: JSON lines out, with the function's own output kept away from them."""

    def __init__(self, fd: int) -> None:
        self.fd = fd

    def send(self, message: dict[str, Any]) -> None:
        data = memoryview((json.dumps(message, default=str) + "\n").encode())
        while data:
            data = data[os.write(self.fd, data) :]


class PipeClient:
    """What :class:`RemoteRuntime` calls in place of an HTTP client: each request is a message to the host, answered by a ``reply``."""

    def __init__(self, channel: Channel) -> None:
        self.channel = channel
        self.pending: dict[int, asyncio.Future] = {}
        self.counter = 0

    async def _ask(self, kind: str, **fields: Any) -> Any:
        self.counter += 1
        number = self.counter
        waiting = asyncio.get_running_loop().create_future()
        self.pending[number] = waiting
        self.channel.send({"t": kind, "id": number, **fields})
        reply = await waiting
        if not reply["ok"]:
            raise PawabaseError(reply["status"], reply["body"])
        return reply["body"]

    async def runtime_call(self, method, args=None, kwargs=None, *, as_user=None, branch=None):
        # The identity and the branch are the host's to decide: a function does not get to name who it acts as.
        return await self._ask("call", method=method, args=args or [], kwargs=kwargs or {})

    async def runtime_db(self, op, **fields):
        return await self._ask("db", op=op, fields=fields)

    def resolve(self, message: dict[str, Any]) -> None:
        waiting = self.pending.pop(message["id"], None)
        if waiting is not None and not waiting.done():
            waiting.set_result(message)


class _NoHttp:
    """Stands in for the kit's HTTP client, which :class:`SandboxRuntime` never uses: building a real one for every call costs a TLS context."""


_NO_HTTP = _NoHttp()


class SandboxRuntime(RemoteRuntime):
    """``ctx.runtime`` in the sandbox. Outbound HTTP goes through the platform (and its guard), not out of this process."""

    async def http_request(
        self, method, url, *, headers=None, json=None, params=None, timeout=30.0, retries=0
    ):
        return await self._rpc(
            "http_request",
            method,
            url,
            headers=dict(headers or {}),
            json=json,
            params=dict(params) if params else None,
            timeout=timeout,
            retries=retries,
        )


def _where(failure: Any) -> list[dict[str, Any]]:
    return [
        {"file": p.file, "line": p.line, "function": p.function, "code": p.code}
        for p in failure.where
    ]


async def invoke(
    message: dict[str, Any], client: PipeClient, channel: Channel, loaded: dict[str, Any]
) -> None:
    """Run one function and send ``done``."""
    number = message["id"]
    spec = get_exact(loaded["key"], message["function"])
    if spec is None:
        channel.send({"t": "done", "id": number, "outcome": "missing"})
        return
    runtime = SandboxRuntime(
        client, auth=message.get("auth"), branch=loaded["branch"], http=_NO_HTTP
    )  # type: ignore[arg-type]
    context = FunctionContext(
        input=decode(message.get("input")),
        auth=dict(message.get("auth") or {}),
        project=message.get("project", ""),
        env=loaded["env"],
        runtime=runtime,
        trigger=message.get("trigger", "http"),
        request=decode(message.get("request")),
        branch=loaded["branch"],
    )
    reply: dict[str, Any] = {"t": "done", "id": number}
    try:
        reply.update(
            outcome="ok",
            result=encode(await asyncio.wait_for(spec.handler(context), timeout=spec.timeout)),
        )
    except TimeoutError:
        reply.update(outcome="timeout")
    except FunctionError as exc:
        reply.update(
            outcome="error",
            status=exc.status,
            code=exc.code,
            message=exc.message,
            details=encode(exc.details),
        )
    except Exception as exc:  # noqa: BLE001 - a function's bug is reported, not raised
        status, detail = getattr(exc, "status_code", None), getattr(exc, "detail", None)
        if isinstance(status, int) and 400 <= status < 600 and detail is not None:
            reply.update(outcome="http", status=status, detail=encode(detail))
        else:
            failure = report(exc, roots=(loaded["artifact"],))
            reply.update(
                outcome="failed",
                kind=failure.kind,
                message=failure.message,
                where=_where(failure),
                hint=failure.hint,
            )
    reply["logs"] = encode([*context.logs, *runtime.logs])
    channel.send(reply)


def load(message: dict[str, Any]) -> dict[str, Any]:
    apply_limits(message.get("limits") or {})
    libraries = [Path(p) for p in message.get("libs") or []]
    for library in reversed(
        libraries
    ):  # for good, not only while importing: a function may import lazily
        sys.path.insert(0, str(library))
    code = load_code_dir(
        Path(message["artifact"]), message["key"], only_functions=True, extra_paths=libraries
    )
    return {
        "key": message["key"],
        "env": message["env"],
        "branch": message["branch"],
        "artifact": message["artifact"],
        "functions": code.functions,
        "errors": code.errors,
    }


async def serve() -> None:
    channel = Channel(os.dup(1))
    os.dup2(2, 1)  # whatever the function prints goes to standard error, away from the protocol
    sys.stdout = sys.stderr
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_MESSAGE)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    client = PipeClient(channel)
    loaded: dict[str, Any] = {}
    running: set[asyncio.Task] = set()
    while line := await reader.readline():
        message = json.loads(line)
        kind = message.get("t")
        if kind == "reply":
            client.resolve(message)
        elif kind == "init":
            try:
                loaded = load(message)
                channel.send(
                    {"t": "ready", "functions": loaded["functions"], "errors": loaded["errors"]}
                )
            except Exception as exc:  # noqa: BLE001
                channel.send(
                    {"t": "ready", "functions": [], "errors": [f"{type(exc).__name__}: {exc}"]}
                )
        elif kind == "invoke":
            task = asyncio.create_task(invoke(message, client, channel, loaded))
            running.add(task)
            task.add_done_callback(running.discard)
        elif kind == "stop":
            break


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
