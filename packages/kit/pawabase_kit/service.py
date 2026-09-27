"""Assembling a Pawabase service on Sillo.

Every service starts the same way: a :class:`~sillo.SilloApp` with its
authentication backends declared (so Sillo installs the middleware and
documents the schemes), the platform-context middleware, telemetry with request
ids, a health check, and the internal telemetry endpoints Studio reads. This
module does that once so each service's bootstrap stays about the service.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sillo import HttpContext, Router, SilloApp, json
from sillo.auth import useAuth
from sillo.auth.backend import AuthenticationBackend

from .auth import ContextMiddleware, ServiceBackend
from .clients import ServiceError
from .principal import Principal
from .settings import PlatformSettings
from .telemetry import Telemetry

SERVICE_ONLY = useAuth(schemes=["serviceToken"])


class ConfigurationError(RuntimeError):
    """The service refuses to start with this configuration."""


def create_service(
    name: str,
    settings: PlatformSettings,
    *,
    title: str,
    description: str,
    version: str = "0.1.0",
    backends: Sequence[AuthenticationBackend] = (),
    docs: Any = None,
    inner: Sequence[Any] = (),
    installables: Sequence[Any] = (),
) -> SilloApp:
    """Build a service application with the shared platform plumbing.

    Args:
        name: The service's name; also the audience of its service tokens.
        settings: Its settings.
        title, description, version: For the OpenAPI document.
        backends: Authentication backends in addition to :class:`ServiceBackend`.
        docs: Passed to ``SilloApp(docs=...)``; ``None`` keeps Sillo's default
            Atlas reference at ``/docs``.
        inner: ASGI middleware that must run *inside* the platform context
            and telemetry (a dispatcher that needs the verified context).
        installables: Sillo installables to install before the platform
            middleware, so their middleware also runs inside it.
    """
    if settings.app_env == "production":
        problems = settings.validate_for_production()
        if problems:
            raise ConfigurationError("; ".join(problems))

    options: dict[str, Any] = {}
    if docs is not None:
        options["docs"] = docs
    app = SilloApp(
        title=title,
        description=description,
        version=version,
        debug=settings.debug,
        auth=[*backends, ServiceBackend(settings.internal_secret, name)],
        auth_user_model=Principal,
        **options,
    )
    app.state["settings"] = settings
    app.state["service_name"] = name
    for installable in installables:
        app.install(installable)
    for middleware in inner:
        app.use(middleware)

    # Registered after SilloApp installed authentication, so it runs before it:
    # a bearer token is verified against the environment the context names.
    app.use(ContextMiddleware(settings.internal_secret))
    telemetry = app.install(Telemetry(name))

    async def service_error(ctx: HttpContext, exc: ServiceError):
        body = exc.body if isinstance(exc.body, dict) else {"detail": exc.body}
        return json(body, status_code=exc.status if exc.status < 600 else 502)

    app.add_exception_handler(ServiceError, service_error)
    app.mount_router(internal_router(name, telemetry))
    app.get("/health", handler=_health(name), name="health", exclude_from_schema=True)
    return app


def _health(name: str):
    async def health(ctx: HttpContext):
        return {"status": "ok", "service": name}

    return health


def internal_router(name: str, telemetry: Telemetry) -> Router:
    """Endpoints every service exposes to Studio."""
    router = Router(prefix="/internal/v1/telemetry")

    @router.get("/requests", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def requests(ctx: HttpContext):
        q = ctx.query_params
        return {
            "service": name,
            "requests": telemetry.query(
                limit=min(int(q.get("limit", 100)), 1000),
                project=q.get("project"),
                env=q.get("env"),
                status_min=int(q["status_min"]) if q.get("status_min") else None,
                request_id=q.get("request_id"),
                path_prefix=q.get("path"),
            ),
        }

    @router.get("/summary", auth=SERVICE_ONLY, exclude_from_schema=True)
    async def summary(ctx: HttpContext):
        return telemetry.summary()

    return router
