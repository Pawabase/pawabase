"""Irreversible environment teardown, performed outside the request path."""

from __future__ import annotations

from typing import Any

from app.data.sql import quote
from app.jobs.base import PawabaseJob
from database.models import (
    AuditEntry,
    Environment,
    EventLog,
    FlowRun,
    JobRun,
    MailLog,
    RequestLog,
    Resource,
)
from pawabase_core.context import PlatformContext


class PurgeEnvironmentJob(PawabaseJob):
    """Delete all environment-owned data after a deliberate Studio confirmation."""

    queue = "default"
    tries = 3
    backoff = 5
    timeout = 300.0

    async def perform(self) -> dict[str, Any]:
        platform, state = await self.environment()
        environment = await Environment.filter(name=self.env).first()
        if environment is None:
            return {"environment": self.env, "already_deleted": True}

        resources = await Resource.filter(environment=environment).all()
        result: dict[str, Any] = {"resource_tables": 0, "storage_objects": 0}

        # Remove all application tables before deleting the resource definitions.
        # Resource table names are validated at creation and quote() validates again.
        source = await state.source()
        for resource in resources:
            await source.execute(f"DROP TABLE IF EXISTS {quote(source.dialect, resource.table)}")
            result["resource_tables"] += 1

        # Every bucket uses an environment-prefixed storage driver. Walk it
        # recursively so local and S3-backed environments are treated alike.
        for bucket_name in list(state.buckets):
            bucket = platform.storage.bucket(state, bucket_name, credential={"is_service": True})
            result["storage_objects"] += await self._empty_bucket(bucket, "")

        # Akountz owns user accounts, sessions, MFA, organizations and auth
        # events in a different database, so it must purge its tenant itself.
        auth = await platform.akountz.delete(
            f"/admin/v1/envs/{self.env}", context=PlatformContext(env=self.env, role="service")
        )
        result["identity"] = auth

        # Activity and audit entries do not FK to Environment. Delete them
        # explicitly so request, flow, mail and event history is not retained.
        for model in (RequestLog, FlowRun, EventLog, MailLog, JobRun, AuditEntry):
            await model.filter(env=self.env).delete()

        await environment.delete()
        platform.envs.forget(self.env)

        # A single receipt remains at the installation level, after all of the
        # environment's own logs have been removed.
        await AuditEntry.create(
            env=None,
            actor=self.params.get("requested_by"),
            action="environment.purged",
            target=self.env,
            details=result,
        )
        return {"environment": self.env, **result}

    async def _empty_bucket(self, bucket: Any, prefix: str) -> int:
        deleted = 0
        cursor = ""
        while True:
            page = await bucket.page(prefix, cursor=cursor, limit=1000)
            for file in page.files:
                if await bucket.delete(file.key, signed=True):
                    deleted += 1
            for child in page.prefixes:
                deleted += await self._empty_bucket(bucket, child)
            if not page.cursor:
                return deleted
            cursor = page.cursor
