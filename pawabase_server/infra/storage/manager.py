"""Storage for every environment, on Sillo's storage package.

Each environment configures where its objects go (``infra.storage``): the
local disk, an S3-compatible service the developer runs, or memory for
tests. Each bucket gets one Sillo driver, built once. A request gets a Sillo
:class:`~sillo.storage.Bucket` wrapping that driver with a Pawabase policy
bound to the caller's credential, so sniffing, size limits, key normalisation
and signed grants all stay Sillo's.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from sillo.storage import Bucket, LocalDriver, MemoryDriver
from sillo.storage.base import Action, StorageEvent
from sillo.storage.errors import PolicyRefused
from sillo.storage.paths import normalise
from sillo.storage.signing import SignedGrant, Signer

from pawabase_server.core import envvars
from pawabase_server.core.telemetry import note
from pawabase_server.infra.storage.s3 import S3Driver
from pawabase_server.infra.storage.transform import Variants
from pawabase_server.policies import PolicyStorage


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
    from pawabase_server.db.models import Bucket as BucketModel
    from pawabase_server.environments.state import EnvironmentState
    from pawabase_server.platform import Platform

EVENT_NAMES = {
    Action.WRITE: "file.uploaded",
    Action.DELETE: "file.deleted",
}


#: The per-environment variables that shape storage, and the config field each sets.
ENVIRONMENT_STORAGE = {
    "STORAGE_DRIVER": "driver",
    "STORAGE_ROOT": "root",
    "STORAGE_ENDPOINT": "endpoint",
    "STORAGE_PUBLIC_ENDPOINT": "public_endpoint",
    "STORAGE_BUCKET": "bucket",
    "STORAGE_REGION": "region",
    "STORAGE_ACCESS_KEY": "access_key",
    "STORAGE_SECRET_KEY": "secret_key",
    "STORAGE_PREFIX": "prefix",
    "STORAGE_PATH_STYLE": "path_style",
}


class StorageManager:
    """Builds and caches storage drivers per environment bucket."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._drivers: dict[tuple[str, str, str, str], Any] = {}
        self._signers: dict[tuple[str, str, str], Signer] = {}
        self.operations = 0
        #: Rendered image variants, bounded and per process; the read policy is checked before every lookup.
        self.variants = Variants()

    def _config(self, state: EnvironmentState) -> dict[str, Any]:
        """The environment's storage.

        The platform default (``PAWABASE_STORAGE_*``) is the base. An older install's
        ``infra.storage`` replaces it, deprecated. Whatever ``<ENV>_STORAGE_*``
        variables the deployment sets for this environment are laid over the result, so
        ``PRODUCTION_STORAGE_BUCKET`` alone moves one bucket and keeps the rest.
        """
        legacy = dict(state.infra.get("storage") or {})
        if legacy:
            self.platform.deprecated(
                state.env_name,
                "infra.storage",
                f"set {envvars.prefix_for(state.env_name)}_STORAGE_* variables instead",
            )
            config = legacy
            if not config.get("driver"):
                config["driver"] = "s3" if config.get("endpoint") else "local"
        else:
            config = self.platform.settings.default_storage()
        project_bucket = getattr(state, "project_bucket", None)
        if project_bucket and config.get("driver") == "s3":
            # A project made by a control plane has a bucket of its own on the deployment's S3 server.
            config["bucket"] = project_bucket

        overlay: dict[str, Any] = {}
        for variable, field in ENVIRONMENT_STORAGE.items():
            value = envvars.get(state.env_name, variable)
            if value is not None:
                overlay[field] = value
        if "path_style" in overlay:
            overlay["path_style"] = envvars.boolean(state.env_name, "STORAGE_PATH_STYLE", True)
        if overlay:
            config.update(overlay)
            if "driver" not in overlay and config["driver"] != "s3" and overlay.get("endpoint"):
                # An endpoint on a deployment whose default is local storage means S3.
                config["driver"] = "s3"
        return {key: self.platform.resolve_value(state, value) for key, value in config.items()}

    def public_base(self, state: EnvironmentState) -> str:
        """The gateway address object URLs are built on: ``<ENV>_PUBLIC_URL`` when the environment has its own hostname, else the platform's."""
        own = envvars.get(state.env_name, "PUBLIC_URL")
        return str(own or self.platform.settings.public_url).rstrip("/")

    def object_url(self, state: EnvironmentState, bucket: str, key: str) -> str:
        """The stable URL of an object on the application gateway. It does not depend on the storage provider."""
        path = "/".join(
            (quote(state.env_name, safe=""), quote(bucket, safe=""), quote(key, safe="/"))
        )
        return f"{self.public_base(state)}/storage/v1/files/{path}"

    def share_url(self, state: EnvironmentState, token: str) -> str:
        return f"{self.public_base(state)}/storage/v1/share/{quote(token, safe='')}"

    def signer(self, state: EnvironmentState, bucket: str) -> Signer:
        key = (state.env_name, bucket)
        signer = self._signers.get(key)
        if signer is None:
            secret = f"{self.platform.settings.internal_secret}:storage:{state.env_name}"
            signer = self._signers[key] = Signer(secret, f"{state.env_name}/{bucket}")
        return signer

    def signed_url(
        self,
        state: EnvironmentState,
        bucket: str,
        key: str,
        *,
        method: str = "GET",
        expires_in: int = 300,
        content_type: str = "",
        max_bytes: int = 0,
        credential: dict[str, Any] | None = None,
    ) -> str:
        """Mint a Pawabase URL so every signed transfer still passes through policy, sniffing and limits.

        In particular, never return a driver's native S3 presigned URL here: a direct PUT would let S3 accept bytes Pawabase has not
        inspected, and S3 presigned URLs cannot enforce an upload-size limit.
        """
        held = self.bucket(state, bucket, credential=credential)
        key = normalise(key)
        verb = method.upper()
        action = Action.READ if verb == "GET" else Action.WRITE
        if not held.policy.signable(action):
            raise PolicyRefused(action.value, key)
        grant = SignedGrant(
            key,
            verb,
            time.time() + max(1, min(int(expires_in), 7 * 24 * 3600)),
            content_type=content_type,
            max_bytes=max_bytes or held.max_bytes,
        )
        token = self.signer(state, bucket).sign(grant)
        base = self.platform.settings.public_url.rstrip("/")
        path = "/".join(
            (
                quote(state.env_name, safe=""),
                quote(bucket, safe=""),
                quote(key, safe="/"),
            )
        )
        return f"{base}/storage/v1/signed/{path}?token={quote(token, safe='')}"

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
        config = self.platform.settings.default_storage()
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
        self,
        state: EnvironmentState,
        name: str,
        *,
        credential: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> Bucket:
        """A Sillo bucket for this request, with its policy bound to *credential*.

        With *overwrite*, a write is answered by the bucket's update policy (replacing an object that exists) instead of its
        upload policy.
        """
        model: BucketModel | None = state.buckets.get(name)
        if model is None:
            from sillo.exceptions import HTTPException

            raise HTTPException(status_code=404, detail=f"no bucket {name!r}")
        policy = PolicyStorage(
            state.engine,
            read="public" if model.public else (model.read_policy or "authenticated"),
            write=(
                (model.update_policy or model.write_policy or "authenticated")
                if overwrite
                else (model.write_policy or "authenticated")
            ),
            list=model.list_policy,
            update=model.update_policy,
            delete=model.delete_policy,
            credential=credential,
            env=state.env_name,
            signed_reads=model.signed_downloads,
            signed_writes=model.signed_uploads,
        )
        max_bytes = int(
            model.max_bytes
            or self.platform.limit(
                state.env_name, "MAX_UPLOAD_BYTES", self.platform.settings.max_upload_bytes
            )
        )
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
