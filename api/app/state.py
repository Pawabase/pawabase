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

from app.data.source import DataSource
from app.data.store import ResourceSpec, ResourceStore
from database.models import (
    Bucket,
    Environment,
    EventSubscription,
    Flow,
    InboundHook,
    MailTemplate,
    PolicyDef,
    Resource,
    RouteDef,
    Schedule,
    SchemaDef,
    Secret,
    TransformerDef,
    WebhookEndpoint,
)
from pawabase_core.policies import Policy, PolicyEngine, python_policies
from pawabase_core.schemas import compile_schemas

if TYPE_CHECKING:
    from app.platform import Platform


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
        configured = self.infra.get("database_url")
        if configured:
            return self.platform.resolve_value(self, configured)
        url = self.platform.settings.default_data_url.format(env=self.env_name)
        if url.startswith(("postgres://", "postgresql://")) and "schema=" not in url:
            # One shared Postgres database, one schema per environment (see ``data_schema``).
            url += ("&" if "?" in url else "?") + f"schema={self.data_schema()}"
        return url

    def data_schema(self) -> str:
        """The Postgres schema holding this environment's resource tables in the shared default database."""
        scope = hashlib.sha256(self.env_name.encode()).hexdigest()[:12]
        return f"pb_{scope}"

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
            self.database_url(), alias=self.env_name
        )

    def spec(self, name: str) -> ResourceSpec:
        spec = self.specs.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no resource {name!r}")
        return spec

    async def store(self, name: str) -> ResourceStore:
        spec = self.spec(name)
        return ResourceStore(await self.source(), replace(spec, table=self.database_table(spec.table)))

    # ── compiled API ─────────────────────────────────────────────────────

    async def compiled(self) -> Any:
        """The Sillo application serving this environment's data plane."""
        if self._compiled is None:
            async with self._lock:
                if self._compiled is None:
                    from app.compiler.build import compile_environment

                    self._compiled = compile_environment(self)
        return self._compiled


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
    for secret in secrets:
        try:
            state.secret_values[secret.name] = platform.box.open(secret.ciphertext)
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
        from app.releases import state_from_snapshot
        from database.models import DefinitionRevision, Release

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
        from database.models import ApiVersion

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
