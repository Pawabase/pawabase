"""Platform blocks: each one is a thin call into the host runtime.

In the API those calls land on Sillo capabilities: Record connections for
resources, ``sillo.cache`` for caching, ``sillo.events`` for events,
``sillo.work.queue`` for jobs, ``sillo.storage`` buckets, ``sillo.mail``, and
Sillo's HTTP client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..engine import FlowError, FlowResponse, observable
from ..registry import Block, BlockResult

# ── auth and policies ──────────────────────────────────────────────────────


class RequireAuth(Block):
    """Fails with 401 unless the caller is signed in (optionally with a role)."""

    key = "auth.require"
    title = "Require sign-in"
    category = "auth"
    config = [{"name": "role", "type": "string"}, {"name": "permission", "type": "string"}]

    async def run(self, config, run):
        auth = run.state.get("auth") or {}
        if not auth.get("authenticated"):
            raise FlowError("Authentication required", status=401, code="unauthenticated")
        if config.get("role") and config["role"] not in (auth.get("roles") or []):
            raise FlowError("Forbidden", status=403, code="missing_role")
        perms = auth.get("permissions") or []
        if config.get("permission") and config["permission"] not in perms and "*" not in perms:
            raise FlowError("Forbidden", status=403, code="missing_permission")
        return BlockResult(output=auth)


class CurrentUser(Block):
    """Loads the calling user's full profile from Akountz."""

    key = "auth.user"
    title = "Current user"
    category = "auth"
    handles = ["next", "anonymous"]

    async def run(self, config, run):
        auth = run.state.get("auth") or {}
        if not auth.get("authenticated"):
            return BlockResult(output=None, handle="anonymous")
        return BlockResult(output=await run.runtime.identity_user(str(auth.get("user_id"))))


class LookupUser(Block):
    """Loads any user of this project from Akountz by id."""

    key = "auth.lookup"
    title = "Look up user"
    category = "auth"
    handles = ["next", "missing"]
    config = [{"name": "user_id", "type": "string", "required": True}]

    async def run(self, config, run):
        user = await run.runtime.identity_user(str(config.get("user_id")))
        return BlockResult(output=user, handle="next" if user else "missing")


class CheckPolicy(Block):
    """Evaluates a policy and follows ``allowed`` or ``denied``."""

    key = "policy.check"
    title = "Check policy"
    category = "auth"
    handles = ["allowed", "denied"]
    config = [
        {
            "name": "policy",
            "type": "json",
            "required": True,
            "description": "Policy name or inline condition",
        },
        {"name": "record", "type": "json"},
        {"name": "input", "type": "json"},
    ]

    async def run(self, config, run):
        context = {
            "auth": run.state.get("auth") or {},
            "credential": run.state.get("credential") or {"is_service": False},
            "env": run.state.get("env"),
            "record": config.get("record"),
            "input": config.get("input"),
        }
        allowed = await run.runtime.check_policy(config.get("policy"), context)
        return BlockResult(output=allowed, handle="allowed" if allowed else "denied")


# ── resources and the database ─────────────────────────────────────────────


class ResourceList(Block):
    """Lists records of a resource."""

    key = "resource.list"
    title = "List records"
    category = "resources"
    config = [
        {"name": "resource", "type": "string", "required": True},
        {"name": "filters", "type": "json", "description": "Equality filters, {field: value}"},
        {"name": "sort", "type": "string", "description": "e.g. -created_at"},
        {"name": "limit", "type": "integer", "default": 50},
        {"name": "offset", "type": "integer", "default": 0},
    ]

    async def run(self, config, run):
        page = await run.runtime.resource_list(
            config["resource"],
            filters=config.get("filters") or {},
            sort=config.get("sort"),
            limit=int(config.get("limit") or 50),
            offset=int(config.get("offset") or 0),
        )
        return BlockResult(output=page)


class ResourceGet(Block):
    """Loads one record; follows ``missing`` when it does not exist."""

    key = "resource.get"
    title = "Get record"
    category = "resources"
    handles = ["next", "missing"]
    config = [
        {"name": "resource", "type": "string", "required": True},
        {"name": "id", "type": "string", "required": True},
    ]

    async def run(self, config, run):
        record = await run.runtime.resource_get(config["resource"], config.get("id"))
        return BlockResult(output=record, handle="next" if record is not None else "missing")


class ResourceCreate(Block):
    """Creates a record."""

    key = "resource.create"
    title = "Create record"
    category = "resources"
    config = [
        {"name": "resource", "type": "string", "required": True},
        {"name": "data", "type": "json", "required": True},
    ]

    async def run(self, config, run):
        return BlockResult(
            output=await run.runtime.resource_create(config["resource"], config.get("data") or {})
        )


class ResourceUpdate(Block):
    """Updates fields of a record; follows ``missing`` when it does not exist."""

    key = "resource.update"
    title = "Update record"
    category = "resources"
    handles = ["next", "missing"]
    config = [
        {"name": "resource", "type": "string", "required": True},
        {"name": "id", "type": "string", "required": True},
        {"name": "data", "type": "json", "required": True},
    ]

    async def run(self, config, run):
        record = await run.runtime.resource_update(
            config["resource"], config.get("id"), config.get("data") or {}
        )
        return BlockResult(output=record, handle="next" if record is not None else "missing")


class ResourceDelete(Block):
    """Deletes a record."""

    key = "resource.delete"
    title = "Delete record"
    category = "resources"
    config = [
        {"name": "resource", "type": "string", "required": True},
        {"name": "id", "type": "string", "required": True},
    ]

    async def run(self, config, run):
        return BlockResult(
            output=await run.runtime.resource_delete(config["resource"], config.get("id"))
        )


class DatabaseQuery(Block):
    """Runs a parameterised read-only SQL query on the project database."""

    key = "db.query"
    title = "SQL query"
    category = "resources"
    config = [
        {
            "name": "sql",
            "type": "text",
            "required": True,
            "widget": "code",
            "description": "SELECT … with ? or $1 placeholders",
        },
        {"name": "params", "type": "json", "description": "List of parameter values"},
    ]

    async def run(self, config, run):
        rows = await run.runtime.db_query(config["sql"], list(config.get("params") or []))
        return BlockResult(output=rows)


class DatabaseTransaction(Block):
    """Applies several resource writes atomically.

    ``operations`` is a list of ``{"op": "create"|"update"|"delete", "resource":
    …, "id": …, "data": {…}}``. Either every write lands or none does.
    """

    key = "db.transaction"
    title = "Transaction"
    category = "resources"
    config = [{"name": "operations", "type": "json", "required": True}]

    async def run(self, config, run):
        operations = config.get("operations") or []
        if not isinstance(operations, list) or not operations:
            raise FlowError("a transaction needs a list of operations", code="bad_config")
        return BlockResult(output=await run.runtime.db_transaction(operations))


# ── cache ──────────────────────────────────────────────────────────────────


class CacheGet(Block):
    """Reads the cache; follows ``hit`` or ``miss``."""

    key = "cache.get"
    title = "Cache get"
    category = "cache"
    handles = ["hit", "miss"]
    config = [{"name": "key", "type": "string", "required": True}]

    async def run(self, config, run):
        value = await run.runtime.cache_get(config["key"])
        return BlockResult(output=value, handle="miss" if value is None else "hit")


class CacheSet(Block):
    """Writes a value to the cache."""

    key = "cache.set"
    title = "Cache set"
    category = "cache"
    config = [
        {"name": "key", "type": "string", "required": True},
        {"name": "value", "type": "json", "required": True},
        {"name": "ttl", "type": "integer", "default": 300},
        {"name": "tags", "type": "array", "items": {"type": "string"}},
    ]

    async def run(self, config, run):
        await run.runtime.cache_set(
            config["key"], config.get("value"), ttl=config.get("ttl"), tags=config.get("tags")
        )
        return BlockResult(output=config.get("value"))


class CacheDelete(Block):
    """Removes a key from the cache."""

    key = "cache.delete"
    title = "Cache delete"
    category = "cache"
    config = [{"name": "key", "type": "string", "required": True}]

    async def run(self, config, run):
        return BlockResult(output=await run.runtime.cache_delete(config["key"]))


class CacheInvalidate(Block):
    """Invalidates every cached entry carrying any of the tags."""

    key = "cache.invalidate"
    title = "Cache invalidate tags"
    category = "cache"
    config = [{"name": "tags", "type": "array", "items": {"type": "string"}, "required": True}]

    async def run(self, config, run):
        return BlockResult(
            output=await run.runtime.cache_invalidate(list(config.get("tags") or []))
        )


# ── events, jobs, realtime ─────────────────────────────────────────────────


class EmitEvent(Block):
    """Publishes a platform event."""

    key = "event.emit"
    title = "Emit event"
    category = "events"
    config = [
        {"name": "event", "type": "string", "required": True},
        {"name": "payload", "type": "json"},
    ]

    async def run(self, config, run):
        return BlockResult(output=await run.runtime.emit(config["event"], config.get("payload")))


class DispatchFlow(Block):
    """Runs another flow as a background job."""

    key = "queue.flow"
    title = "Queue flow"
    category = "jobs"
    config = [
        {"name": "flow", "type": "string", "required": True},
        {"name": "input", "type": "json"},
        {"name": "delay", "type": "integer", "default": 0},
        {"name": "queue", "type": "string"},
    ]

    async def run(self, config, run):
        job_id = await run.runtime.dispatch_flow(
            config["flow"],
            config.get("input"),
            delay=int(config.get("delay") or 0),
            queue=config.get("queue"),
        )
        return BlockResult(output={"job_id": job_id})


class CallFlow(Block):
    """Calls another flow inline and continues with its result."""

    key = "flow.call"
    title = "Call flow"
    category = "jobs"
    config = [
        {"name": "flow", "type": "string", "required": True},
        {"name": "input", "type": "json"},
    ]

    async def run(self, config, run):
        return BlockResult(output=await run.runtime.call_flow(config["flow"], config.get("input")))


class DispatchFunction(Block):
    """Runs a Python function as a background job."""

    key = "queue.function"
    title = "Queue function"
    category = "jobs"
    config = [
        {"name": "function", "type": "string", "required": True},
        {"name": "input", "type": "json"},
        {"name": "delay", "type": "integer", "default": 0},
        {"name": "queue", "type": "string"},
    ]

    async def run(self, config, run):
        job_id = await run.runtime.dispatch_function(
            config["function"],
            config.get("input"),
            delay=int(config.get("delay") or 0),
            queue=config.get("queue"),
        )
        return BlockResult(output={"job_id": job_id})


class Publish(Block):
    """Publishes a message to a realtime channel (through Angula)."""

    key = "realtime.publish"
    title = "Publish to channel"
    category = "realtime"
    config = [
        {"name": "channel", "type": "string", "required": True},
        {"name": "event", "type": "string", "default": "message"},
        {"name": "payload", "type": "json"},
    ]

    async def run(self, config, run):
        return BlockResult(
            output=await run.runtime.publish(
                config["channel"], config.get("event") or "message", config.get("payload")
            )
        )


# ── storage and mail ───────────────────────────────────────────────────────


class StoragePut(Block):
    """Writes text or JSON to a bucket."""

    key = "storage.put"
    title = "Store file"
    category = "storage"
    config = [
        {"name": "bucket", "type": "string", "required": True},
        {"name": "key", "type": "string", "required": True},
        {"name": "content", "type": "json", "required": True},
        {"name": "content_type", "type": "string", "default": "text/plain"},
    ]

    async def run(self, config, run):
        import json

        content = config.get("content")
        if not isinstance(content, (str, bytes)):
            content = json.dumps(content, default=str)
        data = content.encode() if isinstance(content, str) else content
        return BlockResult(
            output=await run.runtime.storage_put(
                config["bucket"], config["key"], data, config.get("content_type") or ""
            )
        )


class StorageRead(Block):
    """Reads a (small) file from a bucket as text."""

    key = "storage.read"
    title = "Read file"
    category = "storage"
    config = [
        {"name": "bucket", "type": "string", "required": True},
        {"name": "key", "type": "string", "required": True},
    ]

    async def run(self, config, run):
        data = await run.runtime.storage_read(config["bucket"], config["key"])
        return BlockResult(output=data.decode("utf-8", "replace"))


class StorageSignedUrl(Block):
    """Creates a signed URL for downloading or uploading one object."""

    key = "storage.signed_url"
    title = "Signed URL"
    category = "storage"
    config = [
        {"name": "bucket", "type": "string", "required": True},
        {"name": "key", "type": "string", "required": True},
        {"name": "method", "type": "string", "enum": ["GET", "PUT"], "default": "GET"},
        {"name": "expires_in", "type": "integer", "default": 300},
    ]

    async def run(self, config, run):
        url = await run.runtime.storage_signed_url(
            config["bucket"],
            config["key"],
            config.get("method") or "GET",
            int(config.get("expires_in") or 300),
        )
        return BlockResult(output=url)


class StorageDelete(Block):
    """Deletes an object from a bucket."""

    key = "storage.delete"
    title = "Delete file"
    category = "storage"
    config = [
        {"name": "bucket", "type": "string", "required": True},
        {"name": "key", "type": "string", "required": True},
    ]

    async def run(self, config, run):
        return BlockResult(output=await run.runtime.storage_delete(config["bucket"], config["key"]))


class SendMail(Block):
    """Sends an email with the environment's mail settings."""

    key = "mail.send"
    title = "Send email"
    category = "mail"
    config = [
        {
            "name": "to",
            "type": "json",
            "required": True,
            "description": "Address or list of addresses",
        },
        {"name": "subject", "type": "string"},
        {"name": "text", "type": "text"},
        {"name": "html", "type": "text"},
        {"name": "template", "type": "string", "description": "Stored mail template name"},
        {"name": "data", "type": "json"},
    ]

    async def run(self, config, run):
        to = config.get("to")
        recipients = to if isinstance(to, list) else [to]
        result = await run.runtime.send_mail(
            [str(r) for r in recipients if r],
            config.get("subject") or "",
            text=config.get("text"),
            html=config.get("html"),
            template=config.get("template"),
            data=config.get("data") or {},
        )
        return BlockResult(output=result)


# ── outbound ───────────────────────────────────────────────────────────────


class HttpRequest(Block):
    """Calls an external HTTP API through Sillo's HTTP client."""

    key = "http.request"
    title = "HTTP request"
    category = "integrations"
    handles = ["next", "failed"]
    config = [
        {
            "name": "method",
            "type": "string",
            "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            "default": "GET",
        },
        {"name": "url", "type": "url", "required": True},
        {"name": "headers", "type": "json"},
        {"name": "params", "type": "json"},
        {"name": "body", "type": "json"},
        {"name": "timeout", "type": "number", "default": 30},
        {"name": "retries", "type": "integer", "default": 0, "maximum": 5},
    ]

    async def run(self, config, run):
        response = await run.runtime.http_request(
            config.get("method") or "GET",
            config["url"],
            headers=config.get("headers") or {},
            json=config.get("body"),
            params=config.get("params") or {},
            timeout=float(config.get("timeout") or 30),
            retries=int(config.get("retries") or 0),
        )
        ok = 200 <= int(response.get("status", 0)) < 400
        return BlockResult(output=response, handle="next" if ok else "failed")


class SendWebhook(Block):
    """Delivers an event to every outbound webhook subscribed to it."""

    key = "webhook.send"
    title = "Send webhook"
    category = "integrations"
    config = [
        {"name": "event", "type": "string", "required": True},
        {"name": "payload", "type": "json"},
    ]

    async def run(self, config, run):
        queued = await run.runtime.webhook_send(config["event"], config.get("payload"))
        return BlockResult(output={"deliveries": queued})


class GetSecret(Block):
    """Reads a secret into the run (never into its trace)."""

    key = "secret.get"
    title = "Read secret"
    category = "integrations"
    config = [
        {"name": "name", "type": "string", "required": True},
        {"name": "as", "type": "string", "description": "Store as vars.<name>"},
    ]

    async def run(self, config, run):
        value = await run.runtime.secret(config["name"])
        if value is None:
            raise FlowError(f"secret {config['name']!r} is not set", code="missing_secret")
        run.state["vars"][config.get("as") or config["name"]] = value
        return BlockResult(output={"name": config["name"], "loaded": True})


class CallFunction(Block):
    """Calls a Python function inline and continues with its result."""

    key = "function.call"
    title = "Call function"
    category = "code"
    config = [
        {"name": "function", "type": "string", "required": True},
        {"name": "input", "type": "json"},
    ]

    async def run(self, config, run):
        return BlockResult(
            output=await run.runtime.call_function(config["function"], config.get("input"))
        )


# ── observability ──────────────────────────────────────────────────────────


class Log(Block):
    """Writes a line to the run's log (visible in Studio)."""

    key = "log.write"
    title = "Log"
    category = "observability"
    config = [
        {
            "name": "level",
            "type": "string",
            "enum": ["debug", "info", "warning", "error"],
            "default": "info",
        },
        {"name": "message", "type": "string", "required": True},
        {"name": "data", "type": "json"},
        {
            "name": "category",
            "type": "string",
            "description": "Stable area such as billing, checkout, or authorization",
        },
        {
            "name": "code",
            "type": "string",
            "description": "Stable machine-readable event code",
        },
        {"name": "tags", "type": "json", "description": "Searchable key/value labels"},
    ]

    async def run(self, config, run):
        run_context = run.state.get("run") or {}
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "sequence": len(run.logs) + 1,
            "level": config.get("level") or "info",
            "message": str(config.get("message") or ""),
            "category": config.get("category") or "flow",
            "code": config.get("code"),
            "tags": observable(config.get("tags") or {}),
            "data": observable(config.get("data")),
            "node": run.current_node,
            "block": self.key,
            "run_id": run_context.get("id"),
            "flow": run_context.get("flow"),
            "request_id": run_context.get("request_id"),
        }
        run.logs.append(entry)
        await run.runtime.log(entry["level"], entry["message"], entry)
        return BlockResult(output=entry)


class Metric(Block):
    """Increments a named metric."""

    key = "metric.increment"
    title = "Metric"
    category = "observability"
    config = [
        {"name": "name", "type": "string", "required": True},
        {"name": "value", "type": "number", "default": 1},
        {"name": "tags", "type": "json"},
    ]

    async def run(self, config, run):
        value = float(config.get("value") if config.get("value") is not None else 1)
        await run.runtime.metric(config["name"], value, config.get("tags") or {})
        return BlockResult(output=value)


# ── responses and errors ───────────────────────────────────────────────────


class Respond(Block):
    """Answers the HTTP request that triggered the run, and ends it."""

    key = "response.return"
    title = "Respond"
    category = "responses"
    handles = []
    config = [
        {"name": "status", "type": "integer", "default": 200, "minimum": 100, "maximum": 599},
        {"name": "body", "type": "json"},
        {"name": "headers", "type": "json"},
    ]

    async def run(self, config, run):
        headers: dict[str, Any] = config.get("headers") or {}
        run.response = FlowResponse(
            status=int(config.get("status") or 200),
            body=config.get("body"),
            headers={str(k): str(v) for k, v in headers.items()},
        )
        return BlockResult(output=config.get("body"), stop=True)


class RaiseError(Block):
    """Fails the run with an HTTP status and message."""

    key = "error.raise"
    title = "Fail"
    category = "responses"
    handles = []
    config = [
        {"name": "status", "type": "integer", "default": 400, "minimum": 400, "maximum": 599},
        {"name": "message", "type": "string", "required": True},
        {"name": "code", "type": "string", "default": "error"},
    ]

    async def run(self, config, run):
        error = FlowError(
            str(config.get("message") or "error"),
            status=int(config.get("status") or 400),
            code="raised",
        )
        error.details = {"code": config.get("code") or "error"}
        raise error


BLOCKS = [
    RequireAuth,
    CurrentUser,
    LookupUser,
    CheckPolicy,
    ResourceList,
    ResourceGet,
    ResourceCreate,
    ResourceUpdate,
    ResourceDelete,
    DatabaseQuery,
    DatabaseTransaction,
    CacheGet,
    CacheSet,
    CacheDelete,
    CacheInvalidate,
    EmitEvent,
    DispatchFlow,
    CallFlow,
    DispatchFunction,
    Publish,
    StoragePut,
    StorageRead,
    StorageSignedUrl,
    StorageDelete,
    SendMail,
    HttpRequest,
    SendWebhook,
    GetSecret,
    CallFunction,
    Log,
    Metric,
    Respond,
    RaiseError,
]
