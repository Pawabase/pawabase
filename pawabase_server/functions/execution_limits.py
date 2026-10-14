"""Per-project admission control for customer function executions.

The sandbox pool already limits the number of workers per deployment.  This
layer adds a second, project-scoped budget so one hosted project cannot occupy
all of a runtime's function capacity.  It is deliberately process-local: the
same policy is applied in every API/worker process, while the deployment-wide
sandbox limit remains the final safety ceiling.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


class ExecutionLimitExceeded(Exception):
    """A project has reached its concurrent or queued function limit."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _ProjectBudget:
    limit: int
    queue_limit: int
    active: int = 0
    queued: int = 0
    condition: asyncio.Condition | None = None

    def __post_init__(self) -> None:
        self.condition = asyncio.Condition()


class ProjectExecutionLimiter:
    """Bound concurrent and waiting function calls for each project.

    A zero value means unlimited for that dimension.  Limits are refreshed when
    the policy version changes, so a plan change affects the next invocation
    without restarting the runtime.
    """

    def __init__(self, *, wait_seconds: float = 30.0) -> None:
        self.wait_seconds = max(float(wait_seconds), 0.0)
        self._budgets: dict[str, _ProjectBudget] = {}

    @asynccontextmanager
    async def acquire(
        self,
        project_id: str | None,
        *,
        max_concurrent: int = 0,
        max_queued: int = 0,
        nested: bool = False,
    ) -> AsyncIterator[None]:
        """Hold one project execution slot until the invocation completes.

        Nested function calls are already bounded by the flow depth guard and
        do not take a second project slot; otherwise a limit of one could
        deadlock a function that calls another function synchronously.
        """
        if not project_id or nested or max_concurrent <= 0:
            yield
            return
        budget = self._budgets.get(project_id)
        if budget is None:
            budget = self._budgets[project_id] = _ProjectBudget(max_concurrent, max_queued)
        elif (budget.limit, budget.queue_limit) != (max_concurrent, max_queued):
            # Keep the existing condition so waiters are not stranded when a
            # plan changes while calls are active.
            async with budget.condition:
                budget.limit, budget.queue_limit = max_concurrent, max_queued
                budget.condition.notify_all()
        assert budget.condition is not None
        async with budget.condition:
            if budget.active >= budget.limit:
                if budget.queue_limit and budget.queued >= budget.queue_limit:
                    raise ExecutionLimitExceeded(
                        "This project's function concurrency and queue limits have been reached.",
                        code="function_capacity",
                    )
                budget.queued += 1
                try:
                    try:
                        await asyncio.wait_for(
                            budget.condition.wait_for(lambda: budget.active < budget.limit),
                            timeout=self.wait_seconds,
                        )
                    except TimeoutError as exc:
                        raise ExecutionLimitExceeded(
                            "This project's function queue is full; try again shortly.",
                            code="function_queue_timeout",
                        ) from exc
                finally:
                    budget.queued -= 1
            budget.active += 1
        try:
            yield
        finally:
            async with budget.condition:
                budget.active = max(0, budget.active - 1)
                budget.condition.notify(1)

    def usage(self, project_id: str) -> dict[str, int]:
        budget = self._budgets.get(project_id)
        if budget is None:
            return {"active": 0, "queued": 0}
        return {"active": budget.active, "queued": budget.queued}
