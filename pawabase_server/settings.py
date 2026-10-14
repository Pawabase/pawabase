"""Every setting the server reads, in one place.

Values come from ``PAWABASE_*`` environment variables and the ``.env`` file through :class:`sillo.config.Config`, so a missing required
secret fails at startup, not on the first request. There is one class because there is one process: the sections below group fields by
what they configure, not by which part of the server reads them.
"""

from __future__ import annotations

from typing import Literal

from sillo.config import Config

from pawabase_server.core.dburl import pooled


class Settings(Config):
    """Configuration for one Pawabase installation.

    Core
        ``app_env`` is the deployment stage of the runtime itself, not of one of its environments. ``internal_secret`` signs service
        tokens and the context the edge attaches to a request; ``jwt_master_secret`` is the root from which each environment's user-token
        signing key is derived; ``master_key`` encrypts stored secrets (rotating it makes existing secrets unreadable, so back it up with
        the database). ``project_name`` is what this runtime calls itself: Studio's title, the title of each generated API's documentation
        and the name in emails to your users.

    Database
        ``database_url`` is the runtime's own database: environments, definitions, activity, users, sessions. ``db_generate_schemas``
        creates tables at startup, for tests and throwaway setups only; migrations own the schema otherwise. ``default_data_url`` is where
        an environment's Resource data lives when the developer has not configured a database (``{env}`` is substituted, so environments
        never share one). ``db_pool_min``/``db_pool_max`` bound the connections kept open (``0`` and a small maximum let an idle deployment
        hold none); ``db_statement_cache_size`` is asyncpg's prepared-statement cache (``0`` behind PgBouncer in transaction mode).

    Storage and mail
        ``storage_*`` describe the S3-compatible service every Pawabase bucket lives in as a key prefix. ``storage_driver`` empty means
        ``s3`` when ``storage_endpoint`` is set, else ``local``. ``storage_public_endpoint`` is where browsers reach it, for presigned URLs.
        ``mail_*`` is the SMTP service every environment sends through (an environment can use another with ``<ENV>_MAIL_*``); without a
        host mail is logged and not sent.

    Functions and jobs
        ``code_path`` is where mounted code (``functions/``, ``policies/``, ``routes.py``) is read from; ``deployments_path`` is where
        uploaded artifacts live (writable; defaults to ``<code_path>/.deployments``). The queue worker and the scheduler are
        processes of their own, never tasks of the server: ``worker_mode`` is ``process`` (the default: ``serve`` starts and supervises
        them as child processes) or ``external`` (something else runs ``pawabase-server worker`` and ``pawabase-server scheduler``, as the
        compose files do). ``inline_worker`` and ``inline_scheduler`` put them inside the server process instead; off unless asked for,
        and meant for tests and throwaway runs.

    Edge (the public entry point)
        ``cors_origins``, ``rate_limit``/``rate_window``, ``max_body_bytes``, ``hosts`` (which environment each hostname serves, as
        ``environment=host[|host...]`` pairs), ``trusted_proxies`` (who may vouch for a client's address through ``X-Forwarded-For``:
        ``private``, ``*``, empty, or CIDR ranges) and the quotas.

    Identity
        ``lockout_*`` is the failed sign-ins before a temporary lock; ``config_ttl`` is how long an environment's auth configuration and
        channel rules are cached.

    Realtime
        ``max_*`` and ``rate_*`` bound what one connection may do; ``presence_ttl``, ``heartbeat_interval`` and ``sweep_interval`` keep
        presence honest across instances.
    """

    # ── listeners ────────────────────────────────────────────────────────
    #: The public entry point: clients, the SDKs and the CLI connect here.
    host: str = "0.0.0.0"
    port: int = 8080
    #: The internal listener: the application without the edge, for the services that manage it (Studio). Requests carry service tokens;
    #: it is not for clients and must never be published. It binds the loopback by default; a container binds ``0.0.0.0`` on a private
    #: network.
    internal_host: str = "127.0.0.1"
    internal_port: int = 8001
    log_level: str = "info"
    #: Which peers uvicorn believes about ``X-Forwarded-*`` (the edge applies its own ``trusted_proxies`` on top).
    forwarded_allow_ips: str = "*"
    #: Uvicorn's event loop and HTTP parser. Docker defaults to uvloop + httptools; ``auto`` keeps Uvicorn's portable selection.
    http_loop: Literal["auto", "asyncio", "uvloop"] = "auto"
    http_protocol: Literal["auto", "h11", "httptools"] = "auto"
    #: Socket backlog and connection behaviour for both HTTP listeners. ``0`` means no application-level concurrency cap.
    http_backlog: int = 2048
    http_keep_alive: int = 10
    http_limit_concurrency: int = 0

    # ── core ─────────────────────────────────────────────────────────────
    app_env: Literal["local", "testing", "staging", "production"] = "local"
    debug: bool = False
    project_name: str = "Pawabase"
    public_url: str = "http://127.0.0.1:8080"
    public_gateway_url: str = "http://localhost:8080"

    internal_secret: str = "dev-internal-secret-change-me-please"
    jwt_master_secret: str = "dev-jwt-master-secret-change-me-please"
    master_key: str = "dev-master-key-change-me-please-32bytes"

    redis_url: str = ""

    # ── database ─────────────────────────────────────────────────────────
    database_url: str = "postgres://pawabase:pawabase@127.0.0.1:5432/pawabase"
    db_generate_schemas: bool = False
    #: Bring the database up to date when the server starts (create the schema on an empty database, apply pending migrations on an
    #: existing one). Set ``PAWABASE_MIGRATE=false`` when something else owns the schema; the server then refuses to start on an empty one.
    migrate: bool = True
    default_data_url: str = "postgres://pawabase:pawabase@127.0.0.1:5432/pawabase"
    db_pool_min: int | None = None
    db_pool_max: int | None = None
    db_statement_cache_size: int | None = None
    query_timeout: float = 15.0

    # ── storage ──────────────────────────────────────────────────────────
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
    deployment_mirror: bool = True
    max_upload_bytes: int = 50 * 1024 * 1024

    # ── mail ─────────────────────────────────────────────────────────────
    mail_host: str = ""
    mail_port: int = 587
    mail_username: str = ""
    mail_password: str = ""
    mail_from: str = ""
    mail_reply_to: str = ""
    mail_use_ssl: bool | None = None
    mail_use_tls: bool | None = None
    mail_suppress: bool = False

    # ── functions, flows and jobs ────────────────────────────────────────
    code_path: str = "code"
    #: Where ``pawabase deploy`` artifacts are stored: writable. Empty means ``<code_path>/.deployments``.
    deployments_path: str = ""
    #: Install a deployment's ``functions/requirements.txt`` at deploy time. ``None`` means yes, except in ``production``, where installing
    #: packages from the network on a deploy is opt-in (``PAWABASE_FUNCTION_INSTALL=true``).
    function_install: bool | None = None
    #: Package index for those installs (empty: the installer's default).
    function_index_url: str = ""
    function_install_timeout: int = 300
    #: Where a deployment's functions run: ``process`` (worker processes of their own, with limits: the default) or ``inprocess``.
    #: ``<ENV>_FUNCTION_ISOLATION`` sets it for one environment.
    function_isolation: str = "process"
    #: Address-space limit of a sandbox worker, in MB (not enforced on macOS).
    sandbox_memory_mb: int = 1024
    #: Most worker processes one deployment may use at once; further calls wait for one.
    sandbox_workers: int = 4
    #: How long an unused worker is kept before it is stopped.
    sandbox_idle_seconds: int = 300
    #: Comma-separated host names a function's ``http_request`` may reach although they resolve to a private address.
    outbound_allow_hosts: str = ""
    inline_worker: bool = False
    inline_scheduler: bool = False
    #: ``process``: ``serve`` runs the worker and the scheduler as supervised child processes. ``external``: it starts neither.
    worker_mode: str = "process"
    #: Jobs a worker process runs at once.
    worker_concurrency: int = 4
    queue_prefix: str = "pawabase:queue:"
    #: Put this in front of every Redis channel, cache key and queue name, so runtimes can share one Redis without hearing each other.
    #: Empty keeps today's names. Set it to something unique per runtime when a Redis is shared.
    namespace: str = ""
    #: Install a deployment's libraries from wheels only (``--only-binary :all:``): a source distribution runs its build script, which is
    #: customer-controlled code, with the API's privileges.
    function_install_binary_only: bool = False

    # ── projects ─────────────────────────────────────────────────────────
    #: The project a fresh installation starts with, and the one environments from before projects existed are attached to.
    default_project: str = "default"
    #: The database that project is bound to. Empty uses ``default_data_url``, which is where an installation's data already is.
    default_database_url: str = ""

    # ── runtime agent (Cloud-managed deployments only) ───────────────────
    #: Where this runtime's agent polls for provisioning jobs. Empty: no agent runs here.
    agent_url: str = ""
    agent_id: str = ""
    #: The control plane's publishable key, which its gateway asks of every call (it opens the door; the agent's signature is the credential).
    agent_api_key: str = ""
    agent_token: str = ""
    #: The runtime pool this agent serves. It only takes jobs assigned to it.
    agent_pool: str = ""
    agent_poll_seconds: float = 5.0
    #: Derives the passwords of managed project databases from their ids, so a job names a database without carrying a password.
    agent_secret_seed: str = ""
    request_retention_days: int = 14
    #: How often function and flow run records are written, in seconds. They gather in memory and go to the database in one batch.
    #: ``0`` writes each record before the request returns.
    record_buffer_seconds: float = 0.5

    # ── edge ─────────────────────────────────────────────────────────────
    #: ``*`` lets any origin call the data plane. Browser clients send keys and tokens in headers, never cookies, so this is the usual
    #: setting for a backend-as-a-service. Environments narrow it per publishable key with ``settings.cors_origins``.
    cors_origins: str = "*"
    key_cache_ttl: int = 30
    #: Seconds a resolved key may still be used when it cannot be re-checked.
    key_stale_ttl: int = 60
    rate_limit: int = 600
    rate_window: int = 60
    max_body_bytes: int = 100 * 1024 * 1024
    hosts: str = ""
    trusted_proxies: str = "private"
    #: Proxies between the edge and the open internet (a load balancer is 1). The caller's address, as handed to functions, is the entry
    #: ``trusted_proxy_hops`` places from the right of ``X-Forwarded-For``.
    trusted_proxy_hops: int = 0
    #: Methods the edge accepts; anything else is answered 405.
    allowed_methods: str = "GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS"
    #: Seconds between status-page probes of the components; ``0`` turns probing off.
    status_probe_interval: float = 300
    #: Seconds between refreshes of the verified custom domains; ``0`` turns it off.
    domain_refresh_interval: float = 30
    daily_request_limit: int = 0
    monthly_request_limit: int = 0
    request_quota_reservation: int = 64
    max_active_connections: int = 0
    max_users: int = 0
    max_environments: int = 0
    max_api_keys_per_environment: int = 0

    # ── identity ─────────────────────────────────────────────────────────
    lockout_threshold: int = 10
    lockout_minutes: int = 15
    config_ttl: int = 10

    # ── realtime ─────────────────────────────────────────────────────────
    max_channels: int = 100
    max_message_bytes: int = 64 * 1024
    max_payload_bytes: int = 32 * 1024
    max_presence_bytes: int = 2 * 1024
    max_history: int = 1000
    history_bytes: int = 256 * 1024
    stale_config_ttl: int = 300
    idle_timeout: float = 300.0
    max_connections_per_env: int = 0
    max_connections_per_identity: int = 20
    rate_per_second: float = 20.0
    rate_burst: int = 40
    abuse_strikes: int = 5
    presence_ttl: float = 45.0
    heartbeat_interval: float = 10.0
    sweep_interval: float = 15.0
    forward_queue_size: int = 1000
    drain_grace: float = 5.0

    class Env:
        env_prefix = "PAWABASE_"

    # ── derived values ───────────────────────────────────────────────────

    @property
    def function_install_enabled(self) -> bool:
        return (
            self.app_env != "production" if self.function_install is None else self.function_install
        )

    def tuned(self, url: str) -> str:
        """*url* with this deployment's pool settings applied (Postgres only)."""
        return pooled(
            url,
            minimum=self.db_pool_min,
            maximum=self.db_pool_max,
            statement_cache=self.db_statement_cache_size,
        )

    def origins(self) -> list[str]:
        """The CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def default_storage(self) -> dict[str, object]:
        """The platform's default storage, as an environment ``infra.storage`` block.

        An environment that configures its own storage never sees this; one that does not gets it, so file storage works on a fresh
        install with no setup.
        """
        driver = self.storage_driver or ("s3" if self.storage_endpoint else "local")
        if driver != "s3":
            return {"driver": driver}
        return {
            "driver": "s3",
            "endpoint": self.storage_endpoint,
            "public_endpoint": self.storage_public_endpoint,
            "bucket": self.storage_bucket,
            "region": self.storage_region,
            "access_key": self.storage_access_key,
            "secret_key": self.storage_secret_key,
            "prefix": self.storage_prefix,
            "path_style": self.storage_path_style,
        }

    def validate_for_production(self) -> list[str]:
        """Name every development default still in place; the server refuses to start in production while any exist."""
        problems = []
        if self.worker_mode not in ("process", "external"):
            problems.append("PAWABASE_WORKER_MODE must be 'process' or 'external'")
        for field in ("internal_secret", "jwt_master_secret", "master_key"):
            value = getattr(self, field)
            if value.startswith("dev-") or len(value) < 32:
                problems.append(
                    f"PAWABASE_{field.upper()} must be set to a random value of 32+ characters"
                )
        return problems
