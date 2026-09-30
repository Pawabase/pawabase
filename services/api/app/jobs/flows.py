"""Running a flow as a background job."""

from __future__ import annotations

from typing import Any

from app.jobs.base import PawabaseJob


class RunFlowJob(PawabaseJob):
    """Runs a flow off the request path (``queue.flow`` blocks, events, schedules)."""

    queue = "flows"
    tries = 1
    timeout = 300.0

    async def perform(self) -> Any:
        from app.execution import run_flow

        platform, state = await self.environment()
        run = await run_flow(
            platform,
            state,
            self.params["flow"],
            self.params.get("input"),
            trigger=self.params.get("trigger", "job"),
            auth=self.params.get("auth"),
            credential={"is_service": True, "role": "service"},
            job_id=self._job_id,
            request_id=self.params.get("request_id"),
            entry=self.params.get("entry"),
        )
        return {"run_id": run.id, "result": run.result()}
