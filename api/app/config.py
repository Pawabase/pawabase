"""Typed settings for the API, read from the environment (``PAWABASE_*``)."""

from __future__ import annotations

from pawabase_core.settings import PlatformSettings


class ApiSettings(PlatformSettings):
    """API settings.

    Attributes:
        database_url: The platform database: projects, definitions, activity.
        db_generate_schemas: Create tables at startup. For tests and throwaway
            setups only; migrations own the schema otherwise.
        default_data_url: Where an environment's Resource data lives when the
            developer has not configured a database. ``{project}`` and
            ``{env}`` are substituted, so environments never share a database.
        storage_root: Default local storage root for environments that have
            not configured storage.
        code_path: Where project code (functions, routes, policies) is mounted.
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
    code_path: str = "code"
    #: Where ``pawabase deploy`` artifacts are stored: writable, and shared by every API and worker process. Empty means ``<code_path>/.deployments``.
    deployments_path: str = ""
    public_url: str = "http://127.0.0.1:8080"
    inline_worker: bool = True
    inline_scheduler: bool = False
    queue_prefix: str = "pawabase:queue:"
    max_upload_bytes: int = 50 * 1024 * 1024
    query_timeout: float = 15.0
    #: Proxies between the gateway and the open internet (a load balancer is 1). The caller's address, as handed to functions, is the entry
    #: ``trusted_proxy_hops`` places from the right of ``X-Forwarded-For``: the gateway appends the address it saw, so the left side is whatever the caller sent.
    trusted_proxy_hops: int = 0


def load_settings(**overrides) -> ApiSettings:
    return ApiSettings(**overrides)
