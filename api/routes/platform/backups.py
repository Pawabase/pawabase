"""Backing up an environment to a file, and restoring from one."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo.exceptions import HTTPException
from sillo.responses import JSONResponse

from app import backups
from app.platform import Platform
from routes.common import OPERATOR_ADMIN, audit, get_environment


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
    dry_run: bool = Field(default=False, description="Report what would be restored; change nothing.")
    confirm: str | None = Field(
        default=None, description="For replace: the environment's name, typed out."
    )


def register(r: Router, platform: Platform) -> None:
    base = "/projects/{ref}/envs/{env}"

    @r.get(
        f"{base}/backup",
        auth=OPERATOR_ADMIN,
        tags=["backups"],
        summary="Download a backup of the environment",
    )
    async def download(ctx: HttpContext, ref: str, env: str):
        environment = await get_environment(ref, env)
        try:
            parts = backups.parse_parts(ctx.query_params.get("include"))
            document = await backups.create_backup(platform, environment, parts)
        except backups.BackupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await audit(
            ctx,
            "backup.created",
            project=ref,
            env=env,
            target=env,
            details={"parts": parts, "summary": backups.summarise(document)},
        )
        stamp = document["created_at"][:19].replace(":", "-")
        return JSONResponse(
            document,
            headers={"Content-Disposition": f'attachment; filename="{ref}-{env}-{stamp}.pawabase-backup.json"'},
        )

    @r.post(
        f"{base}/restore",
        auth=OPERATOR_ADMIN,
        tags=["backups"],
        request_model=RestoreRequest,
        summary="Restore an environment from a backup",
    )
    async def restore(ctx: HttpContext, ref: str, env: str, body: RestoreRequest):
        environment = await get_environment(ref, env)
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
            raise HTTPException(status_code=422, detail=f"the backup is malformed: missing {exc}") from exc
        await audit(
            ctx,
            "backup.restored",
            project=ref,
            env=env,
            target=env,
            details={"parts": parts, "strategy": body.strategy, "from": body.backup.get("source", {})},
        )
        return report
