"""The base of every Pawabase queue job.

Pawabase jobs are Sillo :class:`~sillo.work.queue.Job` subclasses, run by
Sillo's :class:`~sillo.work.queue.QueueWorker`. Two things are added:

* **Tracking.** Every attempt updates a :class:`~database.models.JobRun`, so
  Studio can show waiting, active, completed, failed and retried jobs.
* **Settled failures.** Retries use Sillo's ``RetryMiddleware`` inside the
  attempt. When the last attempt fails, the job records the failure (in the
  ``JobRun`` and through Sillo's failed-job repository interface), calls
  ``failed()``, and returns normally, so the worker acknowledges it instead of
  leaving it for redelivery.
"""

from __future__ import annotations

import os
import socket
import time
import traceback
from datetime import UTC, datetime
from typing import Any, ClassVar

from sillo.work.queue import Job, QRetryMiddleware
from tortoise.exceptions import IntegrityError

from app.platform import get_platform, json_safe
from database.models import JobRun

WORKER_NAME = f"{socket.gethostname()}:{os.getpid()}"


class PawabaseJob(Job):
    """A tracked, settled Sillo job.

    Subclasses define ``queue``, ``tries``, ``backoff`` and ``perform()``.
    Constructor keyword arguments are what :meth:`Platform.dispatch` queued.
    """

    queue: ClassVar[str] = "default"
    tries: ClassVar[int] = 1
    backoff: ClassVar[int] = 1
    timeout: ClassVar[float | None] = 120.0

    def __init__(self, project: str, env: str, **kwargs: Any) -> None:
        super().__init__()
        self.project = project
        self.env = env
        self.params = kwargs

    def middleware_pipeline(self) -> list[Any]:
        pipeline = list(self.__class__.middleware)
        if self.tries > 1:
            pipeline.insert(
                0,
                QRetryMiddleware(
                    max_attempts=self.tries, base_delay=float(self.backoff or 1), max_delay=60.0
                ),
            )
        return pipeline

    async def perform(self) -> Any:
        raise NotImplementedError

    async def handle(self) -> Any:
        self._attempts += 1
        await self._track(
            status="active" if self._attempts == 1 else "retrying",
            attempts=self._attempts,
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

    async def _track(self, **fields: Any) -> None:
        job_id = self._job_id
        if not job_id:
            return
        updated = await JobRun.filter(id=job_id).update(**fields)
        if updated:
            return
        try:
            await JobRun.create(
                id=job_id,
                project=self.project,
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

        from app.jobs.failed import RecordFailedJobRepository

        await RecordFailedJobRepository().log(
            queue=self.queue,
            job_id=self._job_id or "unknown",
            job_class=self.job_reference(),
            payload=json.dumps(
                {"project": self.project, "env": self.env, **self.params}, default=str
            ),
            exception=error,
        )

    async def environment(self):
        platform = get_platform()
        return platform, await platform.state(self.project, self.env)
