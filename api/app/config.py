"""Typed settings for the API, read from the environment (``PAWABASE_*``)."""

from __future__ import annotations

from pawabase_kit.settings import PlatformSettings


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
        public_url: The gateway's public origin, used in signed URLs and docs.
        inline_worker: Run a queue worker inside the API process. Right for a
            single-process development setup with no Redis; production runs
            ``python -m app.worker`` separately.
        inline_scheduler: Likewise for the scheduler.
    """

    service_name: str = "api"
    database_url: str = "sqlite://storage/api.db"
    db_generate_schemas: bool = False
    default_data_url: str = "sqlite://storage/data/{project}__{env}.db"
    storage_root: str = "storage/objects"
    code_path: str = "code"
    public_url: str = "http://127.0.0.1:8080"
    inline_worker: bool = True
    inline_scheduler: bool = False
    queue_prefix: str = "pawabase:queue:"
    max_upload_bytes: int = 50 * 1024 * 1024
    query_timeout: float = 15.0


def load_settings(**overrides) -> ApiSettings:
    return ApiSettings(**overrides)
