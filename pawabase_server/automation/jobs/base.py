"""The base of every Pawabase queue job.

Pawabase jobs are Sillo :class:`~sillo.work.queue.Job` subclasses, run by
Sillo's :class:`~sillo.work.queue.QueueWorker`. Two things are added:

* **Tracking.** Every attempt updates a :class:`~database.models.JobRun`, so
  Studio can show waiting, active, completed, failed and retried jobs.
* **Durable retries.** A failed attempt is not retried by sleeping inside the
  worker (which held a concurrency slot and lost the retry if the worker
  died). The job dispatches itself again as a *delayed* job, with exponential
  backoff and jitter, and the current attempt is acknowledged. The retry lives
  in the queue (Redis' delayed set), so it survives restarts and may wait hours.
  One ``JobRun`` record follows the job across attempts.
* **Settled failures.** When the last attempt fails, the job records the failure
  (in the ``JobRun`` and through Sillo's failed-job repository interface), calls
  ``failed()``, and returns normally, so the worker acknowledges it instead of
  leaving it for redelivery. Failed jobs are listed in Studio and can be replayed.
"""

from __future__ import annotations

import os
import socket
import time
import traceback
from datetime import UTC, datetime
from typing import Any, ClassVar

from sillo.work.queue import Job
from tortoise.exceptions import IntegrityError

from pawabase_server.core.retry import RetryPolicy
from pawabase_server.db.models import JobRun
from pawabase_server.platform import get_platform, json_safe

WORKER_NAME = f"{socket.gethostname()}:{os.getpid()}"

#: Parameters a retry carries that are not the job's own: which attempt this is, and the record it reports to.
ATTEMPT_KEY = "_attempt"
RECORD_KEY = "_record"


class PawabaseJob(Job):
    """A tracked, settled Sillo job.

    Subclasses define ``queue``, ``tries``, ``backoff`` and ``perform()``.
    Constructor keyword arguments are what :meth:`Platform.dispatch` queued.
    """

    queue: ClassVar[str] = "default"
    tries: ClassVar[int] = 1
    backoff: ClassVar[float] = 1
    #: The longest a single retry waits, whatever the backoff says.
    max_backoff: ClassVar[float] = 3600.0
    timeout: ClassVar[float | None] = 120.0

    def __init__(self, env: str, **kwargs: Any) -> None:
        super().__init__()
        self.env = env
        self.attempt = int(kwargs.pop(ATTEMPT_KEY, 1) or 1)
        self.record_id = kwargs.pop(RECORD_KEY, None)
        self.params = kwargs

    # ── what a subclass may tune ─────────────────────────────────────────

    @classmethod
    def policy(cls) -> RetryPolicy:
        """The class's retry policy (``core/retry.py``): ``tries`` attempts, ``backoff`` doubling, capped at ``max_backoff``."""
        return RetryPolicy(
            max(1, int(cls.tries)), base=float(cls.backoff or 1), max_delay=cls.max_backoff
        )

    def retry_policy(self) -> RetryPolicy:
        """This job's policy; a subclass may make it depend on the job (a webhook endpoint's attempt limit)."""
        return self.policy()

    def attempt_limit(self) -> int:
        """How many attempts this job gets in all."""
        return self.retry_policy().max_attempts

    def retry_delay(self, attempt: int) -> float:
        """Seconds before attempt ``attempt + 1``, from the retry policy (with jitter)."""
        return self.retry_policy().delay(attempt)

    def retryable(self, exc: Exception) -> bool:
        """Whether a failure is worth another attempt. Everything is, unless a subclass says otherwise."""
        return True

    async def perform(self) -> Any:
        raise NotImplementedError

    # ── lifecycle ────────────────────────────────────────────────────────

    async def handle(self) -> Any:
        self._attempts += 1
        await self._track(
            status="active" if self.attempt == 1 else "retrying",
            attempts=self.attempt,
            started_at=datetime.now(UTC),
            worker=WORKER_NAME,
        )
        return await self.perform()

    async def fire(self) -> Any:
        started = time.perf_counter()
        try:
            result = await super().fire()
        except Exception as exc:
            error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            if self.attempt < self.attempt_limit() and self.retryable(exc):
                try:
                    await self._schedule_retry(error, started)
                    return None
                except (
                    Exception
                ):  # the queue itself is unavailable: settle, so the failure is at least recorded
                    pass
            await self._track(
                status="failed",
                error=error,
                finished_at=datetime.now(UTC),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            await self._record_failure(error)
            try:
                await self.failed(exc)
            except Exception:
                pass
            return None
        await self._track(
            status="completed",
            result=json_safe(result),
            finished_at=datetime.now(UTC),
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
        )
        return result

    async def _schedule_retry(self, error: str, started: float) -> None:
        delay = self.retry_delay(self.attempt)
        await get_platform().dispatch(
            type(self),
            env=self.env,
            queue=self.queue,
            delay=max(1, round(delay)),
            **self.params,
            **{ATTEMPT_KEY: self.attempt + 1, RECORD_KEY: self._record()},
        )
        await self._track(
            status="retrying",
            error=error,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            available_at=datetime.fromtimestamp(time.time() + delay, UTC),
        )
        await self.retrying(self.attempt + 1, delay, error)

    async def retrying(self, next_attempt: int, delay: float, error: str) -> None:
        """Called after a retry has been scheduled. Subclasses record it where their users look."""

    def _record(self) -> str | None:
        return self.record_id or self._job_id

    async def _track(self, **fields: Any) -> None:
        job_id = self._record()
        if not job_id:
            return
        updated = await JobRun.filter(id=job_id).update(**fields)
        if updated:
            return
        try:
            await JobRun.create(
                id=job_id,
                env=self.env,
                queue=self.queue,
                job=type(self).__name__,
                **fields,
            )
        except IntegrityError:
            # The dispatcher recorded the job between our update and create.
            await JobRun.filter(id=job_id).update(**fields)

    async def _record_failure(self, error: str) -> None:
        import json

        from pawabase_server.automation.jobs.failed import RecordFailedJobRepository

        await RecordFailedJobRepository().log(
            queue=self.queue,
            job_id=self._record() or "unknown",
            job_class=self.job_reference(),
            payload=json.dumps({"env": self.env, **self.params}, default=str),
            exception=error,
        )

    async def environment(self):
        platform = get_platform()
        release_id = self.params.get("release_id")
        state = (
            await platform.state_for_release(self.env, release_id)
            if release_id
            else await platform.state(self.env)
        )
        return platform, state
