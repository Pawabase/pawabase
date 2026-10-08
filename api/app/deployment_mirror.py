"""A copy of every function bundle in object storage, so a deployment's code outlives its volume.

Function bundles are unpacked onto a local volume (``PAWABASE_DEPLOYMENTS_PATH``). Lose that
volume, or start the same deployment on another server, and its functions are gone until someone
deploys again. When the platform's default storage is S3-compatible, each bundle is also kept
there, under ``_pawabase/deployments/``; at start-up an active deployment whose files are missing
locally is fetched and installed again. Nothing changes when storage is local: the volume is then
the only store, as before.

The bundle is source code, so it is not stored in the platform database; the control-plane record
only holds its checksum, which a restored copy must match.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from app.config import default_storage
from app.storage.s3 import S3Driver
from database.models import FunctionDeployment

if TYPE_CHECKING:
    from app.platform import Platform

logger = logging.getLogger("pawabase.api")

PREFIX = "_pawabase/deployments"


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


class DeploymentMirror:
    def __init__(self, platform: Platform, driver: Any = None) -> None:
        self.platform = platform
        self._driver = driver

    @property
    def enabled(self) -> bool:
        if self._driver is not None:
            return True
        settings = self.platform.settings
        return bool(settings.deployment_mirror) and default_storage(settings)["driver"] == "s3"

    def driver(self) -> Any:
        if self._driver is None:
            config = default_storage(self.platform.settings)
            base = str(config.get("prefix") or "").strip("/")
            self._driver = S3Driver(
                bucket=str(config["bucket"]),
                endpoint=str(config["endpoint"]),
                region=str(config["region"]),
                access_key=str(config["access_key"]),
                secret_key=str(config["secret_key"]),
                prefix="/".join(part for part in (base, PREFIX) if part),
                path_style=bool(config["path_style"]),
            )
        return self._driver

    @staticmethod
    def key(env: str, branch: str, deployment_id: str) -> str:
        return f"{env}/{branch}/{deployment_id}.tar.gz"

    async def put(self, env: str, branch: str, deployment_id: str, data: bytes) -> bool:
        """Keep a copy. A failure is logged and reported, never raised: the deploy itself succeeded."""
        if not self.enabled:
            return False
        try:
            await self.driver().write(
                self.key(env, branch, deployment_id), _chunks(data), content_type="application/gzip"
            )
        except Exception as exc:
            logger.warning(
                "deployment %s of %s/%s was not mirrored to storage: %s",
                deployment_id,
                env,
                branch,
                exc,
            )
            return False
        return True

    async def get(self, env: str, branch: str, deployment_id: str) -> bytes | None:
        if not self.enabled:
            return None
        try:
            return b"".join(
                [chunk async for chunk in self.driver().read(self.key(env, branch, deployment_id))]
            )
        except Exception:
            return None

    async def restore(self) -> list[str]:
        """Install the active bundles this container does not have. Returns the deployments restored."""
        if not self.enabled:
            return []
        restored: list[str] = []
        active = await FunctionDeployment.filter(status="active", removed_at=None).prefetch_related(
            "environment"
        )
        for deployment in active:
            env, branch = deployment.environment.name, deployment.branch
            stamp = self.platform.deployments.stamp(env, branch)
            if stamp and stamp.get("id") == deployment.id:
                continue
            data = await self.get(env, branch, deployment.id)
            if data is None:
                logger.warning(
                    "deployment %s of %s/%s is active but its files are missing and no mirrored copy exists",
                    deployment.id,
                    env,
                    branch,
                )
                continue
            if hashlib.sha256(data).hexdigest() != deployment.checksum:
                logger.error(
                    "the mirrored copy of deployment %s does not match its checksum; not restoring it",
                    deployment.id,
                )
                continue
            try:
                await asyncio.to_thread(self.platform.deployments.prepare, data)
                self.platform.deployments.install(
                    env, branch, data, deployment_id=deployment.id, checksum=deployment.checksum
                )
            except Exception as exc:
                logger.error(
                    "could not restore deployment %s of %s/%s: %s", deployment.id, env, branch, exc
                )
                continue
            self.platform.envs.forget(env)
            restored.append(deployment.id)
            logger.info("restored deployment %s of %s/%s from storage", deployment.id, env, branch)
        return restored

    async def close(self) -> None:
        if self._driver is not None and hasattr(self._driver, "close"):
            await self._driver.close()
