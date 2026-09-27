"""Sillo's failed-job repository, persisted in the platform database."""

from __future__ import annotations

import time

from sillo.work.queue import FailedJob, FailedJobRepository

from database.models import FailedJobRecord


class RecordFailedJobRepository(FailedJobRepository):
    """Implements Sillo's :class:`FailedJobRepository` on a Record model."""

    async def log(
        self, queue: str, job_id: str, job_class: str, payload: str, exception: str
    ) -> None:
        await FailedJobRecord.create(
            queue=queue,
            job_id=job_id,
            job_class=job_class,
            payload=payload,
            exception=exception,
            failed_at=time.time(),
        )

    async def all(self, limit: int = 50, offset: int = 0) -> list[FailedJob]:
        rows = await FailedJobRecord.all().offset(offset).limit(limit)
        return [self._to_failed(row) for row in rows]

    async def find(self, job_id: str) -> FailedJob | None:
        row = await FailedJobRecord.filter(job_id=job_id).first()
        return self._to_failed(row) if row else None

    async def forget(self, job_id: str) -> bool:
        return bool(await FailedJobRecord.filter(job_id=job_id).delete())

    async def flush(self) -> None:
        await FailedJobRecord.all().delete()

    @staticmethod
    def _to_failed(row: FailedJobRecord) -> FailedJob:
        return FailedJob(
            id=row.job_id,
            queue=row.queue,
            job_class=row.job_class,
            payload=row.payload,
            exception=row.exception,
            failed_at=row.failed_at,
        )
