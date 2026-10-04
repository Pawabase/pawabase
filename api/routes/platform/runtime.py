"""The platform's runtime, over HTTP: how ``pawabase emulate`` (or a test) gives a function on your machine the same ``ctx.runtime`` it has when deployed.

Two endpoints, both for a project's own secret key with the ``runtime:use`` scope:

``POST /envs/{env}/runtime/call``
    ``{"method": "resource_get", "args": [...], "kwargs": {...}, "as_user": {...}, "branch": "main"}``. Only the methods in :data:`METHODS` are callable.
    ``as_user`` is the auth context the call runs as (the emulator passes the real caller's, so audit trails and policies see who acted).

``POST /envs/{env}/runtime/db``
    SQL for ``ctx.runtime.db()`` and ``transaction()``: ``{"op": "fetch|one|scalar|execute|insert|update|delete|begin|commit|rollback", ...}``. A transaction is
    opened with ``begin``, which returns an id the later calls carry, and is rolled back by itself after ``TRANSACTION_SECONDS`` of silence. Transactions live
    in the memory of the process that opened them, so they are meant for one developer's emulator, not for load.

Values travel in :mod:`pawabase.codec` form so datetimes, decimals and bytes survive the trip.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo import json as json_response
from sillo.exceptions import HTTPException

from app.platform import Platform
from app.runtime import ApiRuntime
from pawabase.codec import decode, encode
from pawabase_core.context import current_context
from pawabase_core.flows import FlowError
from pawabase_core.functions import FunctionError
from pawabase_core.principal import ANONYMOUS_POLICY_CONTEXT, Principal
from pawabase_core.tokens import TokenInvalid, verify_user_token
from routes.common import MANAGE, audit, get_environment

TRANSACTION_SECONDS = 60
MAX_OPEN_TRANSACTIONS = 16

#: What a remote caller may do. ``http_request`` is absent on purpose: the emulator makes outbound requests from the developer's own machine.
METHODS = frozenset({
    "resource_list", "resource_get", "resource_create", "resource_update", "resource_delete",
    "db_query", "db_transaction",
    "cache_get", "cache_set", "cache_delete", "cache_invalidate",
    "emit", "dispatch_flow", "call_flow", "dispatch_function", "publish",
    "storage_put", "storage_read", "storage_signed_url", "storage_delete",
    "send_mail", "webhook_send", "secret", "call_function", "identity_user", "check_policy", "log", "metric",
})
DB_OPS = frozenset({"fetch", "one", "scalar", "execute", "insert", "update", "delete", "begin", "commit", "rollback"})


class CallBody(BaseModel):
    method: str
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    as_user: dict[str, Any] | None = None
    branch: str | None = None


class IdentifyBody(BaseModel):
    token: str | None = Field(default=None, description="A user's access token, or nothing for an anonymous caller")


class DbBody(BaseModel):
    op: str
    tx: str | None = None
    sql: str | None = None
    params: list[Any] = Field(default_factory=list)
    table: str | None = None
    id: Any = None
    data: dict[str, Any] | None = None
    default: Any = None


def _scope(ctx: HttpContext) -> None:
    context = current_context(ctx)
    if context is not None and context.is_service and not context.allows_scope("runtime:use"):
        raise HTTPException(status_code=403, detail="This API key lacks the 'runtime:use' scope")


def _failure(exc: Exception) -> Any:
    if isinstance(exc, FlowError):
        return json_response({"error": {"code": exc.code, "message": exc.message, "status": exc.status, "details": encode(exc.details)}}, status_code=exc.status)
    if isinstance(exc, FunctionError):
        return json_response({"error": {"code": exc.code, "message": exc.message, "status": exc.status, "details": encode(exc.details)}}, status_code=exc.status)
    return json_response({"error": {"code": "runtime_error", "message": f"{type(exc).__name__}: {exc}", "status": 500}}, status_code=500)


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


class _Transactions:
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
            raise FlowError("That transaction is not open (it ended, timed out, or belongs to another process).", status=409, code="no_transaction")
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


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}/runtime"
    transactions = _Transactions()

    async def runtime_for(ctx: HttpContext, env: str, as_user: dict[str, Any] | None, branch: str | None) -> ApiRuntime:
        _scope(ctx)
        await get_environment(env)
        state = await platform.state(env)
        auth = as_user or {"authenticated": True, "kind": "service", "user_id": "pawabase-cli", "roles": ["service"]}
        return ApiRuntime(platform, state, auth=auth, request_id=ctx.headers.get("x-request-id"), branch=branch)

    @r.post(f"{base}/call", auth=MANAGE, tags=["runtime"], request_model=CallBody, summary="Call one runtime capability")
    async def call(ctx: HttpContext, env: str, body: CallBody):
        if body.method not in METHODS:
            raise HTTPException(status_code=404, detail=f"{body.method!r} is not a runtime method")
        if body.method == "secret":
            # Reading a secret's value is a step beyond using the runtime: a key meant for emulating need not be able to read every secret.
            context = current_context(ctx)
            if context is not None and context.is_service and not context.allows_scope("secrets:read"):
                raise HTTPException(status_code=403, detail="This API key lacks the 'secrets:read' scope")
        runtime = await runtime_for(ctx, env, body.as_user, body.branch)
        try:
            result = await getattr(runtime, body.method)(*decode(body.args), **decode(body.kwargs))
        except Exception as exc:  # noqa: BLE001 - the caller gets the failure, typed
            return _failure(exc)
        if body.method == "secret":
            await audit(ctx, "runtime.secret_read", env=env, target=str(body.args[0] if body.args else ""))
        return {"result": encode(result), "logs": runtime.logs[-20:] if body.method == "log" else []}

    @r.post(f"{base}/identify", auth=MANAGE, tags=["runtime"], request_model=IdentifyBody, summary="Who a user access token belongs to")
    async def identify(ctx: HttpContext, env: str, body: IdentifyBody):
        """Verify a user's access token *here* (the platform holds the signing secret) and return the ``auth`` context policies and functions see.

        The emulator calls this for every request it serves locally, so a function under emulation sees the same ``ctx.auth`` as when deployed, and a forged
        or expired token is simply anonymous, never trusted.
        """
        _scope(ctx)
        await get_environment(env)
        if not body.token:
            return {"auth": dict(ANONYMOUS_POLICY_CONTEXT)}
        try:
            claims = verify_user_token(body.token, platform.settings.jwt_master_secret, env=env)
        except TokenInvalid:
            return {"auth": dict(ANONYMOUS_POLICY_CONTEXT), "reason": "invalid_token"}
        return {"auth": Principal("user", claims).as_policy_context()}

    @r.post(f"{base}/db", auth=MANAGE, tags=["runtime"], request_model=DbBody, summary="Run SQL, optionally inside a transaction")
    async def db(ctx: HttpContext, env: str, body: DbBody):
        if body.op not in DB_OPS:
            raise HTTPException(status_code=422, detail=f"{body.op!r} is not a database operation")
        runtime = await runtime_for(ctx, env, None, None)
        try:
            if body.op == "begin":
                return {"tx": await transactions.begin(runtime), "expires_in": TRANSACTION_SECONDS}
            if body.op in ("commit", "rollback"):
                await transactions.finish(body.tx or "", commit=body.op == "commit")
                return {"result": True}
            params = decode(body.params)

            async def work(session: Any) -> Any:
                if body.op == "fetch":
                    return await session.fetch(body.sql or "", params)
                if body.op == "one":
                    return await session.one(body.sql or "", params)
                if body.op == "scalar":
                    return await session.scalar(body.sql or "", params, decode(body.default))
                if body.op == "execute":
                    return await session.execute(body.sql or "", params)
                if body.op == "insert":
                    return await session.insert(body.table or "", decode(body.data or {}))
                if body.op == "update":
                    return await session.update(body.table or "", decode(body.id), decode(body.data or {}))
                return await session.delete(body.table or "", decode(body.id))

            result = await (transactions.get(body.tx).run(work) if body.tx else work(await runtime.db()))
        except Exception as exc:  # noqa: BLE001
            return _failure(exc)
        return {"result": encode(result)}
