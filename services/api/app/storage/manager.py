"""Storage for every environment, on Sillo's storage package.

Each environment configures where its objects go (``infra.storage``): the
local disk, an S3-compatible service the developer runs, or memory for
tests. Each bucket gets one Sillo driver, built once. A request gets a Sillo
:class:`~sillo.storage.Bucket` wrapping that driver with a Pawabase policy
bound to the caller's credential, so sniffing, size limits, key normalisation
and signed grants all stay Sillo's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sillo.storage import Bucket, LocalDriver, MemoryDriver
from sillo.storage.base import Action, StorageEvent
from sillo.storage.signing import Signer

from pawabase_kit.policies import PolicyStorage
from pawabase_kit.telemetry import note

from .s3 import S3Driver

if TYPE_CHECKING:
    from app.platform import Platform
    from app.state import EnvironmentState
    from database.models import Bucket as BucketModel

EVENT_NAMES = {
    Action.WRITE: "file.uploaded",
    Action.DELETE: "file.deleted",
}


class StorageManager:
    """Builds and caches storage drivers per environment bucket."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._drivers: dict[tuple[str, str, str, str], Any] = {}
        self._signers: dict[tuple[str, str, str], Signer] = {}
        self.operations = 0

    def _config(self, state: EnvironmentState) -> dict[str, Any]:
        config = dict(state.infra.get("storage") or {})
        config.setdefault("driver", "local")
        return {key: self.platform.resolve_value(state, value) for key, value in config.items()}

    def signer(self, state: EnvironmentState, bucket: str) -> Signer:
        key = (state.project_ref, state.env_name, bucket)
        signer = self._signers.get(key)
        if signer is None:
            secret = f"{self.platform.settings.internal_secret}:storage:{state.project_ref}:{state.env_name}"
            signer = self._signers[key] = Signer(
                secret, f"{state.project_ref}/{state.env_name}/{bucket}"
            )
        return signer

    def driver(self, state: EnvironmentState, bucket: str) -> Any:
        config = self._config(state)
        fingerprint = repr(sorted(config.items()))
        key = (state.project_ref, state.env_name, bucket, fingerprint)
        driver = self._drivers.get(key)
        if driver is not None:
            return driver
        kind = config["driver"]
        if kind == "memory":
            driver = MemoryDriver()
        elif kind == "s3":
            base_prefix = str(config.get("prefix") or "").strip("/")
            prefix = "/".join(
                part for part in (base_prefix, state.project_ref, state.env_name, bucket) if part
            )
            driver = S3Driver(
                bucket=config.get("bucket", ""),
                endpoint=config.get("endpoint", ""),
                region=config.get("region", "us-east-1"),
                access_key=config.get("access_key", ""),
                secret_key=config.get("secret_key", ""),
                prefix=prefix,
                path_style=bool(config.get("path_style", True)),
            )
        elif kind == "local":
            root = config.get("root") or self.platform.settings.storage_root
            driver = LocalDriver(
                f"{root.rstrip('/')}/{state.project_ref}/{state.env_name}/{bucket}",
                signer=self.signer(state, bucket),
                base_url=f"{self.platform.settings.public_url.rstrip('/')}/storage/v1/signed/{state.project_ref}/{state.env_name}/{bucket}",
            )
        else:
            raise ValueError(f"unknown storage driver {kind!r}")
        driver.listen(self._listener(state.project_ref, state.env_name))
        self._drivers[key] = driver
        return driver

    def _listener(self, project: str, env: str):
        async def listener(event: StorageEvent) -> None:
            self.operations += 1
            note("storage", f"{event.action.value}:{event.bucket}/{event.key}", append=True)
            name = EVENT_NAMES.get(event.action)
            if name and event.outcome == "ok":
                await self.platform.bus.emit(
                    name,
                    project=project,
                    env=env,
                    payload={
                        "bucket": event.bucket,
                        "key": event.key,
                        "size": event.size,
                        "driver": event.driver,
                    },
                )

        return listener

    def bucket(
        self, state: EnvironmentState, name: str, *, credential: dict[str, Any] | None = None
    ) -> Bucket:
        """A Sillo bucket for this request, with its policy bound to *credential*."""
        model: BucketModel | None = state.buckets.get(name)
        if model is None:
            from sillo.exceptions import HTTPException

            raise HTTPException(status_code=404, detail=f"no bucket {name!r}")
        policy = PolicyStorage(
            state.engine,
            read="public" if model.public else (model.read_policy or "authenticated"),
            write=model.write_policy or "authenticated",
            credential=credential,
            project=state.project_ref,
            env=state.env_name,
            signed_writes=model.signed_uploads,
        )
        max_bytes = int(model.max_bytes or self.platform.settings.max_upload_bytes)
        driver = self.driver(state, name)
        # Sillo buckets name themselves in storage events; keep the Pawabase name.
        return Bucket(
            name, driver, policy=policy, max_bytes=max_bytes, accepts=tuple(model.accepts or ())
        )

    async def close(self) -> None:
        for driver in self._drivers.values():
            try:
                await driver.close()
            except Exception:
                pass
        self._drivers.clear()
