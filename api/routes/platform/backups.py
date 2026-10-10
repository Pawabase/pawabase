"""Backing up an environment to a file, and restoring from one."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException
from sillo.responses import JSONResponse

from app import backups, snapshots
from app.platform import Platform
from database.models import BackupSchedule, BackupSnapshot
from routes.common import MANAGE, audit, dump, get_environment


class RestoreRequest(BaseModel):
    backup: dict[str, Any] = Field(description="The document a backup produced.")
    include: list[str] | None = Field(
        default=None,
        description="Parts to restore: definitions, settings, users, data. Default: every part the file holds.",
    )
    strategy: Literal["merge", "replace"] = Field(
        default="merge",
        description="merge adds and updates; replace first removes what the chosen parts hold.",
    )
    dry_run: bool = Field(
        default=False, description="Report what would be restored; change nothing."
    )
    confirm: str | None = Field(
        default=None, description="For replace: the environment's name, typed out."
    )


class SnapshotBody(BaseModel):
    name: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=1000)
    include: list[str] | None = None


class SnapshotPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=1000)
    pinned: bool | None = None


class SnapshotRestore(BaseModel):
    include: list[str] | None = None
    strategy: Literal["merge", "replace"] = "merge"
    dry_run: bool = False
    confirm: str | None = None
    safety_snapshot: bool = Field(
        default=True, description="Take a snapshot of the environment as it is, first."
    )


class ScheduleBody(BaseModel):
    enabled: bool = False
    frequency: Literal["hourly", "daily", "weekly"] = "daily"
    hour: int = Field(default=3, ge=0, le=23)
    weekday: int = Field(default=0, ge=0, le=6)
    keep_last: int = Field(default=7, ge=0, le=365)
    keep_days: int = Field(default=30, ge=0, le=3650)
    include: list[str] = Field(default_factory=list)


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    @r.get(
        f"{base}/backup",
        auth=MANAGE,
        tags=["backups"],
        summary="Download a backup of the environment",
    )
    async def download(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        try:
            parts = backups.parse_parts(ctx.query_params.get("include"))
            document = await backups.create_backup(platform, environment, parts)
        except backups.BackupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(
            ctx,
            "backup.created",
            env=env,
            target=env,
            details={"parts": parts, "summary": backups.summarise(document)},
        )
        stamp = document["created_at"][:19].replace(":", "-")
        return JSONResponse(
            document,
            headers={
                "Content-Disposition": f'attachment; filename="{env}-{stamp}.pawabase-backup.json"'
            },
        )

    @r.post(
        f"{base}/restore",
        auth=MANAGE,
        tags=["backups"],
        request_model=RestoreRequest,
        summary="Restore an environment from a backup",
    )
    async def restore(ctx: HttpContext, env: str, body: RestoreRequest):
        environment = await get_environment(env)
        try:
            available = backups.verify(body.backup)
            parts = backups.parse_parts(body.include) if body.include else available
        except backups.BackupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        missing = [part for part in parts if part not in available]
        if missing:
            raise HTTPException(
                status_code=422, detail=f"the backup does not contain: {', '.join(missing)}"
            )
        replace = body.strategy == "replace"
        if body.dry_run:
            return {
                "dry_run": True,
                "strategy": body.strategy,
                "would_restore": parts,
                "contents": backups.summarise(body.backup),
                "source": body.backup.get("source", {}),
            }
        if replace and body.confirm != env:
            raise HTTPException(
                status_code=422,
                detail=f"replace removes what the chosen parts hold; set confirm to {env!r} to proceed",
            )
        try:
            report = await backups.restore_backup(
                platform, environment, body.backup, parts, replace=replace
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=422, detail=f"the backup is malformed: missing {exc}"
            ) from exc
        await audit(
            ctx,
            "backup.restored",
            env=env,
            target=env,
            details={
                "parts": parts,
                "strategy": body.strategy,
                "from": body.backup.get("source", {}),
            },
        )
        return report

    async def find(env: str, snapshot_id: str) -> BackupSnapshot:
        row = await BackupSnapshot.get_or_none(id=snapshot_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such snapshot")
        return row

    @r.get(
        f"{base}/snapshots", auth=MANAGE, tags=["backups"], summary="Saved backups and the schedule"
    )
    async def list_snapshots(ctx: HttpContext, env: str):
        await get_environment(env)
        schedule = await BackupSchedule.get_or_none(env=env)
        rows = await BackupSnapshot.filter(env=env).order_by("-id").limit(200)
        return {
            "data": [dump(row) for row in rows],
            "schedule": dump(schedule) if schedule else ScheduleBody().model_dump() | {"env": env},
            "stored_bytes": sum(row.size_bytes for row in rows),
        }

    @r.post(
        f"{base}/snapshots",
        auth=MANAGE,
        tags=["backups"],
        request_model=SnapshotBody,
        summary="Take a snapshot now",
    )
    async def take_snapshot(ctx: HttpContext, env: str, body: SnapshotBody):
        await get_environment(env)
        try:
            row = await snapshots.take(
                platform, env, include=body.include, name=body.name, note=body.note
            )
        except backups.BackupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(ctx, "snapshot.taken", env=env, target=row.id, details={"status": row.status})
        return created(dump(row))

    @r.patch(
        f"{base}/snapshots/{{snapshot_id}}",
        auth=MANAGE,
        tags=["backups"],
        request_model=SnapshotPatch,
        summary="Rename, annotate or pin a snapshot",
    )
    async def patch_snapshot(ctx: HttpContext, env: str, snapshot_id: str, body: SnapshotPatch):
        row = await find(env, snapshot_id)
        for key, value in body.model_dump(exclude_none=True).items():
            setattr(row, key, value)
        await row.save()
        return dump(row)

    @r.delete(
        f"{base}/snapshots/{{snapshot_id}}",
        auth=MANAGE,
        tags=["backups"],
        summary="Delete a snapshot",
    )
    async def delete_snapshot(ctx: HttpContext, env: str, snapshot_id: str):
        row = await find(env, snapshot_id)
        await snapshots.remove(platform, row)
        await audit(ctx, "snapshot.deleted", env=env, target=snapshot_id)
        return no_content()

    @r.post(
        f"{base}/snapshots/{{snapshot_id}}/verify",
        auth=MANAGE,
        tags=["backups"],
        summary="Read a snapshot back and check it",
    )
    async def verify_snapshot(ctx: HttpContext, env: str, snapshot_id: str):
        return await snapshots.check(platform, await find(env, snapshot_id))

    @r.get(
        f"{base}/snapshots/{{snapshot_id}}/download",
        auth=MANAGE,
        tags=["backups"],
        summary="Download a snapshot as a backup file",
    )
    async def download_snapshot(ctx: HttpContext, env: str, snapshot_id: str):
        row = await find(env, snapshot_id)
        try:
            document = await snapshots.read(platform, row)
        except snapshots.SnapshotError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        stamp = row.created_at.strftime("%Y-%m-%dT%H-%M-%S")
        return JSONResponse(
            document,
            headers={
                "Content-Disposition": f'attachment; filename="{env}-{stamp}.pawabase-backup.json"'
            },
        )

    @r.post(
        f"{base}/snapshots/{{snapshot_id}}/restore",
        auth=MANAGE,
        tags=["backups"],
        request_model=SnapshotRestore,
        summary="Restore the environment from a snapshot",
    )
    async def restore_snapshot(ctx: HttpContext, env: str, snapshot_id: str, body: SnapshotRestore):
        environment = await get_environment(env)
        row = await find(env, snapshot_id)
        try:
            document = await snapshots.read(platform, row)
            available = backups.verify(document)
            parts = backups.parse_parts(body.include) if body.include else available
        except (snapshots.SnapshotError, backups.BackupError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        missing = [part for part in parts if part not in available]
        if missing:
            raise HTTPException(
                status_code=422, detail=f"the snapshot does not contain: {', '.join(missing)}"
            )
        replace = body.strategy == "replace"
        if body.dry_run:
            return {
                "dry_run": True,
                "strategy": body.strategy,
                "would_restore": parts,
                "contents": backups.summarise(document),
            }
        if replace and body.confirm != env:
            raise HTTPException(
                status_code=422,
                detail=f"replace removes what the chosen parts hold; set confirm to {env!r} to proceed",
            )
        safety = None
        if body.safety_snapshot:
            safety = await snapshots.take(
                platform,
                env,
                include=parts,
                trigger="before-restore",
                name=f"Before restoring {row.name}",
            )
            if safety.status != "complete":
                raise HTTPException(
                    status_code=502,
                    detail=f"the safety snapshot failed, so nothing was restored: {safety.error}",
                )
        try:
            report = await backups.restore_backup(
                platform, environment, document, parts, replace=replace
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=422, detail=f"the snapshot is malformed: missing {exc}"
            ) from exc
        await audit(
            ctx,
            "snapshot.restored",
            env=env,
            target=snapshot_id,
            details={"parts": parts, "strategy": body.strategy},
        )
        return {**report, "safety_snapshot": safety.id if safety else None}

    @r.put(
        f"{base}/backup-schedule",
        auth=MANAGE,
        tags=["backups"],
        request_model=ScheduleBody,
        summary="Set the automatic backup schedule",
    )
    async def put_schedule(ctx: HttpContext, env: str, body: ScheduleBody):
        await get_environment(env)
        try:
            include = backups.parse_parts(body.include) if body.include else []
        except backups.BackupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        values = {**body.model_dump(), "include": include}
        schedule = await BackupSchedule.get_or_none(env=env)
        if schedule is None:
            schedule = await BackupSchedule.create(env=env, **values)
        else:
            for key, value in values.items():
                setattr(schedule, key, value)
            await schedule.save()
        await audit(
            ctx, "backup_schedule.saved", env=env, target=env, details={"enabled": body.enabled}
        )
        return dump(schedule)
