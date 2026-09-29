"""Operating on an environment's data from Studio.

Resource schema migration, record browsing and editing, and a database console.
Operators act with service rights. Every write still goes through the same
side effects as the public API (cache invalidation, events, realtime).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app.data import inspect as db_inspect
from app.data.inspect import is_read_only
from app.data.sql import SqlError
from app.data.store import Filter, parse_filters, parse_sort
from app.openapi_scope import add_apikey_security
from app.platform import Platform
from app.resources import after_write
from routes.common import OPERATOR, actor, audit


class QueryBody(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    params: list[Any] = Field(default_factory=list)
    allow_write: bool = False


def register(r: Router, platform: Platform) -> None:
    base = "/projects/{ref}/envs/{env}"

    @r.post(
        f"{base}/resources/{{name}}/migrate",
        auth=OPERATOR,
        tags=["resources"],
        summary="Create or extend the resource's table",
    )
    async def migrate(ctx: HttpContext, ref: str, env: str, name: str):
        state = await platform.state(ref, env)
        store = await state.store(name)
        try:
            statements = await store.migrate()
        except SqlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(
            ctx,
            "resource.migrated",
            project=ref,
            env=env,
            target=name,
            details={"statements": statements},
        )
        return {"resource": name, "table": store.spec.table, "statements": statements}

    @r.get(
        f"{base}/resources/{{name}}/records",
        auth=OPERATOR,
        tags=["resources"],
        summary="Browse records",
    )
    async def browse(ctx: HttpContext, ref: str, env: str, name: str):
        state = await platform.state(ref, env)
        store = await state.store(name)
        q = ctx.query_params
        page = max(1, int(q.get("page", 1)))
        per_page = max(1, min(int(q.get("per_page", 50)), 200))
        try:
            rows, total = await store.list(
                filters=parse_filters(q, store.spec),
                sort=parse_sort(q.get("sort"), store.spec),
                limit=per_page,
                offset=(page - 1) * per_page,
            )
        except SqlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"data": rows, "page": page, "per_page": per_page, "total": total}

    @r.post(
        f"{base}/resources/{{name}}/records",
        auth=OPERATOR,
        tags=["resources"],
        summary="Insert a record",
    )
    async def insert(ctx: HttpContext, ref: str, env: str, name: str):
        state = await platform.state(ref, env)
        store = await state.store(name)
        data = await ctx.json
        try:
            record = await store.create(data or {})
        except SqlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await after_write(platform, state, name, "created", record, actor=actor(ctx))
        return created(record)

    @r.patch(
        f"{base}/resources/{{name}}/records/{{record_id}}",
        auth=OPERATOR,
        tags=["resources"],
        summary="Update a record",
    )
    async def modify(ctx: HttpContext, ref: str, env: str, name: str, record_id: str):
        state = await platform.state(ref, env)
        store = await state.store(name)
        key: Any = (
            int(record_id) if store.spec.id_type == "integer" and record_id.isdigit() else record_id
        )
        try:
            record = await store.update(key, await ctx.json or {})
        except SqlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="no such record")
        await after_write(platform, state, name, "updated", record, actor=actor(ctx))
        return record

    @r.delete(
        f"{base}/resources/{{name}}/records/{{record_id}}",
        auth=OPERATOR,
        tags=["resources"],
        summary="Delete a record",
    )
    async def remove(ctx: HttpContext, ref: str, env: str, name: str, record_id: str):
        state = await platform.state(ref, env)
        store = await state.store(name)
        key: Any = (
            int(record_id) if store.spec.id_type == "integer" and record_id.isdigit() else record_id
        )
        existing = await store.get(key)
        if existing is None:
            raise HTTPException(status_code=404, detail="no such record")
        await store.delete(key)
        await after_write(platform, state, name, "deleted", existing, actor=actor(ctx))
        return no_content()

    @r.get(
        f"{base}/openapi",
        auth=OPERATOR,
        tags=["resources"],
        summary="The environment's whole compiled OpenAPI document",
    )
    async def environment_openapi(ctx: HttpContext, ref: str, env: str):
        # Operators read the docs whether or not ``public_docs`` publishes them
        # at /docs/v1; that setting only decides what anonymous callers see.
        state = await platform.state(ref, env)
        spec = json.loads((await state.compiled()).build_openapi("/rest/v1"))
        return add_apikey_security(spec)

    @r.get(
        f"{base}/resources/{{name}}/openapi",
        auth=OPERATOR,
        tags=["resources"],
        summary="The resource's compiled routes",
    )
    async def compiled_routes(ctx: HttpContext, ref: str, env: str, name: str):
        state = await platform.state(ref, env)
        document = json.loads((await state.compiled()).build_openapi("/rest/v1"))
        prefix = f"/rest/v1/{name}"
        return {
            "paths": {
                path: spec
                for path, spec in document.get("paths", {}).items()
                if path == prefix or path.startswith(prefix + "/")
            }
        }

    # ── database console ─────────────────────────────────────────────────

    @r.get(f"{base}/database", auth=OPERATOR, tags=["database"], summary="Database overview")
    async def database_overview(ctx: HttpContext, ref: str, env: str):
        state = await platform.state(ref, env)
        source = await state.source()
        tables = await db_inspect.list_tables(source)
        managed = {spec.table: name for name, spec in state.specs.items()}
        return {
            "dialect": source.dialect,
            "configured": bool(state.infra.get("database_url")),
            "tables": [{"name": table, "resource": managed.get(table)} for table in tables],
        }

    @r.get(
        f"{base}/database/tables/{{table}}",
        auth=OPERATOR,
        tags=["database"],
        summary="Describe a table",
    )
    async def describe(ctx: HttpContext, ref: str, env: str, table: str):
        state = await platform.state(ref, env)
        source = await state.source()
        if table not in await db_inspect.list_tables(source):
            raise HTTPException(status_code=404, detail=f"no table {table!r}")
        description = await db_inspect.describe_table(source, table)
        rows = await source.fetch(f"SELECT COUNT(*) AS total FROM {_quoted(source.dialect, table)}")
        description["rows"] = int(rows[0]["total"]) if rows else 0
        return description

    @r.get(
        f"{base}/database/tables/{{table}}/rows",
        auth=OPERATOR,
        tags=["database"],
        summary="Browse a table",
    )
    async def table_rows(ctx: HttpContext, ref: str, env: str, table: str):
        state = await platform.state(ref, env)
        source = await state.source()
        if table not in await db_inspect.list_tables(source):
            raise HTTPException(status_code=404, detail=f"no table {table!r}")
        limit = max(1, min(int(ctx.query_params.get("limit", 50)), 500))
        offset = max(0, int(ctx.query_params.get("offset", 0)))
        placeholder = {"postgres": "$1", "mysql": "%s"}.get(source.dialect, "?")
        second = {"postgres": "$2", "mysql": "%s"}.get(source.dialect, "?")
        rows = await source.fetch(
            f"SELECT * FROM {_quoted(source.dialect, table)} LIMIT {placeholder} OFFSET {second}",
            [limit, offset],
        )
        from app.data.sql import decode_value

        return {
            "data": [{k: decode_value(None, v) for k, v in row.items()} for row in rows],
            "limit": limit,
            "offset": offset,
        }

    @r.post(
        f"{base}/database/query",
        auth=OPERATOR,
        tags=["database"],
        request_model=QueryBody,
        summary="Run SQL",
    )
    async def run_query(ctx: HttpContext, ref: str, env: str, body: QueryBody):
        state = await platform.state(ref, env)
        source = await state.source()
        read_only = is_read_only(body.sql)
        if not read_only and not body.allow_write:
            raise HTTPException(
                status_code=400, detail="this statement writes; set allow_write to run it"
            )
        from app.data.sql import decode_value

        try:
            if read_only:
                rows = await asyncio.wait_for(
                    source.fetch(body.sql, body.params), timeout=platform.settings.query_timeout
                )
                result = {
                    "rows": [
                        {k: decode_value(None, v) for k, v in row.items()} for row in rows[:5000]
                    ],
                    "truncated": len(rows) > 5000,
                }
            else:
                affected = await asyncio.wait_for(
                    source.execute(body.sql, body.params), timeout=platform.settings.query_timeout
                )
                result = {"rows": [], "affected": affected}
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="the query timed out") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"{type(exc).__name__}: {exc}") from exc
        if not read_only:
            await audit(
                ctx, "database.write", project=ref, env=env, details={"sql": body.sql[:2000]}
            )
            await platform.cache_invalidate(state, [f"resource:{name}" for name in state.specs])
        return result


def _quoted(dialect: str, table: str) -> str:
    from app.data.sql import quote

    return quote(dialect, table)


__all__ = ["Filter", "register"]
