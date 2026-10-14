import asyncio

import pytest

from pawabase_server.functions.execution_limits import (
    ExecutionLimitExceeded,
    ProjectExecutionLimiter,
)


@pytest.mark.asyncio
async def test_a_project_cannot_exceed_its_concurrent_function_limit():
    limiter = ProjectExecutionLimiter(wait_seconds=1)
    entered = asyncio.Event()

    async with limiter.acquire("project-a", max_concurrent=1, max_queued=0):
        entered.set()
        waiting = asyncio.create_task(
            limiter.acquire("project-a", max_concurrent=1, max_queued=1).__aenter__()
        )
        await asyncio.sleep(0)
        with pytest.raises(ExecutionLimitExceeded, match="queue"):
            async with limiter.acquire("project-a", max_concurrent=1, max_queued=1):
                pass
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    assert entered.is_set()
    assert limiter.usage("project-a") == {"active": 0, "queued": 0}


@pytest.mark.asyncio
async def test_waiting_function_is_released_after_the_active_call_finishes():
    limiter = ProjectExecutionLimiter(wait_seconds=1)
    started = asyncio.Event()
    finished = asyncio.Event()

    async def waiting_call():
        async with limiter.acquire("project-a", max_concurrent=1, max_queued=1):
            started.set()
            finished.set()

    async with limiter.acquire("project-a", max_concurrent=1, max_queued=1):
        task = asyncio.create_task(waiting_call())
        await asyncio.sleep(0)
        assert limiter.usage("project-a") == {"active": 1, "queued": 1}
    await task
    assert started.is_set() and finished.is_set()
    assert limiter.usage("project-a") == {"active": 0, "queued": 0}


@pytest.mark.asyncio
async def test_nested_function_calls_do_not_deadlock_a_single_slot():
    limiter = ProjectExecutionLimiter(wait_seconds=0.01)
    async with (
        limiter.acquire("project-a", max_concurrent=1, max_queued=0),
        limiter.acquire("project-a", max_concurrent=1, max_queued=0, nested=True),
    ):
        assert limiter.usage("project-a") == {"active": 1, "queued": 0}
