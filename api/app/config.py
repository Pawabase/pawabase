"""Typed settings for the API, read from the environment (``PAWABASE_*``)."""

from __future__ import annotations

from pawabase_core.settings import PlatformSettings


class ApiSettings(PlatformSettings):
    """API settings.

    Attributes:
        database_url: The runtime's own database: environments, definitions, activity.
        db_generate_schemas: Create tables at startup. For tests and throwaway
            setups only; migrations own the schema otherwise.
        default_data_url: Where an environment's Resource data lives when the
            developer has not configured a database. ``{env}`` is substituted, so
            environments never share a database.
        storage_root: Local storage root, used when the platform default is the
            ``local`` driver.
        storage_driver: The default object storage for environments that have
            not configured their own (``local``, ``s3`` or ``memory``). Empty
            means automatic: ``s3`` when ``storage_endpoint`` is set (MinIO, AWS
            S3, R2, any S3-compatible service), else ``local``. The Docker
            install points ``storage_endpoint`` at its bundled MinIO.
        storage_endpoint, storage_bucket, storage_region, storage_access_key,
        storage_secret_key, storage_prefix: The S3-compatible service and the
            remote bucket every Pawabase bucket lives in (as key prefixes).
        storage_public_endpoint: Where browsers reach the service, for presigned
            URLs, when ``storage_endpoint`` is an internal address.
        storage_path_style: Address the bucket in the URL path (MinIO, Ceph)
            rather than as a subdomain (AWS virtual-hosted style).
        request_retention_days: How long request history (the traces behind route
            statistics) is kept. ``0`` keeps it forever.
        code_path: The directory mounted code (``functions/``, ``policies/``,
            ``routes.py``) is read from.
        deployments_path: Where uploaded function artifacts live (a writable volume shared by the API, workers and scheduler); defaults to
            ``<code_path>/.deployments``, which is wrong when ``code_path`` is a read-only mount.
        public_url: The gateway's public origin, used in signed URLs and docs.
        inline_worker: Run a queue worker inside the API process. Right for a
            single-process development setup with no Redis; production runs
            ``python -m app.worker`` separately.
        inline_scheduler: Likewise for the scheduler.
    """

    service_name: str = "api"
    database_url: str = "postgres://pawabase:pawabase@127.0.0.1:5432/pawabase"
    db_generate_schemas: bool = False
    default_data_url: str = "postgres://pawabase:pawabase@127.0.0.1:5432/pawabase"
    storage_root: str = "storage/objects"
    storage_driver: str = ""
    storage_endpoint: str = ""
    storage_public_endpoint: str = ""
    storage_bucket: str = "pawabase"
    storage_region: str = "us-east-1"
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_prefix: str = ""
    storage_path_style: bool = True
    code_path: str = "code"
    #: Where ``pawabase deploy`` artifacts are stored: writable, and shared by every API and worker process. Empty means ``<code_path>/.deployments``.
    deployments_path: str = ""
    #: Install a deployment's ``functions/requirements.txt`` at deploy time. ``None`` (the default) means yes, except when ``app_env`` is ``production``,
    #: where installing packages from the network on a deploy is opt-in (``PAWABASE_FUNCTION_INSTALL=true``).
    function_install: bool | None = None
    #: Package index for those installs (empty: the installer's default).
    function_index_url: str = ""
    function_install_timeout: int = 300
    public_url: str = "http://127.0.0.1:8080"
    inline_worker: bool = True
    inline_scheduler: bool = False
    queue_prefix: str = "pawabase:queue:"
    max_upload_bytes: int = 50 * 1024 * 1024
    request_retention_days: int = 14
    query_timeout: float = 15.0
    #: Proxies between the gateway and the open internet (a load balancer is 1). The caller's address, as handed to functions, is the entry
    #: ``trusted_proxy_hops`` places from the right of ``X-Forwarded-For``: the gateway appends the address it saw, so the left side is whatever the caller sent.
    trusted_proxy_hops: int = 0


def default_storage(settings: ApiSettings) -> dict[str, object]:
    """The platform's default storage, as an environment ``infra.storage`` block.

    An environment that configures its own storage never sees this; one that
    does not gets it, so file storage works on a fresh install with no setup.
    """
    driver = settings.storage_driver or ("s3" if settings.storage_endpoint else "local")
    if driver != "s3":
        return {"driver": driver}
    return {
        "driver": "s3",
        "endpoint": settings.storage_endpoint,
        "public_endpoint": settings.storage_public_endpoint,
        "bucket": settings.storage_bucket,
        "region": settings.storage_region,
        "access_key": settings.storage_access_key,
        "secret_key": settings.storage_secret_key,
        "prefix": settings.storage_prefix,
        "path_style": settings.storage_path_style,
    }


def function_install_enabled(settings: ApiSettings) -> bool:
    return settings.app_env != "production" if settings.function_install is None else settings.function_install


def load_settings(**overrides) -> ApiSettings:
    return ApiSettings(**overrides)
