"""What the functions runtime service asks this server for, on its internal listener only (``/internal/`` never passes the gateway).

Every request carries a service token minted by the runtime service (issuer ``functions``) and, except for variables, the grant of the
invocation it is acting for. The server acts exactly as the grant says: its environment, its branch, its caller.

``POST /internal/v1/functions/rpc/call``    one ``ctx.runtime`` method: ``{grant, method, args, kwargs}``
``POST /internal/v1/functions/rpc/db``      one SQL operation: ``{grant, op, fields}``
``POST /internal/v1/functions/rpc/finish``  the invocation is over: roll back what it left open
``POST /internal/v1/functions/report``      a finished HTTP run: ``{grant, report}``
``GET  /internal/v1/functions/variables``   an environment's secrets, for its function workers' environment variables
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo import json as json_response
from sillo.exceptions import HTTPException

from pawabase_server.core.service import SERVICE_ONLY
from pawabase_server.core.tokens import TokenInvalid
from pawabase_server.functions.runtime import ApiRuntime
from pawabase_server.functions.runtime_rpc import failure_payload
from pawabase_server.functions.service import grants
from pawabase_server.functions.service.bridge import LocalBridge
from pawabase_server.functions.service.reports import RunReport
from pawabase_server.functions.service.runtime import variables_version
from pawabase_server.platform import Platform

#: How long an invocation's bridge is kept after its last call when no ``finish`` arrives (the service crashed mid-call).
ABANDONED_SECONDS = 600


class CallBody(BaseModel):
    grant: str
    method: str
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)


class DbBody(BaseModel):
    grant: str
    op: str
    fields: dict[str, Any] = Field(default_factory=dict)


class GrantBody(BaseModel):
    grant: str


class ReportBody(BaseModel):
    grant: str
    report: dict[str, Any]


@dataclass
class _Held:
    bridge: LocalBridge
    grant: grants.Grant
    last_used: float


class InvocationBridges:
    """One bridge per live invocation, so its transactions, logs and identity carry from one runtime call to the next."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.held: dict[str, _Held] = {}
        self._lock = asyncio.Lock()

    def verify(self, token: str) -> grants.Grant:
        try:
            return grants.verify(self.platform.settings.internal_secret, token)
        except TokenInvalid as exc:
            raise HTTPException(status_code=403, detail=f"invalid invocation grant: {exc}") from exc

    async def bridge(self, token: str) -> LocalBridge:
        grant = self.verify(token)
        async with self._lock:
            held = self.held.get(grant.invocation)
            if held is None:
                state = await self.platform.state(grant.env)
                runtime = ApiRuntime(
                    self.platform,
                    state,
                    auth=grant.auth,
                    request_id=grant.request_id,
                    depth=grant.depth,
                    branch=grant.branch,
                )
                held = self.held[grant.invocation] = _Held(
                    LocalBridge(runtime, self.platform.sandbox_transactions), grant, time.monotonic()
                )
            held.last_used = time.monotonic()
            await self._sweep()
            return held.bridge

    async def finish(self, token: str) -> None:
        grant = self.verify(token)
        held = self.held.pop(grant.invocation, None)
        if held is not None:
            await held.bridge.close()

    async def _sweep(self) -> None:
        now = time.monotonic()
        for invocation, held in list(self.held.items()):
            if now - held.last_used > ABANDONED_SECONDS or held.grant.expires_at and held.grant.expires_at < time.time():
                self.held.pop(invocation, None)
                await held.bridge.close()


def register(app: Any, platform: Platform) -> None:
    r = Router(prefix="/internal/v1/functions", tags=["internal"])
    bridges = InvocationBridges(platform)
    app.state["function_bridges"] = bridges

    def _failure(exc: Exception) -> Any:
        status, payload = failure_payload(exc)
        return json_response(payload, status_code=status)

    @r.post("/rpc/call", auth=SERVICE_ONLY, request_model=CallBody, exclude_from_schema=True)
    async def call(ctx: HttpContext, body: CallBody):
        bridge = await bridges.bridge(body.grant)
        try:
            return await bridge.call(body.method, body.args, body.kwargs)
        except Exception as exc:  # noqa: BLE001 - told to the function, typed
            return _failure(exc)

    @r.post("/rpc/db", auth=SERVICE_ONLY, request_model=DbBody, exclude_from_schema=True)
    async def db(ctx: HttpContext, body: DbBody):
        bridge = await bridges.bridge(body.grant)
        try:
            return await bridge.db(body.op, body.fields)
        except Exception as exc:  # noqa: BLE001 - told to the function, typed
            return _failure(exc)

    @r.post("/rpc/finish", auth=SERVICE_ONLY, request_model=GrantBody, exclude_from_schema=True)
    async def finish(ctx: HttpContext, body: GrantBody):
        await bridges.finish(body.grant)
        return {"finished": True}

    @r.post("/report", auth=SERVICE_ONLY, request_model=ReportBody, exclude_from_schema=True)
    async def report(ctx: HttpContext, body: ReportBody):
        grant = bridges.verify(body.grant)
        run = RunReport.from_dict(body.report)
        # The grant, not the service, says what ran: a report cannot be filed for another environment, function or request.
        run.env, run.branch, run.function = grant.env, grant.branch, grant.function
        run.invocation, run.request_id = grant.invocation, grant.request_id
        run.deployment_id = grant.deployment_id or run.deployment_id
        await platform.records.add(run.to_record())
        return {"recorded": True}

    @r.get("/variables", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def variables(ctx: HttpContext):
        env = ctx.query_params.get("env")
        if not env:
            raise HTTPException(status_code=400, detail="env is required")
        state = await platform.state(env)
        values = dict(state.secret_values)
        return {"env": env, "version": variables_version(values), "values": values}

    app.mount_router(r)
