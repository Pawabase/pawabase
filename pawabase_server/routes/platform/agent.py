"""``POST /platform/v1/agent/execute``: a control plane hands this runtime one signed instruction directly, the push counterpart of the polling agent.

The caller needs a management key, and the instruction itself must carry the pool's signature, so a leaked key alone cannot make the runtime do
anything. It is the same executor, allowlist and idempotency as the polling agent.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sillo import HttpContext, Router
from sillo import json as json_response
from sillo.exceptions import HTTPException

from pawabase_server.agent.executor import Executor, OperationError
from pawabase_server.agent.protocol import ProtocolError, verify_job
from pawabase_server.automation.jobs.environments import PurgeEnvironmentJob
from pawabase_server.db.models import Environment
from pawabase_server.platform import Platform
from pawabase_server.routes.common import MANAGE


class JobBody(BaseModel):
    job: dict[str, Any]


def register(r: Router, platform: Platform) -> None:
    executor = Executor(platform.settings)

    @r.post(
        "/agent/execute",
        auth=MANAGE,
        tags=["agent"],
        request_model=JobBody,
        summary="Carry out one signed instruction from the control plane",
    )
    async def execute(ctx: HttpContext, body: JobBody):
        settings = platform.settings
        if not (settings.agent_token and settings.agent_pool):
            raise HTTPException(status_code=404, detail="this runtime takes no instructions")
        try:
            job = verify_job(settings.agent_token, body.job, pool=settings.agent_pool)
        except ProtocolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if job["kind"] == "project.delete" and (job.get("payload") or {}).get("purge"):
            # A project is only unregistered once it has no environments: queue each one's purge, then ask again.
            remaining = await Environment.filter(project_id=job["project_id"]).all()
            for environment in remaining:
                settings_ = environment.settings or {}
                if not settings_.get("deletion_pending"):
                    environment.settings = {**settings_, "deletion_pending": True}
                    await environment.save()
                    await platform.dispatch(
                        PurgeEnvironmentJob,
                        env=environment.name,
                        target=environment.name,
                        source="project.delete",
                        requested_by="control-plane",
                    )
            if remaining:
                return json_response(
                    {
                        "ok": False,
                        "error": f"{len(remaining)} environment(s) are being removed",
                        "retryable": True,
                    },
                    status_code=422,
                )
        try:
            result = await executor.execute(job)
        except OperationError as exc:
            return json_response(
                {"ok": False, "error": str(exc)[:500], "retryable": exc.retryable},
                status_code=422,
            )
        return {"ok": True, "result": result}
