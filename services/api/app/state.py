"""An environment's definitions, loaded once per version.

Every definition change bumps ``Environment.version``. A request reads the
current version (one indexed query) and reuses the loaded
:class:`EnvironmentState`, including its policy engine and compiled API, until
the version moves. Several API instances therefore agree without any
invalidation messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pawabase_kit.policies import Policy, PolicyEngine, python_policies
from pawabase_kit.schemas import compile_schemas
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

if TYPE_CHECKING:
    from app.platform import Platform


@dataclass
class EnvironmentState:
    """Everything defined for one project environment, at one version."""

    platform: Platform
    environment: Environment
    project_ref: str
    project_name: str
    env_name: str
    version: int
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
    _compiled: Any = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def key(self) -> tuple[str, str]:
        return (self.project_ref, self.env_name)

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
        return self.platform.settings.default_data_url.format(project=self.project_ref, env=self.env_name)

    async def source(self) -> DataSource:
        return await self.platform.sources.get(self.database_url(), alias=f"{self.project_ref}:{self.env_name}")

    def spec(self, name: str) -> ResourceSpec:
        spec = self.specs.get(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no resource {name!r}")
        return spec

    async def store(self, name: str) -> ResourceStore:
        return ResourceStore(await self.source(), self.spec(name))

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
    project = environment.project
    state = EnvironmentState(
        platform=platform,
        environment=environment,
        project_ref=project.ref,
        project_name=project.name,
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
    try:
        state.compiled_schemas = compile_schemas(state.schemas)
    except Exception:  # a broken reusable schema must not take down the environment
        state.compiled_schemas = {}
    return state


class EnvironmentCache:
    """Loaded :class:`EnvironmentState` objects, by environment and version."""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._states: dict[tuple[str, str], EnvironmentState] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def get(self, project: str, env: str) -> EnvironmentState:
        environment = await Environment.filter(project__ref=project, name=env).select_related("project").first()
        if environment is None:
            raise HTTPException(status_code=404, detail=f"no environment {project}/{env}")
        key = (project, env)
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

    def forget(self, project: str | None = None) -> None:
        if project is None:
            self._states.clear()
            return
        for key in [k for k in self._states if k[0] == project]:
            del self._states[key]


async def bump(environment_id: int) -> None:
    """Mark an environment's definitions as changed."""
    await Environment.filter(id=environment_id).update(version=F("version") + 1)
