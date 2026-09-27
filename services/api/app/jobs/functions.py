"""Running a Python function as a background job."""

from __future__ import annotations

from typing import Any

from app.jobs.base import PawabaseJob


class RunFunctionJob(PawabaseJob):
    """Runs a registered function off the request path."""

    queue = "functions"
    tries = 1
    timeout = 300.0

    async def perform(self) -> Any:
        from app.execution import call_function

        platform, state = await self.environment()
        return await call_function(
            platform,
            state,
            self.params["function"],
            self.params.get("input"),
            trigger=self.params.get("trigger", "job"),
            auth=self.params.get("auth"),
        )
