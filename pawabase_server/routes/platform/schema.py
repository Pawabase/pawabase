"""A resource's table against its definition: what is not applied yet, the history of changes, and undoing them."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo.exceptions import HTTPException

from pawabase_server.data import schema
from pawabase_server.db.models import SchemaMigration
from pawabase_server.platform import Platform
from pawabase_server.routes.common import MANAGE, actor, audit


class ApplyBody(BaseModel):
    #: Change ids from the plan (``add_column:title``). Omitted: every change that only adds.
    changes: list[str] | None = None
    #: The resource's name, required when a change retires or rewrites values.
    confirm: str | None = None


class AcknowledgeBody(BaseModel):
    #: Why the baseline is being taken, for the history (``"Imported from an existing table"``).
    note: str | None = Field(default=None, max_length=200)


class PurgeBody(BaseModel):
    confirm: str = Field(min_length=1)


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    @r.get(
        f"{base}/schema",
        auth=MANAGE,
        tags=["schema"],
        summary="Every resource's table against its definition",
    )
    async def overview(ctx: HttpContext, env: str):
        state = await platform.state(env)
        source = await state.source()
        resources = []
        for name in sorted(state.specs):
            store = await state.store(name)
            report = await schema.status(env, source, store.spec)
            last = (
                await SchemaMigration.filter(env=env, resource=name, kind="apply", status="applied")
                .order_by("-number")
                .first()
            )
            report["last_applied"] = (
                {
                    "number": last.number,
                    "summary": last.summary,
                    "at": last.created_at.isoformat() if last.created_at else None,
                    "actor": last.actor,
                }
                if last
                else None
            )
            resources.append(report)
        return {
            "resources": resources,
            "pending": sum(
                1 for item in resources if item["state"] in ("pending", "no_table", "review")
            ),
        }

    @r.get(
        f"{base}/resources/{{name}}/schema",
        auth=MANAGE,
        tags=["schema"],
        summary="What applying the definition would change",
    )
    async def plan(ctx: HttpContext, env: str, name: str):
        state = await platform.state(env)
        store = await state.store(name)
        return await schema.status(env, store.source, store.spec)

    @r.post(
        f"{base}/resources/{{name}}/schema/apply",
        auth=MANAGE,
        tags=["schema"],
        request_model=ApplyBody,
        summary="Apply the definition to the table",
    )
    async def apply(ctx: HttpContext, env: str, name: str, body: ApplyBody):
        state = await platform.state(env)
        store = await state.store(name)
        if body.changes is not None:
            preview = await schema.status(env, store.source, store.spec)
            wanted = set(body.changes)
            risky = [c for c in preview["changes"] if c["id"] in wanted and not c["safe"]]
            if risky and body.confirm != name:
                raise HTTPException(
                    status_code=422,
                    detail=f"these changes retire or rewrite values; send confirm: {name!r} to apply them",
                )
        try:
            migration = await schema.apply(
                env,
                store.source,
                store.spec,
                select=body.changes,
                allow_destructive=body.confirm == name,
                actor=actor(ctx),
                origin="studio",
            )
        except schema.SchemaError as exc:
            if exc.migration is not None:
                await audit(
                    ctx,
                    "resource.migration_failed",
                    env=env,
                    target=name,
                    details={"error": str(exc)},
                )
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if migration is None:
            return {
                "applied": False,
                "message": "The table already matches the definition.",
                "migration": None,
            }
        await audit(
            ctx,
            "resource.migrated",
            env=env,
            target=name,
            details={"migration": migration.number, "statements": migration.up},
        )
        return {
            "applied": True,
            "migration": {
                "id": migration.id,
                "number": migration.number,
                "summary": migration.summary,
            },
            "statements": migration.up,
        }

    @r.get(
        f"{base}/resources/{{name}}/migrations",
        auth=MANAGE,
        tags=["schema"],
        summary="The history of changes to the table",
    )
    async def migrations(ctx: HttpContext, env: str, name: str):
        state = await platform.state(env)
        store = await state.store(name)
        return {"data": await schema.history(env, name, store.source, store.spec)}

    @r.post(
        f"{base}/resources/{{name}}/migrations/{{migration_id}}/rollback",
        auth=MANAGE,
        tags=["schema"],
        summary="Undo a migration",
    )
    async def roll_back(ctx: HttpContext, env: str, name: str, migration_id: str):
        state = await platform.state(env)
        store = await state.store(name)
        try:
            entry = await schema.roll_back(
                env, store.source, store.spec, migration_id, actor=actor(ctx)
            )
        except schema.SchemaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(
            ctx,
            "resource.rolled_back",
            env=env,
            target=name,
            details={"migration": migration_id, "statements": entry.up},
        )
        return {"rolled_back": migration_id, "migration": {"id": entry.id, "number": entry.number}}

    @r.post(
        f"{base}/resources/{{name}}/schema/purge",
        auth=MANAGE,
        tags=["schema"],
        request_model=PurgeBody,
        summary="Remove what migrations kept for a rollback",
    )
    async def purge(ctx: HttpContext, env: str, name: str, body: PurgeBody):
        if body.confirm != name:
            raise HTTPException(
                status_code=422, detail=f"send confirm: {name!r} to remove the kept data for good"
            )
        state = await platform.state(env)
        store = await state.store(name)
        try:
            removed = await schema.purge(store.source, store.spec)
        except schema.SchemaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(ctx, "resource.purged", env=env, target=name, details={"removed": removed})
        return {"removed": removed}

    @r.get(
        f"{base}/schema/tables",
        auth=MANAGE,
        tags=["schema"],
        summary="Tables in the database that no resource is defined for",
    )
    async def tables(ctx: HttpContext, env: str):
        state = await platform.state(env)
        source = await state.source()
        found = await schema.unclaimed_tables(source, list(state.specs.values()))
        out = []
        for table in found:
            try:
                definition, skipped = schema.definition_from_table(table, source.dialect)
            except schema.SchemaError as exc:
                out.append({**table, "definition": None, "problem": str(exc), "skipped": []})
                continue
            out.append({**table, "definition": definition, "problem": None, "skipped": skipped})
        return {"data": out}

    @r.post(
        f"{base}/resources/{{name}}/schema/acknowledge",
        auth=MANAGE,
        tags=["schema"],
        request_model=AcknowledgeBody,
        summary="Take the table as it is now as the starting point, and put what changed on the record",
    )
    async def acknowledge(ctx: HttpContext, env: str, name: str, body: AcknowledgeBody):
        state = await platform.state(env)
        store = await state.store(name)
        report = await schema.status(env, store.source, store.spec)
        if not report["exists"]:
            raise HTTPException(status_code=422, detail="the table does not exist yet")
        entry = await schema.record_external(
            env,
            store.source,
            store.spec,
            report["external"],
            actor=actor(ctx),
            origin="import" if body.note else "external",
            summary=body.note,
        )
        await audit(
            ctx, "resource.acknowledged", env=env, target=name, details={"migration": entry.number}
        )
        return {"migration": {"id": entry.id, "number": entry.number}}
