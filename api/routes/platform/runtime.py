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

from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo import json as json_response
from sillo.exceptions import HTTPException

from app.platform import Platform
from app.runtime import ApiRuntime
from app.runtime_rpc import (
    DB_OPS,
    METHODS,
    Transactions,
    failure_payload,
    serve_call,
    serve_db,
)
from pawabase_core.context import current_context
from pawabase_core.principal import ANONYMOUS_POLICY_CONTEXT, Principal
from pawabase_core.tokens import TokenInvalid, verify_user_token
from routes.common import MANAGE, audit, get_environment


class CallBody(BaseModel):
    method: str
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    as_user: dict[str, Any] | None = None
    branch: str | None = None


class IdentifyBody(BaseModel):
    token: str | None = Field(
        default=None, description="A user's access token, or nothing for an anonymous caller"
    )


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
    status, payload = failure_payload(exc)
    return json_response(payload, status_code=status)


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}/runtime"
    transactions = Transactions()

    async def runtime_for(
        ctx: HttpContext, env: str, as_user: dict[str, Any] | None, branch: str | None
    ) -> ApiRuntime:
        _scope(ctx)
        await get_environment(env)
        state = await platform.state(env)
        auth = as_user or {
            "authenticated": True,
            "kind": "service",
            "user_id": "pawabase-cli",
            "roles": ["service"],
        }
        return ApiRuntime(
            platform, state, auth=auth, request_id=ctx.headers.get("x-request-id"), branch=branch
        )

    @r.post(
        f"{base}/call",
        auth=MANAGE,
        tags=["runtime"],
        request_model=CallBody,
        summary="Call one runtime capability",
    )
    async def call(ctx: HttpContext, env: str, body: CallBody):
        if body.method not in METHODS:
            raise HTTPException(status_code=404, detail=f"{body.method!r} is not a runtime method")
        if body.method == "secret":
            # Reading a secret's value is a step beyond using the runtime: a key meant for emulating need not be able to read every secret.
            context = current_context(ctx)
            if (
                context is not None
                and context.is_service
                and not context.allows_scope("secrets:read")
            ):
                raise HTTPException(
                    status_code=403, detail="This API key lacks the 'secrets:read' scope"
                )
        runtime = await runtime_for(ctx, env, body.as_user, body.branch)
        try:
            result = await serve_call(runtime, body.method, body.args, body.kwargs)
        except Exception as exc:  # noqa: BLE001 - the caller gets the failure, typed
            return _failure(exc)
        if body.method == "secret":
            await audit(
                ctx, "runtime.secret_read", env=env, target=str(body.args[0] if body.args else "")
            )
        return {
            "result": result,
            "logs": runtime.logs[-20:] if body.method == "log" else [],
        }

    @r.post(
        f"{base}/identify",
        auth=MANAGE,
        tags=["runtime"],
        request_model=IdentifyBody,
        summary="Who a user access token belongs to",
    )
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

    @r.post(
        f"{base}/db",
        auth=MANAGE,
        tags=["runtime"],
        request_model=DbBody,
        summary="Run SQL, optionally inside a transaction",
    )
    async def db(ctx: HttpContext, env: str, body: DbBody):
        if body.op not in DB_OPS:
            raise HTTPException(status_code=422, detail=f"{body.op!r} is not a database operation")
        runtime = await runtime_for(ctx, env, None, None)
        try:
            return await serve_db(runtime, transactions, body.op, body.model_dump())
        except Exception as exc:  # noqa: BLE001
            return _failure(exc)
