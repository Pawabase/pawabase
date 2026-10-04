"""The API's long-lived services, assembled once per process.

:class:`Platform` holds what handlers, jobs, the event processor and the
scheduler share: the event bus (Sillo events), the queue connection (Sillo
work), the cache (Sillo cache), storage and mail managers, the developer
database pool, clients for Angula and Akountz, the secret box, and the
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

from app.config import ApiSettings, function_install_enabled
from app.data.source import DataSourcePool
from app.deployments import Deployments
from app.mail import MailManager
from app.secrets import SecretBox, is_reference, reference_name
from app.state import EnvironmentCache, EnvironmentState
from app.storage.manager import StorageManager
from pawabase_core.clients import ServiceClient
from pawabase_core.context import PlatformContext
from pawabase_core.events import EventBus
from pawabase_core.functions import (
    MAIN,
    FunctionSpec,
    RUNTIME,
    ProjectCode,
    clear_functions,
    list_resolved_functions,
    load_code_dir,
    resolve_function,
)
from pawabase_core.telemetry import note, span

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
        settings: ApiSettings,
        *,
        cache: BaseCache | None = None,
        queue: QueueConnection | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self.settings = settings
        self.box = SecretBox(settings.master_key)
        self.sources = DataSourcePool()
        self.envs = EnvironmentCache(self)
        self.storage = StorageManager(self)
        self.mail = MailManager(self)
        self.bus = bus or EventBus.from_url(settings.redis_url or None, source="api")
        self.cache = cache or self._build_cache()
        self.queue = queue or self._build_queue()
        self.angula = ServiceClient(
            settings.angula_url, secret=settings.internal_secret, issuer="api", audience="angula"
        )
        self.akountz = ServiceClient(
            settings.akountz_url, secret=settings.internal_secret, issuer="api", audience="akountz"
        )
        self.outbound = httpx.AsyncClient(
            timeout=30.0, follow_redirects=False, headers={"user-agent": "Pawabase/0.1"}
        )
        self.code: ProjectCode | None = None
        self.deployments = Deployments(
            settings.deployments_path or Path(settings.code_path) / ".deployments",
            install_requirements=function_install_enabled(settings),
            index_url=settings.function_index_url,
            install_timeout=settings.function_install_timeout,
        )
        self.app: Any = None
        self.app: Any = None
        self.started_at = datetime.now(UTC)

    # ── construction ─────────────────────────────────────────────────────

    def _build_cache(self) -> BaseCache:
        if self.settings.redis_url:
            from sillo.cache import RedisCache

            return RedisCache(url=self.settings.redis_url, namespace="pawabase")
        return MemoryCache(namespace="pawabase")

    def _build_queue(self) -> Any:
        from sillo.work.commands import connection_for

        return connection_for(self.settings.redis_url or None, prefix=self.settings.queue_prefix)

    def bind(self) -> Platform:
        global _current
        _current = self
        return self

    async def start(self) -> None:
        self.bind()
        await self.bus.start()
        logger.info(await self.storage.prepare_default())

    async def stop(self) -> None:
        await self.bus.stop()
        await self.sources.close()
        await self.storage.close()
        await self.mail.close()
        await self.angula.close()
        await self.akountz.close()
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

    def function_specs(self, env: str, branch: str | None = None) -> list[FunctionSpec]:
        self.ensure_code()
        self.deployments.ensure_loaded(env)
        if branch and branch != MAIN:
            self.deployments.ensure_loaded(env, branch)
        return list_resolved_functions(env, branch)

    def reload_code(self) -> ProjectCode:
        clear_functions(RUNTIME)
        for name in [name for name in list(__import__("sys").modules) if name.startswith(f"pawabase_code.{RUNTIME}.")]:
            del __import__("sys").modules[name]
        self.code = None
        self.envs.forget()
        return self.ensure_code()

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
        from database.models import JobRun

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
            return await self.bus.emit(
                name,
                env=state.env_name,
                payload=payload,
                actor=actor,
                request_id=request_id,
                release_id=state.release_id,
                api_version=state.api_version,
            )

    # ── realtime ─────────────────────────────────────────────────────────

    async def publish(
        self, state: EnvironmentState, channel: str, event: str, payload: Any
    ) -> dict[str, Any]:
        """Publish through Angula, the realtime service."""
        context = PlatformContext(env=state.env_name, role="service")
        note("realtime", channel, append=True)
        with span("realtime", f"publish {channel}", event=event):
            return await self.angula.post(
                "/internal/v1/publish",
                json={"channel": channel, "event": event, "payload": payload},
                context=context,
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
