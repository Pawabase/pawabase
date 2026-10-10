"""Serving the platform's runtime to code that runs somewhere else.

``ctx.runtime`` inside the API process is an :class:`~app.runtime.ApiRuntime`. Code that runs elsewhere (a developer's machine under ``pawabase emulate``,
or a function in a sandbox process) gets the same methods through a transport instead: a call names a method and its arguments, and this module runs it on
the real runtime and hands back the answer, or the failure, in :mod:`pawabase.codec` form so datetimes, decimals and bytes survive the trip.

* :func:`serve_call` runs one whitelisted runtime method.
* :func:`serve_db` runs SQL for ``db()`` and ``transaction()``; :class:`Transactions` holds the open ones.
* :func:`failure_payload` is how a failure is told to the caller.

The HTTP routes in ``routes/platform/runtime.py`` and the sandbox host in ``app/sandbox`` are two transports over this one implementation.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from pawabase.codec import decode, encode

from pawabase_core.flows import FlowError
from pawabase_core.functions import FunctionError

if TYPE_CHECKING:
    from app.runtime import ApiRuntime

TRANSACTION_SECONDS = 60
MAX_OPEN_TRANSACTIONS = 16

#: What a remote caller may do. ``http_request`` is absent on purpose: the emulator makes outbound requests from the developer's own machine.
METHODS = frozenset(
    {
        "resource_list",
        "resource_get",
        "resource_create",
        "resource_update",
        "resource_delete",
        "db_query",
        "db_transaction",
        "cache_get",
        "cache_set",
        "cache_delete",
        "cache_invalidate",
        "emit",
        "dispatch_flow",
        "call_flow",
        "dispatch_function",
        "publish",
        "storage_put",
        "storage_read",
        "storage_signed_url",
        "storage_delete",
        "send_mail",
        "webhook_send",
        "secret",
        "call_function",
        "identity_user",
        "check_policy",
        "log",
        "metric",
    }
)
DB_OPS = frozenset(
    {
        "fetch",
        "one",
        "scalar",
        "execute",
        "insert",
        "update",
        "delete",
        "begin",
        "commit",
        "rollback",
    }
)


class _Rollback(Exception):
    """Raised inside a held transaction to end it without committing."""


class _Held:
    """One open transaction, owned by one task.

    A database transaction is a context manager that must be entered and left from the same task (it keeps a context variable). A remote caller's
    ``begin``, statements and ``commit`` arrive as separate HTTP requests handled by different tasks, so the transaction lives in a task of its own that
    runs whatever it is sent, one thing at a time, and ends when told to commit or roll back (or when it is abandoned).
    """

    def __init__(self, runtime: ApiRuntime) -> None:
        self.deadline = time.monotonic() + TRANSACTION_SECONDS
        self.inbox: asyncio.Queue[tuple[Any, asyncio.Future] | None] = asyncio.Queue()
        self.ready: asyncio.Future = asyncio.get_running_loop().create_future()
        self.done: asyncio.Future = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._own(runtime))

    async def _own(self, runtime: ApiRuntime) -> None:
        try:
            async with runtime.transaction() as session:
                self.ready.set_result(None)
                while True:
                    message = await self.inbox.get()
                    if message is None:
                        break  # commit
                    work, reply = message
                    if work is _Rollback:
                        reply.set_result(True)
                        raise _Rollback
                    try:
                        reply.set_result(await work(session))
                    except Exception as exc:  # noqa: BLE001 - the caller decides whether a failed statement ends the transaction
                        reply.set_exception(exc)
        except _Rollback:
            pass
        except Exception as exc:  # noqa: BLE001
            if not self.ready.done():
                self.ready.set_exception(exc)
        finally:
            self.done.set_result(None)

    async def run(self, work: Any) -> Any:
        self.deadline = time.monotonic() + TRANSACTION_SECONDS
        reply: asyncio.Future = asyncio.get_running_loop().create_future()
        await self.inbox.put((work, reply))
        return await reply

    async def finish(self, *, commit: bool) -> None:
        if commit:
            await self.inbox.put(None)
        else:
            reply: asyncio.Future = asyncio.get_running_loop().create_future()
            await self.inbox.put((_Rollback, reply))
            await reply
        await self.done


class Transactions:
    """Open SQL transactions held for remote callers, each rolled back after a quiet minute."""

    def __init__(self) -> None:
        self.open: dict[str, _Held] = {}
        self._reaper: asyncio.Task | None = None

    async def begin(self, runtime: ApiRuntime) -> str:
        await self.reap()
        if len(self.open) >= MAX_OPEN_TRANSACTIONS:
            raise FlowError("too many open transactions", status=429, code="too_many_transactions")
        held = _Held(runtime)
        await held.ready
        tx = uuid.uuid4().hex
        self.open[tx] = held
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_forever())
        return tx

    def get(self, tx: str) -> _Held:
        held = self.open.get(tx)
        if held is None:
            raise FlowError(
                "That transaction is not open (it ended, timed out, or belongs to another process).",
                status=409,
                code="no_transaction",
            )
        return held

    async def finish(self, tx: str, *, commit: bool) -> None:
        held = self.open.pop(tx, None)
        if held is None:
            raise FlowError("That transaction is not open.", status=409, code="no_transaction")
        await held.finish(commit=commit)

    async def reap(self) -> None:
        now = time.monotonic()
        for tx in [tx for tx, held in self.open.items() if held.deadline < now]:
            try:
                await self.finish(tx, commit=False)
            except Exception:  # noqa: BLE001 - a transaction that cannot even roll back is already gone
                self.open.pop(tx, None)

    async def _reap_forever(self) -> None:
        while self.open:
            await asyncio.sleep(5)
            await self.reap()


def failure_payload(exc: Exception) -> tuple[int, dict[str, Any]]:
    """The status and body that tell a caller why a runtime call failed."""
    if isinstance(exc, (FlowError, FunctionError)):
        return exc.status, {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "status": exc.status,
                "details": encode(exc.details),
            }
        }
    return 500, {
        "error": {
            "code": "runtime_error",
            "message": f"{type(exc).__name__}: {exc}",
            "status": 500,
        }
    }


async def serve_call(
    runtime: ApiRuntime,
    method: str,
    args: list[Any],
    kwargs: dict[str, Any],
    *,
    allowed: frozenset[str] = METHODS,
) -> Any:
    """Run *method* on *runtime* with encoded *args*, and return the result encoded."""
    if method not in allowed:
        raise FlowError(f"{method!r} is not a runtime method", status=404, code="no_such_method")
    return encode(await getattr(runtime, method)(*decode(args), **decode(kwargs)))


async def serve_db(
    runtime: ApiRuntime, transactions: Transactions, op: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Run one database operation (``fetch``, ``insert``, ``begin``, ``commit`` ...) and return ``{"result": ...}`` or ``{"tx": ...}``."""
    if op not in DB_OPS:
        raise FlowError(f"{op!r} is not a database operation", status=422, code="no_such_operation")
    tx = fields.get("tx")
    if op == "begin":
        return {"tx": await transactions.begin(runtime), "expires_in": TRANSACTION_SECONDS}
    if op in ("commit", "rollback"):
        await transactions.finish(tx or "", commit=op == "commit")
        return {"result": True}
    params = decode(fields.get("params") or [])
    sql = fields.get("sql") or ""
    table = fields.get("table") or ""

    async def work(session: Any) -> Any:
        if op == "fetch":
            return await session.fetch(sql, params)
        if op == "one":
            return await session.one(sql, params)
        if op == "scalar":
            return await session.scalar(sql, params, decode(fields.get("default")))
        if op == "execute":
            return await session.execute(sql, params)
        if op == "insert":
            return await session.insert(table, decode(fields.get("data") or {}))
        if op == "update":
            return await session.update(
                table, decode(fields.get("id")), decode(fields.get("data") or {})
            )
        return await session.delete(table, decode(fields.get("id")))

    result = await (transactions.get(tx).run(work) if tx else work(await runtime.db()))
    return {"result": encode(result)}
