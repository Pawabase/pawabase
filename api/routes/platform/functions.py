"""Deployment control plane for uploaded custom Function source artifacts.

``pawabase deploy`` posts a bundle here. It is activated for one environment and one branch (``main`` unless the CLI says otherwise): a branch's functions
exist beside the environment's own, are reached by naming the branch, and fall back to the environment's functions for everything the branch does not
redefine. See :mod:`app.deployments` for how artifacts are stored and switched.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created
from sillo.exceptions import HTTPException

from app.deployments import DeploymentError
from app.platform import Platform
from database.models import FunctionDeployment, FunctionRun
from pawabase_core.context import current_context
from pawabase_core.functions import MAIN
from routes.common import NAME_PATTERN, OPERATOR, actor, audit, dump, get_environment, page_params


def _manage_scope(ctx: HttpContext) -> None:
    context = current_context(ctx)
    if context is not None and context.is_service and not context.allows_scope("functions:manage"):
        raise HTTPException(status_code=403, detail="This API key lacks the 'functions:manage' scope")


class DeployBody(BaseModel):
    archive: str = Field(description="base64 encoded .tar.gz source bundle")
    branch: str = Field(default=MAIN, pattern=NAME_PATTERN, description="The branch these functions belong to")
    manifest: dict[str, Any] = Field(default_factory=dict, description="Facts about the build: git commit, kit version, the functions found locally")
    runtime: str = Field(default="python3.11", max_length=64)
    limits: dict[str, Any] = Field(default_factory=dict)


def _problem(error: DeploymentError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"message": error.message, "problems": error.problems})


def register(r: Router, platform: Platform) -> None:
    base = "/projects/{ref}/envs/{env}"

    async def activate(ref: str, env: str, deployment: FunctionDeployment, raw: bytes | None) -> FunctionDeployment:
        """Install (or re-install) *deployment*'s artifact and make it the only active one on its branch."""
        try:
            if raw is None:
                code = platform.deployments.reactivate(ref, env, deployment.branch, deployment.id)
            else:
                code = platform.deployments.install(ref, env, deployment.branch, raw, deployment_id=deployment.id, checksum=deployment.checksum)
        except DeploymentError as exc:
            deployment.status, deployment.error = "failed", "; ".join([exc.message, *exc.problems])[:2000]
            await deployment.save(update_fields=["status", "error"])
            raise _problem(exc) from exc
        await FunctionDeployment.filter(environment_id=deployment.environment_id, branch=deployment.branch, status="active").exclude(id=deployment.id).update(status="superseded")
        deployment.status, deployment.error, deployment.activated_at = "active", "", datetime.now(UTC)
        deployment.manifest = {**(deployment.manifest or {}), "functions": code.functions}
        await deployment.save(update_fields=["status", "error", "activated_at", "manifest"])
        platform.envs.forget(ref)
        return deployment

    @r.get(f"{base}/function-deployments", auth=OPERATOR, tags=["functions"])
    async def deployments(ctx: HttpContext, ref: str, env: str):
        _manage_scope(ctx)
        environment = await get_environment(ref, env)
        limit, offset = page_params(ctx)
        query = FunctionDeployment.filter(environment=environment)
        if branch := ctx.query_params.get("branch"):
            query = query.filter(branch=branch)
        return {"data": [dump(row) for row in await query.offset(offset).limit(limit)]}

    @r.post(f"{base}/function-deployments", auth=OPERATOR, tags=["functions"], request_model=DeployBody)
    async def deploy(ctx: HttpContext, ref: str, env: str, body: DeployBody):
        _manage_scope(ctx)
        environment = await get_environment(ref, env)
        try:
            raw = base64.b64decode(body.archive, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="archive is not valid base64") from exc
        if len(raw) > platform.settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="archive exceeds the upload limit")
        deployment = await FunctionDeployment.create(
            id=uuid4().hex, environment=environment, branch=body.branch, checksum=hashlib.sha256(raw).hexdigest(),
            runtime=body.runtime, manifest=body.manifest, limits=body.limits, created_by=actor(ctx),
        )
        await activate(ref, env, deployment, raw)
        await audit(ctx, "function.deployed", project=ref, env=env, target=deployment.id, details={"checksum": deployment.checksum, "branch": body.branch})
        specs = platform.function_specs(ref, env, body.branch)
        return created({"deployment": dump(deployment), "functions": [spec.describe() for spec in specs]})

    @r.get(f"{base}/function-deployments/{{deployment_id}}", auth=OPERATOR, tags=["functions"])
    async def deployment(ctx: HttpContext, ref: str, env: str, deployment_id: str):
        _manage_scope(ctx)
        environment = await get_environment(ref, env)
        row = await FunctionDeployment.get_or_none(id=deployment_id, environment=environment)
        if row is None:
            raise HTTPException(status_code=404, detail="no such function deployment")
        return dump(row)

    @r.post(f"{base}/function-deployments/{{deployment_id}}/activate", auth=OPERATOR, tags=["functions"])
    async def reactivate(ctx: HttpContext, ref: str, env: str, deployment_id: str):
        """Roll back (or forward) to a deployment whose artifact is still stored."""
        _manage_scope(ctx)
        environment = await get_environment(ref, env)
        row = await FunctionDeployment.get_or_none(id=deployment_id, environment=environment)
        if row is None or row.status == "removed":
            raise HTTPException(status_code=404, detail="no such function deployment")
        await activate(ref, env, row, None)
        await audit(ctx, "function.activated", project=ref, env=env, target=row.id, details={"branch": row.branch})
        return {"deployment": dump(row), "functions": [spec.describe() for spec in platform.function_specs(ref, env, row.branch)]}

    @r.delete(f"{base}/function-deployments/{{deployment_id}}", auth=OPERATOR, tags=["functions"])
    async def remove(ctx: HttpContext, ref: str, env: str, deployment_id: str):
        _manage_scope(ctx)
        environment = await get_environment(ref, env)
        row = await FunctionDeployment.get_or_none(id=deployment_id, environment=environment)
        if row is None:
            raise HTTPException(status_code=404, detail="no such function deployment")
        if row.status == "active":
            raise HTTPException(status_code=409, detail="an active deployment cannot be removed")
        archive = platform.deployments.archive(ref, env, row.branch, row.id)
        archive.unlink(missing_ok=True)
        row.status, row.removed_at = "removed", datetime.now(UTC)
        await row.save(update_fields=["status", "removed_at"])
        await audit(ctx, "function.deployment_removed", project=ref, env=env, target=deployment_id)
        return {"removed": True}

    @r.get(f"{base}/function-branches", auth=OPERATOR, tags=["functions"])
    async def branches(ctx: HttpContext, ref: str, env: str):
        """Every branch with code deployed, and the deployment that is active on it."""
        _manage_scope(ctx)
        await get_environment(ref, env)
        out = []
        for name in [MAIN, *[b for b in platform.deployments.branches(ref, env) if b != MAIN]]:
            stamp = platform.deployments.stamp(ref, env, name)
            if stamp is None and name == MAIN:
                continue
            functions = platform.function_specs(ref, env, name)
            out.append({"branch": name, "deployment_id": (stamp or {}).get("id"), "checksum": (stamp or {}).get("checksum"), "functions": [spec.name for spec in functions]})
        return {"data": out}

    @r.delete(f"{base}/function-branches/{{branch}}", auth=OPERATOR, tags=["functions"])
    async def remove_branch(ctx: HttpContext, ref: str, env: str, branch: str):
        """Delete a branch's functions. The environment's own are untouched."""
        _manage_scope(ctx)
        environment = await get_environment(ref, env)
        try:
            removed = platform.deployments.remove_branch(ref, env, branch)
        except DeploymentError as exc:
            raise _problem(exc) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="that branch has no deployed functions")
        await FunctionDeployment.filter(environment=environment, branch=branch).exclude(status="removed").update(status="removed", removed_at=datetime.now(UTC))
        await audit(ctx, "function.branch_removed", project=ref, env=env, target=branch)
        return {"removed": True}

    @r.get(f"{base}/function-runs", auth=OPERATOR, tags=["functions"])
    async def runs(ctx: HttpContext, ref: str, env: str):
        _manage_scope(ctx)
        limit, offset = page_params(ctx)
        query = FunctionRun.filter(project=ref, env=env)
        if name := ctx.query_params.get("function"):
            query = query.filter(function=name)
        if branch := ctx.query_params.get("branch"):
            query = query.filter(branch=branch)
        if status := ctx.query_params.get("status"):
            query = query.filter(status=status)
        if after := ctx.query_params.get("after"):  # a follow loop asks only for what is newer than the last row it saw
            query = query.filter(created_at__gt=datetime.fromisoformat(after))
        return {"data": [dump(row, exclude=("logs",)) for row in await query.offset(offset).limit(limit)]}

    @r.get(f"{base}/function-runs/{{run_id}}", auth=OPERATOR, tags=["functions"])
    async def run(ctx: HttpContext, ref: str, env: str, run_id: str):
        _manage_scope(ctx)
        row = await FunctionRun.get_or_none(id=run_id, project=ref, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such function run")
        return dump(row)
