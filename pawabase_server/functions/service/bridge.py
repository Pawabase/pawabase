"""Answering a worker's ``ctx.runtime`` calls.

A worker asks for platform capabilities (``resource_get``, ``emit``, ``secret``, SQL…) by name. A **bridge** answers those calls for one
invocation:

* :class:`LocalBridge` runs them on the invocation's :class:`~pawabase_server.functions.runtime.ApiRuntime`, in this process (the
  runtime embedded in the server);
* :class:`RemoteBridge` sends them to the server's internal listener with the invocation's grant (the runtime service), and the server
  runs them on an ``ApiRuntime`` it builds from that grant.

Both keep track of the database transactions the invocation opened, refuse one it did not open, and roll back whatever it left open
when it ends.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from pawabase_server.flows import FlowError
from pawabase_server.functions.runtime_rpc import (
    METHODS,
    Transactions,
    failure_payload,
    serve_call,
    serve_db,
)

if TYPE_CHECKING:
    from pawabase_server.functions.runtime import ApiRuntime

#: What a sandboxed function may do through the runtime: the remote runtime's methods, plus outbound requests (made by the platform,
#: through its guard, because a worker has no network of its own).
ALLOWED = METHODS | {"http_request"}


class BridgeFailure(Exception):
    """A runtime call failed. ``status`` and ``payload`` are what the worker is told (the platform's error shape)."""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("message") or payload.get("error") or f"status {status}")
        self.status = status
        self.payload = payload


class Bridge(Protocol):
    async def call(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        """Run one runtime method. Returns ``{"result": <encoded>}``."""

    async def db(self, op: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Run one SQL operation of ``ctx.runtime.db()`` / ``transaction()``."""

    async def close(self) -> None:
        """The invocation ended: roll back what it left open."""


class LocalBridge:
    """The invocation's own ``ApiRuntime``, in this process."""

    def __init__(self, runtime: ApiRuntime, transactions: Transactions) -> None:
        self.runtime = runtime
        self.transactions = transactions
        self.owned: set[str] = set()

    @property
    def logs(self) -> list[dict[str, Any]]:
        return self.runtime.logs

    async def call(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        return {"result": await serve_call(self.runtime, method, args, kwargs, allowed=ALLOWED)}

    async def db(self, op: str, fields: dict[str, Any]) -> dict[str, Any]:
        tx = fields.get("tx")
        if op != "begin" and tx is not None and tx not in self.owned:
            raise FlowError("That transaction belongs to another call.", status=409, code="no_transaction")
        body = await serve_db(self.runtime, self.transactions, op, fields)
        if op == "begin":
            self.owned.add(body["tx"])
        elif op in ("commit", "rollback"):
            self.owned.discard(tx)
        return body

    async def close(self) -> None:
        for tx in list(self.owned):  # whatever the call left open is not committed
            with contextlib.suppress(Exception):
                await self.transactions.finish(tx, commit=False)
        self.owned.clear()


class RemoteBridge:
    """The server's runtime, reached over its internal listener with the invocation's grant."""

    def __init__(self, client: httpx.AsyncClient, grant: str, *, token: Any) -> None:
        self.client = client
        self.grant = grant
        #: Mints the service token for each request (short-lived, addressed to the server).
        self.token = token
        self.used = False

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.used = True
        try:
            response = await self.client.post(
                path, json={"grant": self.grant, **body}, headers=self.token()
            )
        except httpx.HTTPError as exc:
            raise BridgeFailure(
                503,
                {"error": "runtime_unavailable", "message": f"the platform could not be reached: {exc}", "details": None},
            ) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": "runtime_error", "message": response.text[:500], "details": None}
        if response.status_code >= 400:
            if not isinstance(payload, dict):
                payload = {"error": "runtime_error", "message": str(payload), "details": None}
            raise BridgeFailure(response.status_code, payload)
        return payload

    async def call(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/internal/v1/functions/rpc/call", {"method": method, "args": args, "kwargs": kwargs})

    async def db(self, op: str, fields: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/internal/v1/functions/rpc/db", {"op": op, "fields": fields})

    async def close(self) -> None:
        if not self.used:
            return  # it never reached the platform, so there is nothing to roll back
        with contextlib.suppress(BridgeFailure):
            await self._post("/internal/v1/functions/rpc/finish", {})


def failure_reply(exc: Exception) -> tuple[int, dict[str, Any]]:
    """What a worker is told when a runtime call failed."""
    if isinstance(exc, BridgeFailure):
        return exc.status, exc.payload
    return failure_payload(exc)
