"""Operating on an environment's data from Studio.

Resource schema migration, record browsing and editing, and a database console.
Studio acts with service rights. Every write still goes through the same
side effects as the public API (cache invalidation, events, realtime).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app.data import inspect as db_inspect
from app.data.inspect import is_read_only
from app.data.sql import SqlError
from app.data.store import Filter, parse_filters, parse_sort
from app.openapi_scope import add_apikey_security
from app.platform import Platform
from app.resources import after_write, resource_tag
from pawabase_core.context import PlatformContext
from pawabase_core.schemas import compile_model
from routes.common import MANAGE, actor, audit


class QueryBody(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    params: list[Any] = Field(default_factory=list)
    allow_write: bool = False


class ImportBody(BaseModel):
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=2000)
    #: ``insert`` always adds; ``upsert`` updates the row whose key is given, and adds the rest.
    mode: Literal["insert", "upsert"] = "insert"
    #: Check every row and say what would happen, writing nothing.
    dry_run: bool = False
    #: Run the resource's events, flows and realtime messages for each row. A bulk import usually should not.
    emit_events: bool = False


EXPORT_DEFAULT = 50_000
EXPORT_MAX = 200_000


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'row'}: {item['msg']}"
        for item in error.errors()
    )


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    @r.post(
        f"{base}/resources/{{name}}/migrate",
        auth=MANAGE,
        tags=["resources"],
        summary="Create or extend the resource's table",
    )
    async def migrate(ctx: HttpContext, env: str, name: str):
        state = await platform.state(env)
        store = await state.store(name)
        try:
            statements = await store.migrate()
        except SqlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(
            ctx,
            "resource.migrated",
            env=env,
            target=name,
            details={"statements": statements},
        )
        return {"resource": name, "table": store.spec.table, "statements": statements}

    @r.get(
        f"{base}/resources/{{name}}/records",
        auth=MANAGE,
        tags=["resources"],
        summary="Browse records",
    )
    async def browse(ctx: HttpContext, env: str, name: str):
        state = await platform.state(env)
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

    @r.get(
        f"{base}/resources/{{name}}/export",
        auth=MANAGE,
        tags=["resources"],
        summary="Every record of a resource, for a file download",
    )
    async def export_records(ctx: HttpContext, env: str, name: str):
        state = await platform.state(env)
        store = await state.store(name)
        limit = max(1, min(int(ctx.query_params.get("limit", EXPORT_DEFAULT)), EXPORT_MAX))
        rows: list[dict[str, Any]] = []
        total = 0
        try:
            while len(rows) < limit:
                batch, total = await store.list(
                    filters=[],
                    sort=parse_sort(None, store.spec),
                    limit=min(500, limit - len(rows)),
                    offset=len(rows),
                )
                if not batch:
                    break
                rows.extend(batch)
        except SqlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "resource": name,
            "columns": store.spec.columns,
            "data": rows,
            "total": total,
            "truncated": total > len(rows),
        }

    @r.post(
        f"{base}/resources/{{name}}/import",
        auth=MANAGE,
        tags=["resources"],
        request_model=ImportBody,
        summary="Add or update records from parsed rows",
    )
    async def import_records(ctx: HttpContext, env: str, name: str, body: ImportBody):
        state = await platform.state(env)
        store = await state.store(name)
        model = compile_model(
            f"{name}Import", store.spec.fields, mode="create", registry=state.compiled_schemas
        )
        key_name = store.spec.primary_key
        report: dict[str, Any] = {"created": 0, "updated": 0, "failed": 0, "errors": [], "rows": []}

        def fail(index: int, why: str) -> None:
            report["failed"] += 1
            if len(report["errors"]) < 100:
                report["errors"].append({"row": index + 1, "error": why})

        for index, raw in enumerate(body.rows):
            row = {k: v for k, v in raw.items() if v is not None}
            key = row.get(key_name)
            existing = None
            if body.mode == "upsert" and key is not None:
                try:
                    existing = await store.get(key)
                except SqlError:
                    existing = None
            try:
                checked = model.model_validate({k: v for k, v in row.items() if k != key_name})
            except ValidationError as exc:
                # An update only has to be valid for what it sets, so only required fields of a new row can fail it.
                if existing is None:
                    fail(index, _problems(exc))
                    continue
                checked = None
            data = row
            if checked is not None:
                full = checked.model_dump(mode="json")
                data = {
                    k: v for k, v in full.items() if k in checked.model_fields_set or v is not None
                }
                if key is not None:
                    data[key_name] = key
            if existing is not None:
                data = {k: v for k, v in data.items() if k != key_name}
            change = "updated" if existing is not None else "created"
            if body.dry_run:
                report[change] += 1
                continue
            try:
                record = (
                    await store.update(existing[key_name], data)
                    if existing is not None
                    else await store.create(data)
                )
            except SqlError as exc:
                fail(index, str(exc))
                continue
            except Exception as exc:  # a database constraint, a duplicate key
                fail(index, str(exc).splitlines()[0][:300])
                continue
            report[change] += 1
            if body.emit_events and record is not None:
                await after_write(platform, state, name, change, record, actor=actor(ctx))
        if not body.dry_run and (report["created"] or report["updated"]):
            if not body.emit_events:
                await platform.cache_invalidate(state, [resource_tag(name)])
            await audit(
                ctx,
                "resource.imported",
                env=env,
                target=name,
                details={
                    "created": report["created"],
                    "updated": report["updated"],
                    "failed": report["failed"],
                },
            )
        report["dry_run"] = body.dry_run
        report.pop("rows")
        return report

    @r.post(
        f"{base}/resources/{{name}}/records",
        auth=MANAGE,
        tags=["resources"],
        summary="Insert a record",
    )
    async def insert(ctx: HttpContext, env: str, name: str):
        state = await platform.state(env)
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
        auth=MANAGE,
        tags=["resources"],
        summary="Update a record",
    )
    async def modify(ctx: HttpContext, env: str, name: str, record_id: str):
        state = await platform.state(env)
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
        auth=MANAGE,
        tags=["resources"],
        summary="Delete a record",
    )
    async def remove(ctx: HttpContext, env: str, name: str, record_id: str):
        state = await platform.state(env)
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
        auth=MANAGE,
        tags=["resources"],
        summary="The environment's whole compiled OpenAPI document",
    )
    async def environment_openapi(ctx: HttpContext, env: str):
        # Studio reads the docs whether or not ``public_docs`` publishes them
        # at /docs/v1; that setting only decides what anonymous callers see.
        version = ctx.query_params.get("version", "v1")
        state = await platform.state_for_version(PlatformContext(env=env, role="service"), version)
        spec = json.loads((await state.compiled()).build_openapi(f"/rest/{version}"))
        return add_apikey_security(spec)

    @r.get(
        f"{base}/resources/{{name}}/openapi",
        auth=MANAGE,
        tags=["resources"],
        summary="The resource's compiled routes",
    )
    async def compiled_routes(ctx: HttpContext, env: str, name: str):
        version = ctx.query_params.get("version", "v1")
        state = await platform.state_for_version(PlatformContext(env=env, role="service"), version)
        document = json.loads((await state.compiled()).build_openapi(f"/rest/{version}"))
        prefix = f"/rest/{version}/{name}"
        return {
            "paths": {
                path: spec
                for path, spec in document.get("paths", {}).items()
                if path == prefix or path.startswith(prefix + "/")
            }
        }

    # ── database console ─────────────────────────────────────────────────

    @r.get(f"{base}/database", auth=MANAGE, tags=["database"], summary="Database overview")
    async def database_overview(ctx: HttpContext, env: str):
        state = await platform.state(env)
        source = await state.source()
        tables = await db_inspect.list_tables(source)
        managed = {spec.table: name for name, spec in state.specs.items()}
        return {
            "dialect": source.dialect,
            "configured": state.database_source() != "default",
            "tables": [{"name": table, "resource": managed.get(table)} for table in tables],
        }

    @r.get(
        f"{base}/database/tables/{{table}}",
        auth=MANAGE,
        tags=["database"],
        summary="Describe a table",
    )
    async def describe(ctx: HttpContext, env: str, table: str):
        state = await platform.state(env)
        source = await state.source()
        if table not in await db_inspect.list_tables(source):
            raise HTTPException(status_code=404, detail=f"no table {table!r}")
        description = await db_inspect.describe_table(source, table)
        rows = await source.fetch(f"SELECT COUNT(*) AS total FROM {_quoted(source.dialect, table)}")
        description["rows"] = int(rows[0]["total"]) if rows else 0
        return description

    @r.get(
        f"{base}/database/tables/{{table}}/rows",
        auth=MANAGE,
        tags=["database"],
        summary="Browse a table",
    )
    async def table_rows(ctx: HttpContext, env: str, table: str):
        state = await platform.state(env)
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
        auth=MANAGE,
        tags=["database"],
        request_model=QueryBody,
        summary="Run SQL",
    )
    async def run_query(ctx: HttpContext, env: str, body: QueryBody):
        state = await platform.state(env)
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
            await audit(ctx, "database.write", env=env, details={"sql": body.sql[:2000]})
            await platform.cache_invalidate(state, [f"resource:{name}" for name in state.specs])
        return result


def _quoted(dialect: str, table: str) -> str:
    from app.data.sql import quote

    return quote(dialect, table)


__all__ = ["Filter", "register"]
