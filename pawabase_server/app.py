"""The server, assembled. Start reading here.

One process serves everything the server does. It is built as two faces of one set of objects:

* the **application** (:func:`create_app`): identity, the management plane, the data plane, storage, functions, flows, events and
  realtime, on one Sillo app, one database and one platform object;
* the **public entry point** (:func:`create_public`): the application behind the edge, which decides what the outside world may reach,
  who is asking and whether there is room (API keys, CORS, firewall, rate limits, quotas);

Studio is not part of this process: it is its own service (the top-level ``studio`` package) and manages the server over its internal
listener with service tokens. Nothing in here speaks HTTP to another part of the server.

Order matters, because Sillo builds middleware inside-out (the last registered runs first):

1. ``create_service`` builds the ``SilloApp`` with its auth backends, then the platform-context middleware and telemetry (request ids)
   around it.
2. Record (the database) and the data-plane dispatcher are installed before that outer plumbing. The dispatcher therefore runs inside the
   context and telemetry middleware, and every ``/rest/v1`` request is traced and attributed to its environment.
3. Routers are mounted most-specific first.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from sillo import SilloApp
from sillo.record import Record
from sillo.security import CorsConfig, CORSMiddleware

from pawabase_server.compiler.dispatch import DataPlaneDispatcher
from pawabase_server.core.auth import UserBackend
from pawabase_server.core.service import create_service
from pawabase_server.db.database import (
    MIGRATIONS_MODULE,
    MODEL_MODULES,
    database_config,
)
from pawabase_server.db.models import Environment
from pawabase_server.edge import Edge
from pawabase_server.edge.key_resolver import KeyResolver, lookup_key
from pawabase_server.edge.networking import DomainSync
from pawabase_server.edge.quotas import RequestQuota
from pawabase_server.identity.accounts import Accounts
from pawabase_server.observability.request_metrics import RequestRollup
from pawabase_server.observability.status import StatusProber
from pawabase_server.platform import Platform
from pawabase_server.projects.manager import ProjectError, ensure_default_project
from pawabase_server.realtime import Realtime
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.server")

DEFAULT_ENVIRONMENT = "development"
#: Wired by the first call that needs them, so importing this module stays cheap.
INTERNAL = "pawabase.internal"


async def ensure_default_environment(settings: Settings, box: Any = None) -> None:
    """Give a fresh runtime its default project and first environment, and attach environments from before projects existed to it."""
    try:
        report = await ensure_default_project(settings, box)
    except ProjectError as exc:
        logger.error("the default project could not be set up: %s", exc)
        return
    if report["created"] or report["attached"]:
        logger.info(
            "default project %r: created %s, attached %s",
            report["project"],
            report["created"] or "nothing",
            report["attached"] or "nothing",
        )


async def housekeeping(realtime: Realtime, interval: float) -> None:
    """Prune idle peers, drop stale presence, close out expired tokens and re-check subscriptions when rules change.

    ``sillo-wire`` leaves pruning cadence to the application. A failing pass is logged and tried again: housekeeping must outlive any bug.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await realtime.sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("realtime housekeeping pass failed")


def create_app(
    settings: Settings | None = None,
    *,
    platform: Platform | None = None,
    realtime: Realtime | None = None,
    role: str = "server",
) -> SilloApp:
    """Build the application: every route the server has, behind the platform's authentication and telemetry.

    ``role`` is what this process is for. ``server`` (the default) serves and keeps its housekeeping; ``worker`` and ``scheduler`` are the
    same application used headlessly by those processes: they start the platform (database, queue, events) and nothing that only a
    server needs, such as realtime or request metrics.
    """
    settings = settings or Settings()
    platform = platform or Platform(settings)
    realtime = realtime or Realtime(settings, platform=platform)
    platform.realtime = realtime
    accounts = Accounts(platform)

    app = create_service(
        "pawabase",
        settings,
        title=f"{settings.project_name} server",
        description="Identity, the management plane, the data plane, storage, functions, flows, events and realtime.",
        backends=[UserBackend(settings.jwt_master_secret)],
        inner=[DataPlaneDispatcher(platform)],
        installables=[Record(database_config(settings), tuple(MODEL_MODULES))],
    )
    app.state["record"].set_migrations(MIGRATIONS_MODULE)
    app.state["platform"] = platform
    app.state["accounts"] = accounts
    app.state["realtime"] = realtime
    platform.app = app
    rollup = RequestRollup(retention_days=settings.request_retention_days).attach(
        app.state["pawabase.telemetry"]
    )
    app.state["request_rollup"] = rollup
    tasks: list[asyncio.Task] = []

    @app.on_startup
    async def start() -> None:
        await platform.start()
        if role != "server":
            return
        await ensure_default_environment(settings, platform.box)
        await platform.audit_configuration()
        rollup.start()
        await realtime.start()
        tasks.append(
            asyncio.create_task(
                housekeeping(realtime, settings.sweep_interval), name="realtime-housekeeping"
            )
        )
        # Queued work is done by processes of their own (see ``worker_mode``). These two switches put it in this process instead, which
        # is for tests and throwaway runs: a slow job then shares the server's event loop.
        if settings.inline_worker:
            from pawabase_server.automation.event_processor import EventProcessor
            from pawabase_server.automation.worker import start_inline_worker

            EventProcessor(platform).attach()
            app.state["inline_worker"] = start_inline_worker(platform)
        if settings.inline_scheduler:
            from pawabase_server.automation.scheduler import PlatformScheduler

            scheduler = PlatformScheduler(platform)
            app.state["inline_scheduler"] = scheduler
            await scheduler.start()

    async def stop_platform() -> None:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if role == "server":
            await rollup.stop()
        worker = app.state.get("inline_worker")
        if worker is not None:
            worker.stop()
            task = app.state.get("inline_worker_task")
            if task is not None:
                await asyncio.wait([task], timeout=5)
        scheduler = app.state.get("inline_scheduler")
        if scheduler is not None:
            await scheduler.stop()
        if role == "server":
            await realtime.stop()
        await platform.stop()

    # Shutdown handlers run in the order they were added, and the database installable added its own when the service was created.
    # Stopping the platform *after* that closes the database: the last request-metrics flush and any job still finishing then write to a
    # closed database, which silently opens a new connection nothing closes. Stop the things that use the database first.
    app.shutdown_handlers.insert(0, stop_platform)

    from pawabase_server.routes import register_routes

    register_routes(app, platform, accounts, realtime)
    return app


# ── the public entry point ──────────────────────────────────────────────────


def create_public(app: SilloApp, settings: Settings | None = None) -> Any:
    """The application behind the edge, as the ASGI app the public listener serves.

    Its background work (verified-domain refresh, status probes) starts and stops with the application.
    """
    settings = settings or app.state["settings"]
    cache: Any = None
    rate_backend: Any = "memory"
    if settings.redis_url:
        from sillo.cache import RedisCache
        from sillo.security import RedisBackend

        cache = RedisCache(url=settings.redis_url, namespace="edge")
        rate_backend = RedisBackend(url=settings.redis_url)
    record = app.state["record"]

    async def lookup(raw: str, env: str | None) -> dict[str, Any]:
        # The edge runs outside the application's own middleware, so it enters the database context itself: on a real server a request
        # is its own task and does not inherit the one startup opened.
        return await record.ensure_context(None, lambda: lookup_key(raw, env))

    resolver = KeyResolver(
        lookup, cache=cache, ttl=settings.key_cache_ttl, stale_ttl=settings.key_stale_ttl
    )
    quota = RequestQuota(
        daily_limit=settings.daily_request_limit,
        monthly_limit=settings.monthly_request_limit,
        reservation=settings.request_quota_reservation,
        redis_url=settings.redis_url,
    )
    edge = Edge(app, settings, resolver, rate_backend=rate_backend, request_quota=quota)

    realtime = app.state["realtime"]

    async def database_ok() -> bool:
        await Environment.exists()
        return True

    async def realtime_ok() -> bool:
        return not realtime.draining

    health = {"api": database_ok, "auth": database_ok, "realtime": realtime_ok}
    prober = StatusProber(
        {"gateway": database_ok, "auth": database_ok, "realtime": realtime_ok},
        every=settings.status_probe_interval,
    )
    domains = DomainSync(edge, every=settings.domain_refresh_interval)
    app.state["edge"] = edge
    app.state["key_resolver"] = resolver
    app.state["request_quota"] = quota
    app.state["status_prober"] = prober
    app.state["domain_sync"] = domains
    app.state["health"] = health

    @app.on_startup
    async def begin() -> None:
        prober.start()
        domains.start()

    async def end() -> None:
        await prober.stop()
        await domains.stop()
        await edge.close()

    app.shutdown_handlers.insert(0, end)

    cors = CORSMiddleware(
        CorsConfig(
            allow_origins=settings.origins() or ["*"],
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            allow_headers=["*"],
            expose_headers=[
                "x-request-id",
                "x-ratelimit-limit",
                "x-ratelimit-remaining",
                "retry-after",
            ],
            allow_credentials=False,
            max_age=600,
        )
    )
    cors.app = edge
    return cors


# ── everything ──────────────────────────────────────────────────────────────


@dataclass
class Server:
    """The faces of one server, and the objects they share."""

    settings: Settings
    #: The application, served by the internal listener: what Studio and other services manage.
    app: SilloApp
    #: The application behind the edge, served by the public listener.
    public: Any

    @property
    def platform(self) -> Platform:
        return self.app.state["platform"]

    @property
    def edge(self) -> Edge:
        return self.app.state["edge"]


def create_server(settings: Settings | None = None) -> Server:
    """Build the application and its public entry point."""
    settings = settings or Settings()
    app = create_app(settings)
    return Server(settings=settings, app=app, public=create_public(app, settings))
