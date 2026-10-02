"""The capabilities flows and functions call, backed by Sillo.

:class:`ApiRuntime` is the API's implementation of
:class:`pawabase_core.flows.Runtime`. Each method lands on a platform service:
Resource stores for data, ``sillo.cache`` for caching, ``sillo.events`` for
events, ``sillo.work`` for jobs, Angula for realtime, ``sillo.storage`` for
files, ``sillo.mail`` for email, and the environment's secrets.

Operations made through the runtime are trusted: flows and functions are the
developer's server-side code. A flow that must enforce a caller's rights does
so explicitly with ``policy.check`` or ``auth.require``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sillo.storage import chunks, collect

from app.data.inspect import is_read_only
from app.data.store import Filter
from app.platform import Platform
from app.state import EnvironmentState
from pawabase_core.flows import BaseRuntime, FlowError, NotAvailable
from pawabase_core.flows.engine import observable
from pawabase_core.functions import FunctionContext
from pawabase_core.policies.engine import evaluate

logger = logging.getLogger("pawabase.flow")


class ApiRuntime(BaseRuntime):
    """Runtime for one environment and one caller."""

    def __init__(
        self,
        platform: Platform,
        state: EnvironmentState,
        *,
        auth: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        depth: int = 0,
        branch: str | None = None,
    ) -> None:
        self.platform = platform
        self.branch = branch
        self.state = state
        self.auth = dict(auth or {})
        self.request_id = request_id
        self.depth = depth
        self.logs: list[dict[str, Any]] = []

    # ── resources ────────────────────────────────────────────────────────

    async def resource_list(self, resource, *, filters=None, sort=None, limit=50, offset=0):
        from app.data.store import parse_sort

        store = await self.state.store(resource)
        parsed = [Filter(column, "eq", value) for column, value in (filters or {}).items()]
        rows, total = await store.list(
            filters=parsed, sort=parse_sort(sort, store.spec), limit=limit, offset=offset
        )
        return {"data": rows, "total": total, "limit": limit, "offset": offset}

    async def resource_get(self, resource, record_id):
        return await (await self.state.store(resource)).get(record_id)

    async def resource_create(self, resource, data):
        from app.resources import after_write

        store = await self.state.store(resource)
        record = await store.create(dict(data))
        await after_write(
            self.platform,
            self.state,
            resource,
            "created",
            record,
            actor=self.auth.get("user_id"),
            request_id=self.request_id,
        )
        return record

    async def resource_update(self, resource, record_id, data):
        from app.resources import after_write

        store = await self.state.store(resource)
        record = await store.update(record_id, dict(data))
        if record is not None:
            await after_write(
                self.platform,
                self.state,
                resource,
                "updated",
                record,
                actor=self.auth.get("user_id"),
                request_id=self.request_id,
            )
        return record

    async def resource_delete(self, resource, record_id):
        from app.resources import after_write

        store = await self.state.store(resource)
        existing = await store.get(record_id)
        deleted = await store.delete(record_id)
        if deleted and existing is not None:
            await after_write(
                self.platform,
                self.state,
                resource,
                "deleted",
                existing,
                actor=self.auth.get("user_id"),
                request_id=self.request_id,
            )
        return deleted

    async def db_query(self, sql, params=None):
        if not is_read_only(sql):
            raise FlowError(
                "db.query only runs single read-only statements; use resource blocks or a transaction to write",
                status=400,
                code="write_refused",
            )
        source = await self.state.source()
        return await asyncio.wait_for(
            source.fetch(sql, list(params or [])), timeout=self.platform.settings.query_timeout
        )

    async def db(self):
        """A SQL session on the environment's database (see :class:`app.data.session.DbSession`)."""
        from app.data.session import DbSession

        return DbSession(await self.state.source(), self.state.specs)

    def transaction(self):
        """``async with runtime.transaction() as db``: one atomic unit of SQL."""
        from app.data.session import Transaction

        return Transaction(self.state)

    async def db_transaction(self, operations):
        from app.resources import after_write

        source = await self.state.source()
        results = []
        effects = []
        async with source.transaction() as client:
            for operation in operations:
                op = operation.get("op")
                store = await self.state.store(operation["resource"])
                if op == "create":
                    record = await store.create(operation.get("data") or {}, client=client)
                    effects.append((operation["resource"], "created", record))
                elif op == "update":
                    record = await store.update(
                        operation["id"], operation.get("data") or {}, client=client
                    )
                    if record is None:
                        raise FlowError(
                            f"{operation['resource']} {operation['id']} not found",
                            status=404,
                            code="not_found",
                        )
                    effects.append((operation["resource"], "updated", record))
                elif op == "delete":
                    record = await store.delete(operation["id"], client=client)
                else:
                    raise FlowError(f"unknown transaction operation {op!r}", code="bad_config")
                results.append(record)
        for resource, change, record in effects:  # only once committed
            await after_write(
                self.platform,
                self.state,
                resource,
                change,
                record,
                actor=self.auth.get("user_id"),
                request_id=self.request_id,
            )
        return results

    # ── cache ────────────────────────────────────────────────────────────

    async def cache_get(self, key):
        return await self.platform.cache_get(self.state, f"user:{key}")

    async def cache_set(self, key, value, ttl=None, tags=None):
        await self.platform.cache_set(self.state, f"user:{key}", value, ttl=ttl, tags=tags)

    async def cache_delete(self, key):
        return await self.platform.cache.delete(self.platform.cache_key(self.state, f"user:{key}"))

    async def cache_invalidate(self, tags):
        return await self.platform.cache_invalidate(self.state, list(tags))

    # ── events, jobs, realtime ───────────────────────────────────────────

    async def emit(self, name, payload):
        return await self.platform.emit(
            self.state, name, payload, actor=self.auth.get("user_id"), request_id=self.request_id
        )

    async def dispatch_flow(self, flow, input, *, delay=0, queue=None):
        from app.jobs.flows import RunFlowJob

        if flow not in self.state.flows:
            raise FlowError(f"no flow {flow!r}", code="unknown_flow")
        return await self.platform.dispatch(
            RunFlowJob,
            project=self.state.project_ref,
            env=self.state.env_name,
            queue=queue,
            delay=delay,
            target=flow,
            flow=flow,
            input=input,
            trigger="job",
            auth=self.auth,
            request_id=self.request_id,
            release_id=self.state.release_id,
            api_version=self.state.api_version,
        )

    async def call_flow(self, flow, input):
        """Run a named flow inline, preserving the caller's execution context."""
        if self.depth >= 8:
            raise FlowError("flow calls are nested too deeply", code="too_deep")
        if flow not in self.state.flows:
            raise FlowError(f"no flow {flow!r}", code="unknown_flow")
        from app.execution import run_flow

        child = await run_flow(
            self.platform,
            self.state,
            flow,
            input,
            trigger="flow",
            auth=self.auth,
            credential={"is_service": True, "role": "service"},
            request_id=self.request_id,
            depth=self.depth + 1,
        )
        return child.result()

    async def dispatch_function(self, function, input, *, delay=0, queue=None):
        from app.jobs.functions import RunFunctionJob

        if self.platform.function_spec(self.state.project_ref, self.state.env_name, function, self.branch) is None:
            raise FlowError(f"no function {function!r}", code="unknown_function")
        return await self.platform.dispatch(
            RunFunctionJob,
            project=self.state.project_ref,
            env=self.state.env_name,
            queue=queue,
            delay=delay,
            target=function,
            function=function,
            branch=self.branch,
            input=input,
            trigger="job",
            auth=self.auth,
            request_id=self.request_id,
            release_id=self.state.release_id,
            api_version=self.state.api_version,
        )

    async def publish(self, channel, event, payload):
        return await self.platform.publish(self.state, channel, event, payload)

    # ── storage and mail ─────────────────────────────────────────────────

    def _bucket(self, bucket):
        return self.platform.storage.bucket(
            self.state, bucket, credential={"is_service": True, "role": "service", "scopes": []}
        )

    async def storage_put(self, bucket, key, content, content_type=""):
        stored = await self._bucket(bucket).put(
            key, chunks(content), content_type=content_type, signed=True
        )
        return {
            "bucket": bucket,
            "key": stored.key,
            "size": stored.size,
            "content_type": stored.content_type,
        }

    async def storage_read(self, bucket, key, limit=1_048_576):
        return await collect(self._bucket(bucket).get(key, signed=True), limit=limit)

    async def storage_signed_url(self, bucket, key, method="GET", expires_in=300):
        return self._bucket(bucket).signed_url(key, method=method, expires_in=expires_in)

    async def storage_delete(self, bucket, key):
        return await self._bucket(bucket).delete(key, signed=True)

    async def send_mail(self, to, subject, *, text=None, html=None, template=None, data=None):
        from app.jobs.mail import SendMailJob

        job_id = await self.platform.dispatch(
            SendMailJob,
            project=self.state.project_ref,
            env=self.state.env_name,
            target=",".join(to),
            to=list(to),
            subject=subject,
            text=text,
            html=html,
            template=template,
            data=dict(data or {}),
            release_id=self.state.release_id,
            api_version=self.state.api_version,
        )
        return {"queued": True, "job_id": job_id}

    # ── outbound ─────────────────────────────────────────────────────────

    async def http_request(
        self, method, url, *, headers=None, json=None, params=None, timeout=30.0, retries=0
    ):
        from app.outbound import request_with_retries

        return await request_with_retries(
            self.platform.outbound,
            method,
            url,
            headers=headers,
            json_body=json,
            params=params,
            timeout=timeout,
            retries=retries,
        )

    async def webhook_send(self, event, payload):
        from app.webhooks import queue_deliveries

        return await queue_deliveries(
            self.platform,
            self.state,
            event_id=f"manual-{int(time.time() * 1000)}",
            event=event,
            payload=payload,
        )

    # ── platform ─────────────────────────────────────────────────────────

    async def secret(self, name):
        return self.state.secret_values.get(name)

    async def call_function(self, name, input):
        if self.depth > 8:
            raise FlowError("function calls are nested too deeply", code="too_deep")
        from app.execution import call_function

        return await call_function(
            self.platform,
            self.state,
            name,
            input,
            trigger="flow",
            auth=self.auth,
            depth=self.depth + 1,
            branch=self.branch,
        )

    async def identity_user(self, user_id):
        from pawabase_core.clients import ServiceError
        from pawabase_core.context import PlatformContext

        context = PlatformContext(
            project=self.state.project_ref, env=self.state.env_name, role="service"
        )
        try:
            return await self.platform.akountz.get(f"/admin/v1/users/{user_id}", context=context)
        except ServiceError as exc:
            if exc.status == 404:
                return None
            raise

    async def check_policy(self, ref, context):
        decision = await self.state.engine.check(ref, context)
        return decision.allowed

    async def log(self, level, message, data=None):
        from pawabase_core.telemetry import note

        entry = observable(
            data
            if isinstance(data, dict) and data.get("message") == message
            else {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": level,
                "message": message,
                "data": data,
                "request_id": self.request_id,
            }
        )
        self.logs.append(entry)
        note("logs", entry, append=True)
        logger.log(
            {
                "debug": logging.DEBUG,
                "warning": logging.WARNING,
                "error": logging.ERROR,
            }.get(str(level).lower(), logging.INFO),
            message,
            extra={"pawabase": entry},
        )

    async def metric(self, name, value=1.0, tags=None):
        from app.metrics import increment

        await increment(self.state.project_ref, self.state.env_name, name, value, tags or {})


def function_context(
    runtime: ApiRuntime, input: Any, trigger: str, request: Mapping[str, Any] | None = None
) -> FunctionContext:
    return FunctionContext(
        request=request,
        input=input,
        auth=runtime.auth,
        project=runtime.state.project_ref,
        env=runtime.state.env_name,
        runtime=runtime,
        trigger=trigger,
        branch=runtime.branch or "main",
    )


def matches_condition(condition: Any, context: Mapping[str, Any]) -> bool:
    if condition in (None, {}, True):
        return True
    return evaluate(condition, context)


__all__ = ["ApiRuntime", "NotAvailable", "function_context", "json", "matches_condition"]
