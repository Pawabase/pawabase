"""The API's long-lived services, assembled once per process.

:class:`Platform` holds what handlers, jobs, the event processor and the
scheduler share: the event bus (Sillo events), the queue connection (Sillo
work), the cache (Sillo cache), storage and mail managers, the developer
database pool, the realtime hub, the secret box, and the
per-environment definition cache. It is bound process-wide so a queued job,
which has no request, can reach it the same way a handler does.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from sillo.cache import BaseCache, MemoryCache
from sillo.cache import base as cache_base
from tortoise.exceptions import IntegrityError

from pawabase_server.core import envvars, namespace
from pawabase_server.core.context import PlatformContext
from pawabase_server.core.crypto import SecretBox, is_reference, reference_name
from pawabase_server.core.events import EventBus, PlatformEvent
from pawabase_server.core.telemetry import note, span
from pawabase_server.data.source import DataSourcePool
from pawabase_server.environments.state import EnvironmentCache, EnvironmentState
from pawabase_server.functions.deployment_mirror import DeploymentMirror
from pawabase_server.functions.deployments import Deployments
from pawabase_server.functions.execution_limits import ProjectExecutionLimiter
from pawabase_server.functions.record_buffer import RecordBuffer
from pawabase_server.functions.registry import (
    MAIN,
    RUNTIME,
    FunctionSpec,
    ProjectCode,
    clear_functions,
    list_resolved_functions,
    load_code_dir,
    resolve_function,
)
from pawabase_server.functions.runtime_rpc import Transactions
from pawabase_server.functions.sandbox import SandboxPool
from pawabase_server.functions.sandbox.host import Limits, resolve_confinement
from pawabase_server.functions.http_functions import HttpFunctionSpec
from pawabase_server.functions.service.gateway import gateway_for
from pawabase_server.infra.mail import MailManager
from pawabase_server.infra.storage.manager import StorageManager
from pawabase_server.projects.quotas import UsageMeter
from pawabase_server.settings import Settings

if TYPE_CHECKING:
    from sillo.work.queue import QueueConnection

logger = logging.getLogger("pawabase.api")

#: Queues the platform itself uses. Workers listen to these plus any
#: developer queues named in PAWABASE_WORKER_QUEUES.
PLATFORM_QUEUES = ["events", "webhooks", "flows", "functions", "mail", "default"]

_current: Platform | None = None


def get_platform() -> Platform:
    if _current is None:
        raise RuntimeError("the platform has not been started")
    return _current


class Platform:
    """Shared services for one API process."""

    def __init__(
        self,
        settings: Settings,
        *,
        cache: BaseCache | None = None,
        queue: QueueConnection | None = None,
        bus: EventBus | None = None,
    ) -> None:
        namespace.configure(settings.namespace)
        self.settings = settings
        self._deprecations: set[tuple[str, str]] = set()
        self.box = SecretBox(settings.master_key)
        self.sources = DataSourcePool()
        self.envs = EnvironmentCache(self)
        self.storage = StorageManager(self)
        self.mail = MailManager(self)
        self.bus = bus or EventBus.from_url(settings.redis_url or None, source="api")
        self.cache = cache or self._build_cache()
        self.queue = queue or self._build_queue()
        #: The realtime hub, attached when the server is assembled (``None`` where only the platform runs, as in some tests).
        self.realtime: Any = None
        self.outbound = httpx.AsyncClient(
            timeout=30.0, follow_redirects=False, headers={"user-agent": "Pawabase/0.1"}
        )
        self.code: ProjectCode | None = None
        self.deployment_mirror = DeploymentMirror(self)
        self.records = RecordBuffer(settings.record_buffer_seconds)
        self.sandboxes = SandboxPool(
            Limits(
                memory_mb=settings.sandbox_memory_mb,
                workers=settings.sandbox_workers,
                idle_seconds=settings.sandbox_idle_seconds,
                confinement=resolve_confinement(settings.sandbox_confinement),
            )
        )
        self.project_execution = ProjectExecutionLimiter()
        #: Monthly plan metering (function invocations, flow runs): see ``projects/quotas.py``.
        self.usage = UsageMeter()
        self.sandbox_transactions = Transactions()
        self.deployments = Deployments(
            settings.deployments_path or Path(settings.code_path) / ".deployments",
            install_requirements=settings.function_install_enabled,
            index_url=settings.function_index_url,
            install_timeout=settings.function_install_timeout,
            binary_only=settings.function_install_binary_only,
            vendor=settings.function_vendor_enabled,
            vendor_timeout=settings.function_vendor_timeout,
            npm_registry=settings.function_npm_registry,
            http_limits={
                "timeout": settings.functions_max_timeout_seconds,
                "memory": settings.functions_memory_mb,
                "concurrency": max(settings.functions_worker_concurrency, 1) * 8,
            },
        )
        self.deployments.confinement = self.sandboxes.limits.confinement
        #: Where functions run: this process, or the runtime service at ``PAWABASE_FUNCTIONS_RUNTIME_URL``.
        self.functions = gateway_for(self)
        self.app: Any = None
        self.app: Any = None
        self.started_at = datetime.now(UTC)

    # ── construction ─────────────────────────────────────────────────────

    def _build_cache(self) -> BaseCache:
        if self.settings.redis_url:
            from sillo.cache import RedisCache

            return RedisCache(url=self.settings.redis_url, namespace=namespace.scoped("pawabase"))
        return MemoryCache(namespace="pawabase")

    def _build_queue(self) -> Any:
        from sillo.work.commands import connection_for

        return connection_for(
            self.settings.redis_url or None, prefix=namespace.scoped(self.settings.queue_prefix)
        )

    def bind(self) -> Platform:
        global _current
        _current = self
        return self

    async def start(self) -> None:
        self.bind()
        await self.bus.start()
        self.records.start()
        logger.info(await self.storage.prepare_default())
        try:
            await self.deployment_mirror.restore()
        except Exception as exc:  # a failed restore must not stop the API starting
            logger.error("could not restore function deployments from storage: %s", exc)

    async def audit_configuration(self) -> None:
        """Say, once at start-up, which configuration is kept where it should not be.

        An environment that still stores ``infra`` settings gets one warning per setting, and any
        ``<ENV>_...`` variable that looks like a setting but names none (a typo) is reported, so
        a misspelt ``PRODUCTION_STORAGE_BUKET`` is not silently ignored.
        """
        from pawabase_server.db.models import Environment

        try:
            environments = await Environment.all()
        except Exception:  # the schema may not be migrated yet; nothing to audit then
            return
        for environment in environments:
            infra = environment.infra or {}
            prefix = envvars.prefix_for(environment.name)
            if infra.get("database_url"):
                self.deprecated(
                    environment.name, "infra.database_url", f"set {prefix}_DATA_URL instead"
                )
            if infra.get("storage"):
                self.deprecated(
                    environment.name, "infra.storage", f"set {prefix}_STORAGE_* variables instead"
                )
            if infra.get("mail"):
                self.deprecated(
                    environment.name,
                    "infra.mail",
                    f"set PAWABASE_MAIL_* or {prefix}_MAIL_* variables "
                    "(`python -m pawabase_server.environments.export_config` prints them from the old settings)",
                    removed=True,
                )
        for env, variables in envvars.unrecognised([e.name for e in environments]).items():
            logger.warning(
                "environment %r: %s name no known setting and are ignored (see pawabase_server.core.envvars.KEYS)",
                env,
                ", ".join(variables),
            )

    async def stop(self) -> None:
        await self.functions.close()
        await self.sandboxes.close()  # idempotent: the embedded runtime may have closed it already
        await self.records.stop()  # before the database closes
        await self.bus.stop()
        await self.sources.close()
        await self.storage.close()
        await self.deployment_mirror.close()
        await self.mail.close()
        await self.outbound.aclose()

    # ── environments ─────────────────────────────────────────────────────

    async def state(self, env: str) -> EnvironmentState:
        state = await self.envs.get(env)
        self.ensure_code()
        self.deployments.ensure_loaded(env)
        return state

    async def state_for(self, context: PlatformContext) -> EnvironmentState:
        return await self.state(context.env)

    async def state_for_version(
        self, context: PlatformContext, api_version: str
    ) -> EnvironmentState:
        state = await self.envs.get_version(context.env, api_version)
        self.ensure_code()
        return state

    async def state_for_release(self, env: str, release_id: str) -> EnvironmentState:
        state = await self.envs.get_release(env, release_id)
        self.ensure_code()
        return state

    def ensure_code(self) -> ProjectCode:
        """Load the mounted code directory once: functions, policies, routes."""
        if self.code is None:
            self.code = load_code_dir(Path(self.settings.code_path), RUNTIME)
            if self.code.errors:
                logger.warning("mounted code errors: %s", self.code.errors)
        return self.code

    def function_spec(self, env: str, name: str, branch: str | None = None) -> FunctionSpec | None:
        """The function *name* as *branch* of *env* sees it: deployed on the branch, deployed to the environment, then mounted code."""
        self.ensure_code()
        self.deployments.ensure_loaded(env)
        if branch and branch != MAIN:
            self.deployments.ensure_loaded(env, branch)
        return resolve_function(env, branch, name)

    def http_function(self, env: str, name: str, branch: str | None = None) -> HttpFunctionSpec | None:
        """The HTTP function *name* as *branch* of *env* sees it (``functions/<name>/index.ts`` of a TypeScript deployment)."""
        return self.deployments.http_function(env, name, branch)

    def http_functions(self, env: str, branch: str | None = None) -> list[HttpFunctionSpec]:
        """Every HTTP function visible to *env* (and *branch*, whose own replace the environment's of the same name)."""
        merged = dict(self.deployments.http_functions(env))
        if branch and branch != MAIN:
            merged.update(self.deployments.http_functions(env, branch))
        return sorted(merged.values(), key=lambda spec: spec.name)

    def function_specs(self, env: str, branch: str | None = None) -> list[FunctionSpec]:
        self.ensure_code()
        self.deployments.ensure_loaded(env)
        if branch and branch != MAIN:
            self.deployments.ensure_loaded(env, branch)
        return list_resolved_functions(env, branch)

    def reload_code(self) -> ProjectCode:
        clear_functions(RUNTIME)
        for name in [
            name
            for name in list(__import__("sys").modules)
            if name.startswith(f"pawabase_code.{RUNTIME}.")
        ]:
            del __import__("sys").modules[name]
        self.code = None
        self.envs.forget()
        return self.ensure_code()

    def deprecated(self, env: str, what: str, advice: str, *, removed: bool = False) -> None:
        """Warn, once per environment and setting, about configuration kept somewhere it should not be.

        ``removed`` says the old place is no longer read at all, so the setting has no effect.
        """
        if (env, what) not in self._deprecations:
            self._deprecations.add((env, what))
            state = "is no longer read" if removed else "is deprecated"
            logger.warning("environment %r: %s %s; %s", env, what, state, advice)

    def limit(self, env: str, key: str, default: int) -> int:
        """An environment's numeric limit: ``<ENV>_<KEY>`` from the process environment, else *default*."""
        return envvars.integer(env, key, default)

    def env_setting(self, state: EnvironmentState, key: str, default: Any = "") -> Any:
        """An environment's setting from the process environment (``<ENV>_<KEY>``), else *default*.

        The value may be a ``secret://NAME`` reference, which is resolved here, so a
        deployment can keep a password in the environment's secrets and name it in a variable.
        """
        value = envvars.get(state.env_name, key)
        return default if value is None else self.resolve_value(state, value)

    def isolated(self, state: EnvironmentState, spec: Any) -> bool:
        """Whether *spec* runs in a sandbox process: a deployed function, in an environment set to ``process`` isolation.

        Code mounted on the runtime is the platform's own and always runs in the API process.
        """
        if "/" not in spec.project:
            return False
        if getattr(getattr(spec, "handler", None), "typescript", False):
            return True  # a TypeScript function only ever runs in its Deno worker
        mode = self.env_setting(state, "FUNCTION_ISOLATION", self.settings.function_isolation)
        return str(mode).strip().lower() == "process"

    def resolve_value(self, state: EnvironmentState, value: Any) -> Any:
        """Replace a ``secret://NAME`` reference with the secret's value."""
        if is_reference(value):
            return state.secret_values.get(reference_name(value), "")
        return value

    # ── cache ────────────────────────────────────────────────────────────

    @staticmethod
    def cache_key(state: EnvironmentState, key: str) -> str:
        definition = state.release_id or f"live-{state.version}"
        return f"{state.env_name}:{definition}:{key}"

    @staticmethod
    def cache_tag(state: EnvironmentState, tag: str) -> str:
        definition = state.release_id or f"live-{state.version}"
        return f"{state.env_name}:{definition}:{tag}"

    async def cache_get(self, state: EnvironmentState, key: str) -> Any:
        with span("cache", f"get {key}") as step:
            value = await self.cache.get(self.cache_key(state, key))
            # Sillo's cache answers a miss with a sentinel object, not None.
            if value is getattr(cache_base, "_MISSING", None):
                value = None
            step.set(result="hit" if value is not None else "miss")
        note("cache", "hit" if value is not None else "miss", append=True)
        return value

    async def cache_set(
        self,
        state: EnvironmentState,
        key: str,
        value: Any,
        *,
        ttl: int | None = None,
        tags: list[str] | None = None,
    ) -> None:
        with span("cache", f"set {key}", ttl=ttl):
            await self.cache.set(
                self.cache_key(state, key),
                value,
                ttl=ttl,
                tags=[self.cache_tag(state, t) for t in tags or []],
            )

    async def cache_invalidate(self, state: EnvironmentState, tags: list[str]) -> int:
        if not tags:
            return 0
        with span("cache", "invalidate", tags=list(tags)) as step:
            removed = await self.cache.invalidate_tags(*[self.cache_tag(state, t) for t in tags])
            step.set(removed=removed)
            return removed

    # ── jobs ─────────────────────────────────────────────────────────────

    async def dispatch(
        self,
        job: type,
        *,
        env: str,
        queue: str | None = None,
        delay: int = 0,
        target: str = "",
        source: str = "api",
        **kwargs: Any,
    ) -> str:
        """Push a Pawabase job onto its queue and record it for Studio."""
        from pawabase_server.db.models import JobRun

        queue_name = queue or job.queue
        payload = json.dumps(
            {
                "job": job.job_reference(),
                "args": [],
                "kwargs": {"env": env, **kwargs},
            },
            default=str,
        )
        with span("job", f"dispatch {job.__name__}", queue=queue_name, delay=int(delay or 0)):
            job_id = await self.queue.push(queue_name, payload, delay=int(delay or 0))
        if kwargs.get("_record"):
            # A retry of a job already on record: the record follows it; nothing new is created.
            return job_id
        now = datetime.now(UTC)
        describe = {
            "target": target,
            "source": source,
            "request_id": kwargs.get("request_id"),
            "max_attempts": getattr(job, "tries", 1),
            "payload": _truncate(kwargs),
            "available_at": now
            if not delay
            else datetime.fromtimestamp(now.timestamp() + delay, UTC),
        }
        try:
            await JobRun.create(
                id=job_id,
                env=env,
                queue=queue_name,
                job=job.__name__,
                status="delayed" if delay else "queued",
                **describe,
            )
        except IntegrityError:
            # A worker in another process picked the job up and recorded it
            # first; keep its status and add what only the dispatcher knows.
            await JobRun.filter(id=job_id).update(**describe)
        note("jobs", f"{job.__name__}:{job_id}", append=True)
        return job_id

    # ── events ───────────────────────────────────────────────────────────

    async def emit(
        self,
        state: EnvironmentState,
        name: str,
        payload: Any,
        *,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> str:
        note("events", name, append=True)
        with span("event", f"emit {name}"):
            return await self.deliver_event(
                PlatformEvent(
                    name=name,
                    env=state.env_name,
                    payload=payload,
                    actor=actor,
                    source=self.bus.source,
                    request_id=request_id,
                    release_id=state.release_id,
                    api_version=state.api_version,
                )
            )

    async def deliver_event(self, event: PlatformEvent) -> str:
        """Hand an event to the event processor durably: as a job on the ``events`` queue.

        The queue holds it until a worker acknowledges it (a worker that dies mid-event leaves it to be taken again), retries a
        failure with backoff, and settles one that keeps failing as a failed job Studio can replay. Returns the event id.
        """
        from pawabase_server.automation.jobs.events import ProcessEventJob

        await self.dispatch(
            ProcessEventJob,
            env=event.env,
            target=event.name,
            source="event",
            event=event.to_dict(),
            request_id=event.request_id,
        )
        self.bus.published += 1
        return event.id

    # ── realtime ─────────────────────────────────────────────────────────

    async def publish(
        self, state: EnvironmentState, channel: str, event: str, payload: Any
    ) -> dict[str, Any]:
        """Publish on a realtime channel on this environment's behalf."""
        if self.realtime is None:
            raise RuntimeError("realtime is not running")
        note("realtime", channel, append=True)
        with span("realtime", f"publish {channel}", event=event):
            return await self.realtime.publish(
                state.env_name, channel, event, payload, sender="server"
            )

    # ── what identity, realtime and the edge ask of the platform ─────────

    async def auth_settings(self, env: str) -> dict[str, Any]:
        """An environment's ``auth`` section with secret references resolved, plus what a sign-in page needs to name the project."""
        state = await self.state(env)
        config = dict(state.environment.auth or {})
        config["providers"] = {
            name: {key: self.resolve_value(state, value) for key, value in (provider or {}).items()}
            for name, provider in (config.get("providers") or {}).items()
        }
        return {
            "env": env,
            "project_name": self.settings.project_name,
            "auth": config,
            "public_url": self.settings.public_url,
            "project": {"id": state.project_id, "limits": state.project_limits},
        }

    async def realtime_settings(self, env: str) -> dict[str, Any]:
        """The channel rules realtime enforces for an environment: its own, plus one per realtime Resource."""
        from pawabase_server.db.models import PolicyDef

        state = await self.state(env)
        environment = state.environment
        realtime = dict((environment.settings or {}).get("realtime") or {})
        channels = list(realtime.get("channels") or [])
        for resource in state.resources.values():
            if resource.realtime:
                # Anyone may subscribe while the resource can be read at all: the read policy is a row policy (``owner``, a
                # ``$record`` condition) that has no row to judge at subscription, so it is applied to each change instead, per
                # subscriber, before it is sent (see ``Realtime._resource_message``).
                operations = resource.operations or {}
                readable = any((operations.get(op) or {}).get("enabled") for op in ("get", "list"))
                channels.append(
                    {
                        "pattern": f"resource:{resource.name}",
                        "subscribe": "public" if readable else "service",
                        "publish": "service",
                        "presence": False,
                    }
                )
        policies = await PolicyDef.filter(environment_id=environment.id)
        return {
            "version": environment.version,
            "channels": channels,
            "allow_client_publish": realtime.get("allow_client_publish", True),
            "default_policy": realtime.get("default_policy", "authenticated"),
            "policies": {
                p.name: {"condition": p.condition, "description": p.description} for p in policies
            },
        }

    async def emit_event(
        self, env: str, name: str, payload: Any, *, actor: str | None = None
    ) -> str:
        """Publish a platform event for *env* by name, for parts of the server that hold no environment state."""
        return await self.emit(await self.state(env), name, payload, actor=actor)

    async def queue_mail(
        self,
        env: str,
        *,
        to: list[str],
        subject: str = "",
        text: str | None = None,
        html: str | None = None,
        template: str | None = None,
        data: dict[str, Any] | None = None,
        source: str = "identity",
    ) -> str:
        """Queue a message through the environment's mail settings; delivery happens in the worker, never in the request."""
        from pawabase_server.automation.jobs.mail import SendMailJob

        await self.state(env)
        return await self.dispatch(
            SendMailJob,
            env=env,
            target=",".join(to),
            source=source,
            to=to,
            subject=subject,
            text=text,
            html=html,
            template=template,
            data=data or {},
        )


def _truncate(value: Any, limit: int = 4000) -> Any:
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return json.loads(text)
    return {"truncated": True, "preview": text[:limit]}


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
