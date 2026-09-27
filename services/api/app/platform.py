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
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
from pawabase_kit.clients import ServiceClient
from pawabase_kit.context import PlatformContext
from pawabase_kit.events import EventBus
from pawabase_kit.functions import ProjectCode, load_project_code
from pawabase_kit.telemetry import note
from sillo.cache import BaseCache, MemoryCache
from sillo.cache import base as cache_base

from app.config import ApiSettings
from app.data.source import DataSourcePool
from app.mail import MailManager
from app.secrets import SecretBox, is_reference, reference_name
from app.state import EnvironmentCache, EnvironmentState
from app.storage.manager import StorageManager

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

    def __init__(self, settings: ApiSettings, *, cache: BaseCache | None = None, queue: QueueConnection | None = None, bus: EventBus | None = None) -> None:
        self.settings = settings
        self.box = SecretBox(settings.master_key)
        self.sources = DataSourcePool()
        self.envs = EnvironmentCache(self)
        self.storage = StorageManager(self)
        self.mail = MailManager(self)
        self.bus = bus or EventBus.from_url(settings.redis_url or None, source="api")
        self.cache = cache or self._build_cache()
        self.queue = queue or self._build_queue()
        self.angula = ServiceClient(settings.angula_url, secret=settings.internal_secret, issuer="api", audience="angula")
        self.akountz = ServiceClient(settings.akountz_url, secret=settings.internal_secret, issuer="api", audience="akountz")
        self.outbound = httpx.AsyncClient(timeout=30.0, follow_redirects=False, headers={"user-agent": "Pawabase/0.1"})
        self.code: dict[str, ProjectCode] = {}
        self.started_at = datetime.now(timezone.utc)

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

    async def stop(self) -> None:
        await self.bus.stop()
        await self.sources.close()
        await self.storage.close()
        await self.mail.close()
        await self.angula.close()
        await self.akountz.close()
        await self.outbound.aclose()

    # ── environments ─────────────────────────────────────────────────────

    async def state(self, project: str, env: str) -> EnvironmentState:
        state = await self.envs.get(project, env)
        self.ensure_code(project)
        return state

    async def state_for(self, context: PlatformContext) -> EnvironmentState:
        return await self.state(context.project, context.env)

    def ensure_code(self, project: str) -> ProjectCode:
        """Load ``<code_path>/<project>`` once: functions, policies, routes."""
        loaded = self.code.get(project)
        if loaded is None:
            loaded = self.code[project] = load_project_code(self.settings.code_path, project)
            if loaded.errors:
                logger.warning("project %s code errors: %s", project, loaded.errors)
        return loaded

    def reload_code(self, project: str) -> ProjectCode:
        self.code.pop(project, None)
        self.envs.forget(project)
        return self.ensure_code(project)

    def resolve_value(self, state: EnvironmentState, value: Any) -> Any:
        """Replace a ``secret://NAME`` reference with the secret's value."""
        if is_reference(value):
            return state.secret_values.get(reference_name(value), "")
        return value

    # ── cache ────────────────────────────────────────────────────────────

    @staticmethod
    def cache_key(state: EnvironmentState, key: str) -> str:
        return f"{state.project_ref}:{state.env_name}:{key}"

    @staticmethod
    def cache_tag(state: EnvironmentState, tag: str) -> str:
        return f"{state.project_ref}:{state.env_name}:{tag}"

    async def cache_get(self, state: EnvironmentState, key: str) -> Any:
        value = await self.cache.get(self.cache_key(state, key))
        # Sillo's cache answers a miss with a sentinel object, not None.
        if value is getattr(cache_base, "_MISSING", None):
            value = None
        note("cache", "hit" if value is not None else "miss", append=True)
        return value

    async def cache_set(self, state: EnvironmentState, key: str, value: Any, *, ttl: int | None = None, tags: list[str] | None = None) -> None:
        await self.cache.set(self.cache_key(state, key), value, ttl=ttl, tags=[self.cache_tag(state, t) for t in tags or []])

    async def cache_invalidate(self, state: EnvironmentState, tags: list[str]) -> int:
        if not tags:
            return 0
        return await self.cache.invalidate_tags(*[self.cache_tag(state, t) for t in tags])

    # ── jobs ─────────────────────────────────────────────────────────────

    async def dispatch(
        self,
        job: type,
        *,
        project: str,
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
        payload = json.dumps({"job": job.job_reference(), "args": [], "kwargs": {"project": project, "env": env, **kwargs}}, default=str)
        job_id = await self.queue.push(queue_name, payload, delay=int(delay or 0))
        now = datetime.now(timezone.utc)
        await JobRun.update_or_create(
            id=job_id,
            defaults={
                "project": project,
                "env": env,
                "queue": queue_name,
                "job": job.__name__,
                "target": target,
                "source": source,
                "status": "delayed" if delay else "queued",
                "max_attempts": getattr(job, "tries", 1),
                "payload": _truncate(kwargs),
                "available_at": now if not delay else datetime.fromtimestamp(now.timestamp() + delay, timezone.utc),
            },
        )
        note("jobs", f"{job.__name__}:{job_id}", append=True)
        return job_id

    # ── events ───────────────────────────────────────────────────────────

    async def emit(self, state: EnvironmentState, name: str, payload: Any, *, actor: str | None = None, request_id: str | None = None) -> str:
        note("events", name, append=True)
        return await self.bus.emit(name, project=state.project_ref, env=state.env_name, payload=payload, actor=actor, request_id=request_id)

    # ── realtime ─────────────────────────────────────────────────────────

    async def publish(self, state: EnvironmentState, channel: str, event: str, payload: Any) -> dict[str, Any]:
        """Publish through Angula, the realtime service."""
        context = PlatformContext(project=state.project_ref, env=state.env_name, role="service")
        note("realtime", channel, append=True)
        return await self.angula.post("/internal/v1/publish", json={"channel": channel, "event": event, "payload": payload}, context=context)


def _truncate(value: Any, limit: int = 4000) -> Any:
    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return json.loads(text)
    return {"truncated": True, "preview": text[:limit]}


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
