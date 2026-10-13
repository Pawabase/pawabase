"""An environment's definitions, loaded once per version.

Every definition change bumps ``Environment.version``. A request reads the
current version (one indexed query) and reuses the loaded
:class:`EnvironmentState`, including its policy engine and compiled API, until
the version moves. Several API instances therefore agree without any
invalidation messages.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from sillo.exceptions import HTTPException
from tortoise.expressions import F

from pawabase_server.core import envvars
from pawabase_server.core.schemas import compile_schemas
from pawabase_server.data.source import DataSource
from pawabase_server.data.store import ResourceSpec, ResourceStore
from pawabase_server.db.models import (
    Bucket,
    Environment,
    EventSubscription,
    Flow,
    InboundHook,
    MailTemplate,
    PolicyDef,
    Project,
    ProjectDatabaseBinding,
    Resource,
    RouteDef,
    Schedule,
    SchemaDef,
    Secret,
    TransformerDef,
    WebhookEndpoint,
)
from pawabase_server.policies import Policy, PolicyEngine, python_policies
from pawabase_server.projects.refs import RefError, resolve_ref

if TYPE_CHECKING:
    from pawabase_server.platform import Platform


def data_schema_name(env_name: str) -> str:
    """The Postgres schema an environment's resource tables live in on the shared default database."""
    return f"pb_{hashlib.sha256(env_name.encode()).hexdigest()[:12]}"


def project_data_url(base: str, schema: str) -> str:
    """An environment's address inside its project's database: its own schema (PostgreSQL), file (SQLite) or database prefix (MySQL).

    PostgreSQL: *base* with ``schema=<schema>``. SQLite: the ``{env}`` of *base* becomes the schema name, or, when *base* has none, *base*
    names a directory and the file is ``<schema>.db`` inside it.
    """
    if base.startswith(("postgres://", "postgresql://")):
        if "schema=" in base:
            return base
        return base + ("&" if "?" in base else "?") + f"schema={schema}"
    if base.startswith(("sqlite://", "sqlite:")):
        if "{env}" in base:
            return base.replace("{env}", schema)
        if base.endswith(".db") or base.endswith(".sqlite"):
            return base  # one file for the project; the schema name is not a file here
        return f"{base.rstrip('/')}/{schema}.db"
    return base.replace("{env}", schema)


def default_data_url(template: str, env_name: str) -> str:
    """The platform default data URL for one environment: the template filled in, with its own schema on Postgres."""
    # ``{project}`` from an older configuration names nothing now: drop it, with its separator.
    template = template.replace("{project}__", "").replace("{project}", "")
    url = template.replace("{env}", env_name)
    if url.startswith(("postgres://", "postgresql://")) and "schema=" not in url:
        # One shared Postgres database, one schema per environment (see ``data_schema_name``).
        url += ("&" if "?" in url else "?") + f"schema={data_schema_name(env_name)}"
    return url


@dataclass
class EnvironmentState:
    """Everything defined for one environment, at one version."""

    platform: Platform
    environment: Environment
    env_name: str
    version: int
    api_version: str | None = None
    release_id: str | None = None
    revision_id: str | None = None
    resources: dict[str, Resource] = field(default_factory=dict)
    specs: dict[str, ResourceSpec] = field(default_factory=dict)
    routes: list[RouteDef] = field(default_factory=list)
    flows: dict[str, Flow] = field(default_factory=dict)
    schemas: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    transformers: dict[str, dict[str, Any]] = field(default_factory=dict)
    policies: dict[str, Policy] = field(default_factory=dict)
    buckets: dict[str, Bucket] = field(default_factory=dict)
    mail_templates: dict[str, MailTemplate] = field(default_factory=dict)
    subscriptions: list[EventSubscription] = field(default_factory=list)
    webhooks: list[WebhookEndpoint] = field(default_factory=list)
    inbound_hooks: dict[str, InboundHook] = field(default_factory=dict)
    schedules: list[Schedule] = field(default_factory=list)
    secret_values: dict[str, str] = field(default_factory=dict)
    #: The project this environment belongs to, and the address of that project's database (resolved when the state was loaded).
    project_id: str | None = None
    project_slug: str | None = None
    project_is_default: bool = True
    project_database: str | None = None
    #: The bucket the project's own storage lives in, when its control plane made one (the S3 credentials stay the deployment's).
    project_bucket: str | None = None
    project_problem: str | None = None
    engine: PolicyEngine = field(default_factory=PolicyEngine)
    compiled_schemas: dict[str, Any] = field(default_factory=dict)
    schema_problems: list[str] = field(default_factory=list)
    _compiled: Any = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def key(self) -> str:
        return self.env_name

    @property
    def infra(self) -> dict[str, Any]:
        return self.environment.infra or {}

    @property
    def settings(self) -> dict[str, Any]:
        return self.environment.settings or {}

    # ── data ─────────────────────────────────────────────────────────────

    def database_url(self) -> str:
        """Where this environment's resource data lives.

        In order: ``<ENV>_DATA_URL`` from the process environment; the deprecated
        ``infra.database_url`` an older install stored on the environment (it wins over the project so that attaching an environment to
        a project never moves where its data is read from); the project's database binding, in the environment's own schema; the
        platform default (``PAWABASE_DEFAULT_DATA_URL``), one schema per environment.
        """
        own = self.platform.env_setting(self, "DATA_URL")
        if own:
            return own
        configured = self.infra.get("database_url")
        if configured:
            self.platform.deprecated(
                self.env_name,
                "infra.database_url",
                f"set {envvars.prefix_for(self.env_name)}_DATA_URL instead",
            )
            return self.platform.resolve_value(self, configured)
        if self.project_database:
            return project_data_url(self.project_database, self.data_schema())
        if self.project_problem:
            raise HTTPException(
                status_code=503,
                detail=f"this project's database cannot be reached: {self.project_problem}",
            )
        return default_data_url(self.platform.settings.default_data_url, self.env_name)

    def database_source(self) -> str:
        """Where the data database comes from: ``environment`` (a variable), ``project`` (its binding), ``stored`` (deprecated ``infra``)
        or ``default``."""
        if envvars.get(self.env_name, "DATA_URL"):
            return "environment"
        if self.infra.get("database_url"):
            return "stored"
        if self.project_database and not self.project_is_default:
            return "project"
        return "default"

    def data_schema(self) -> str:
        """The schema holding this environment's resource tables: the one recorded on the environment, else the legacy derived name."""
        return self.environment.schema_name or data_schema_name(self.env_name)

    def database_table(self, table: str) -> str:
        """The physical table name: the resource's own.

        Environments sharing the default Postgres database are kept apart by
        *schema* (``database_url`` points each at its own ``search_path``), not by
        renaming tables. A table keeps the name the developer gave it, so the SQL a
        function or a flow writes (``SELECT … FROM orders``) means the same thing in
        every database, whatever hosts it.
        """
        return table

    async def source(self) -> DataSource:
        return await self.platform.sources.get(
            self.platform.settings.tuned(self.database_url()), alias=self.env_name
        )

    def spec(self, name: str) -> ResourceSpec:
        spec = self.specs.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no resource {name!r}")
        return spec

    async def store(self, name: str) -> ResourceStore:
        spec = self.spec(name)
        return ResourceStore(
            await self.source(), replace(spec, table=self.database_table(spec.table))
        )

    # ── compiled API ─────────────────────────────────────────────────────

    async def compiled(self) -> Any:
        """The Sillo application serving this environment's data plane."""
        if self._compiled is None:
            async with self._lock:
                if self._compiled is None:
                    from pawabase_server.compiler.build import compile_environment

                    self._compiled = compile_environment(self)
        return self._compiled


async def attach_project(platform: Platform, state: EnvironmentState) -> None:
    """Note which project the environment belongs to and where that project's database is, from its binding.

    A binding that cannot be read is not an error here: the environment loads, and only a request that needs the data says why it cannot
    have it. Nothing is guessed in place of a project's database.
    """
    project_id = state.environment.project_id
    if not project_id:
        return
    state.project_id = project_id
    project = await Project.get_or_none(id=project_id)
    state.project_slug = project.slug if project else None
    state.project_is_default = bool(project and project.is_default)
    if project is not None:
        state.project_bucket = ((project.details or {}).get("storage") or {}).get("bucket")
    binding = await ProjectDatabaseBinding.get_or_none(project_id=project_id)
    if binding is None:
        return
    try:
        state.project_database = resolve_ref(platform.box, binding.connection_ref)
    except RefError as exc:
        state.project_problem = str(exc)


async def load_state(platform: Platform, environment: Environment) -> EnvironmentState:
    """Read every definition of *environment*."""
    state = EnvironmentState(
        platform=platform,
        environment=environment,
        env_name=environment.name,
        version=environment.version,
    )
    env_id = environment.id
    (
        resources,
        routes,
        flows,
        schemas,
        transformers,
        policies,
        buckets,
        templates,
        subscriptions,
        webhooks,
        hooks,
        schedules,
        secrets,
    ) = await asyncio.gather(
        Resource.filter(environment_id=env_id).all(),
        RouteDef.filter(environment_id=env_id, enabled=True).all(),
        Flow.filter(environment_id=env_id).all(),
        SchemaDef.filter(environment_id=env_id).all(),
        TransformerDef.filter(environment_id=env_id).all(),
        PolicyDef.filter(environment_id=env_id).all(),
        Bucket.filter(environment_id=env_id).all(),
        MailTemplate.filter(environment_id=env_id).all(),
        EventSubscription.filter(environment_id=env_id, enabled=True).all(),
        WebhookEndpoint.filter(environment_id=env_id, enabled=True).all(),
        InboundHook.filter(environment_id=env_id).all(),
        Schedule.filter(environment_id=env_id).all(),
        Secret.filter(environment_id=env_id).all(),
    )
    state.resources = {r.name: r for r in resources}
    state.specs = {r.name: ResourceSpec.from_model(r) for r in resources}
    state.routes = list(routes)
    state.flows = {f.name: f for f in flows}
    state.schemas = {s.name: list(s.fields_ or []) for s in schemas}
    state.transformers = {t.name: dict(t.definition or {}) for t in transformers}
    state.policies = {p.name: Policy(p.name, p.condition, p.description) for p in policies}
    state.buckets = {b.name: b for b in buckets}
    state.mail_templates = {t.name: t for t in templates}
    state.subscriptions = list(subscriptions)
    state.webhooks = list(webhooks)
    state.inbound_hooks = {h.slug: h for h in hooks}
    state.schedules = list(schedules)
    await attach_project(platform, state)
    for secret in secrets:
        try:
            state.secret_values[secret.name] = platform.box.open(secret.ciphertext, state.env_name)
        except Exception:  # sealed under another master key
            continue
    state.engine = PolicyEngine(state.policies, python=python_policies())
    # A broken schema is skipped and reported; the rest stay usable.
    state.schema_problems = []
    state.compiled_schemas = compile_schemas(state.schemas, problems=state.schema_problems)
    return state


class EnvironmentCache:
    """Loaded :class:`EnvironmentState` objects, by environment and version."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._states: dict[str, EnvironmentState] = {}
        self._release_states: dict[tuple[str, str], EnvironmentState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, env: str) -> EnvironmentState:
        environment = await Environment.filter(name=env).first()
        if environment is None:
            raise HTTPException(status_code=404, detail=f"no environment {env!r}")
        key = env
        cached = self._states.get(key)
        if cached is not None and cached.version == environment.version:
            return cached
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._states.get(key)
            if cached is not None and cached.version == environment.version:
                return cached
            state = await load_state(self.platform, environment)
            self._states[key] = state
            return state

    async def get_release(self, env: str, release_id: str) -> EnvironmentState:
        from pawabase_server.db.models import DefinitionRevision, Release
        from pawabase_server.environments.releases import state_from_snapshot

        key = (env, release_id)
        cached = self._release_states.get(key)
        if cached is not None:
            return cached
        release = (
            await Release.filter(id=release_id, environment__name=env)
            .select_related("environment")
            .first()
        )
        if release is None:
            raise HTTPException(status_code=404, detail=f"no release {release_id!r}")
        revision = await DefinitionRevision.get_or_none(
            id=release.revision_id, environment_id=release.environment_id
        )
        if revision is None:
            raise HTTPException(status_code=409, detail="the release revision is missing")
        state = await state_from_snapshot(
            self.platform,
            release.environment,
            revision.snapshot,
            api_version=release.api_version,
            release_id=release.id,
            revision_id=revision.id,
        )
        self._release_states[key] = state
        return state

    async def get_version(self, env: str, version: str) -> EnvironmentState:
        from pawabase_server.db.models import ApiVersion

        api_version = await ApiVersion.filter(environment__name=env, name=version).first()
        # An environment stays live on v1 until it explicitly activates a release.
        if api_version is None and version == "v1":
            state = await self.get(env)
            state.api_version = "v1"
            return state
        if api_version is None:
            raise HTTPException(status_code=404, detail=f"API version {version!r} does not exist")
        if api_version.status in ("sunset", "disabled"):
            raise HTTPException(
                status_code=410, detail=f"API version {version!r} is {api_version.status}"
            )
        if not api_version.active_release_id:
            if version == "v1":
                state = await self.get(env)
                state.api_version = "v1"
                return state
            raise HTTPException(
                status_code=503, detail=f"API version {version!r} has no active release"
            )
        return await self.get_release(env, api_version.active_release_id)

    def forget(self, env: str | None = None) -> None:
        if env is None:
            self._states.clear()
            self._release_states.clear()
            return
        self._states.pop(env, None)
        for key in [k for k in self._release_states if k[0] == env]:
            del self._release_states[key]


async def bump(environment_id: str) -> None:
    """Mark an environment's definitions as changed."""
    await Environment.filter(id=environment_id).update(version=F("version") + 1)
