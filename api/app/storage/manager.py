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

from app.config import default_storage
from pawabase_core.policies import PolicyStorage
from pawabase_core.telemetry import note

from .s3 import S3Driver


class MimePatterns(tuple):
    """A bucket's ``accepts`` list that understands wildcards.

    Studio and the API store MIME *patterns* (``image/*``), but Sillo's
    ``Bucket`` checks ``resolved in accepts`` by equality, so ``image/png``
    never matched ``image/*`` and every wildcard bucket refused every upload.
    Sillo only uses ``in`` and iteration on this value, so overriding
    ``__contains__`` is enough. ``*`` and ``*/*`` accept anything; parameters
    such as ``; charset=utf-8`` and letter case are ignored.
    """

    __slots__ = ()

    def __new__(cls, patterns=()):
        return super().__new__(cls, (str(p).strip().lower() for p in patterns if str(p).strip()))

    def __contains__(self, content_type: object) -> bool:
        if not isinstance(content_type, str):
            return False
        wanted = content_type.split(";", 1)[0].strip().lower()
        major = wanted.split("/", 1)[0]
        for pattern in tuple.__iter__(self):
            if pattern in ("*", "*/*") or pattern == wanted:
                return True
            if pattern.endswith("/*") and pattern[:-2] == major:
                return True
        return False


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
        """The environment's storage: its own ``infra.storage``, else the platform default."""
        config = dict(state.infra.get("storage") or {})
        if not config:
            config = default_storage(self.platform.settings)
        elif not config.get("driver"):
            config["driver"] = "s3" if config.get("endpoint") else "local"
        return {key: self.platform.resolve_value(state, value) for key, value in config.items()}

    def signer(self, state: EnvironmentState, bucket: str) -> Signer:
        key = (state.env_name, bucket)
        signer = self._signers.get(key)
        if signer is None:
            secret = f"{self.platform.settings.internal_secret}:storage:{state.env_name}"
            signer = self._signers[key] = Signer(secret, f"{state.env_name}/{bucket}")
        return signer

    def driver(self, state: EnvironmentState, bucket: str) -> Any:
        config = self._config(state)
        fingerprint = repr(sorted(config.items()))
        key = (state.env_name, bucket, fingerprint)
        driver = self._drivers.get(key)
        if driver is not None:
            return driver
        kind = config["driver"]
        if kind == "memory":
            driver = MemoryDriver()
        elif kind == "s3":
            base_prefix = str(config.get("prefix") or "").strip("/")
            prefix = "/".join(part for part in (base_prefix, state.env_name, bucket) if part)
            driver = S3Driver(
                bucket=config.get("bucket", ""),
                endpoint=config.get("endpoint", ""),
                region=config.get("region", "us-east-1"),
                access_key=config.get("access_key", ""),
                secret_key=config.get("secret_key", ""),
                prefix=prefix,
                path_style=bool(config.get("path_style", True)),
                public_endpoint=config.get("public_endpoint", ""),
            )
        elif kind == "local":
            root = config.get("root") or self.platform.settings.storage_root
            driver = LocalDriver(
                f"{root.rstrip('/')}/{state.env_name}/{bucket}",
                signer=self.signer(state, bucket),
                base_url=f"{self.platform.settings.public_url.rstrip('/')}/storage/v1/signed/{state.env_name}/{bucket}",
            )
        else:
            raise ValueError(f"unknown storage driver {kind!r}")
        driver.listen(self._listener(state.env_name))
        self._drivers[key] = driver
        return driver

    async def prepare_default(self) -> str:
        """Ready the platform default storage at startup.

        For an S3-compatible default, create the remote bucket when missing so
        the bundled MinIO needs no setup. A service that is down or refuses the
        credentials is reported, not raised: the API still starts, and uploads
        fail with that same reason until it is fixed.

        Returns:
            A one-line description of the default storage, for the startup log.
        """
        config = default_storage(self.platform.settings)
        if config["driver"] != "s3":
            return f"storage: {config['driver']} (default)"
        driver = S3Driver(
            bucket=str(config["bucket"]),
            endpoint=str(config["endpoint"]),
            region=str(config["region"]),
            access_key=str(config["access_key"]),
            secret_key=str(config["secret_key"]),
            path_style=bool(config["path_style"]),
        )
        where = f"{config['endpoint'] or 'aws'}/{config['bucket']}"
        try:
            created = await driver.ensure_bucket()
        except Exception as error:
            return f"storage: s3 {where} NOT READY ({error})"
        finally:
            await driver.close()
        return f"storage: s3 {where} ({'bucket created' if created else 'ready'})"

    def _listener(self, env: str):
        async def listener(event: StorageEvent) -> None:
            self.operations += 1
            note("storage", f"{event.action.value}:{event.bucket}/{event.key}", append=True)
            name = EVENT_NAMES.get(event.action)
            if name and event.outcome == "ok":
                await self.platform.bus.emit(
                    name,
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
            env=state.env_name,
            signed_writes=model.signed_uploads,
        )
        max_bytes = int(model.max_bytes or self.platform.settings.max_upload_bytes)
        driver = self.driver(state, name)
        # Sillo buckets name themselves in storage events; keep the Pawabase name.
        return Bucket(
            name,
            driver,
            policy=policy,
            max_bytes=max_bytes,
            accepts=MimePatterns(model.accepts or ()),
        )

    async def close(self) -> None:
        for driver in self._drivers.values():
            try:
                await driver.close()
            except Exception:
                pass
        self._drivers.clear()
