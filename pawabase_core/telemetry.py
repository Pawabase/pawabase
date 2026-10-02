"""Request telemetry every service records, for Studio's request inspector.

:class:`Telemetry` is a Sillo installable. It adds Sillo's
:class:`~sillo.http.RequestIdMiddleware` (so every hop carries ``X-Request-ID``)
and a recorder that captures, per request: route, status, duration, project,
environment, caller, credential role, the policy decision, and whatever the
handler noted along the way (cache hits, events emitted, jobs dispatched).

Records are kept in a bounded in-memory ring and handed to optional sinks. The
API and Akountz add a sink that persists them. Studio merges every service's
records through ``GET /internal/v1/telemetry/requests``.
"""

from __future__ import annotations

import contextvars
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from sillo.core.http import HttpContext
from sillo.http import RequestIdMiddleware, get_request_id_from_request

from .context import SCOPE_KEY

Sink = Callable[["RequestRecord"], Awaitable[None]]

_trace: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "pawabase_trace", default=None
)

SKIP_PREFIXES = ("/internal/v1/telemetry",)


def should_skip(path: str) -> bool:
    """Skip service probes and telemetry's own reads, not application routes like /health/v1."""
    return path == "/health" or path.startswith(SKIP_PREFIXES)


@dataclass(slots=True)
class RequestRecord:
    """One served request."""

    service: str
    request_id: str | None
    method: str
    path: str
    route: str | None
    status: int
    duration_ms: float
    started_at: str
    project: str | None = None
    env: str | None = None
    role: str | None = None
    user: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    error: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def note(key: str, value: Any = True, *, append: bool = False) -> None:
    """Attach *value* to the current request's record.

    ``note("cache", "hit")``; ``note("events", "order.created", append=True)``.
    Does nothing outside a request, so library code can call it freely.
    """
    trace = _trace.get()
    if trace is None:
        return
    if append:
        trace.setdefault(key, []).append(value)
    else:
        trace[key] = value


def current_notes() -> dict[str, Any] | None:
    return _trace.get()


class TelemetryRecorder:
    """ASGI middleware that records each HTTP request."""

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry
        self.app: Any = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or should_skip(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()
        status_holder = {"status": 500}
        trace: dict[str, Any] = {}
        token = _trace.set(trace)
        error: str | None = None

        async def capture(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _trace.reset(token)
            await self.telemetry.record(
                self._build(
                    scope, receive, status_holder["status"], started, started_at, trace, error
                )
            )

    def _build(self, scope, receive, status, started, started_at, trace, error) -> RequestRecord:
        ctx = HttpContext(scope, receive)
        context = scope.get(SCOPE_KEY)
        user = scope.get("user")
        route = scope.get("route")
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = scope.get("client")
        try:
            request_id = get_request_id_from_request(ctx)
        except Exception:
            request_id = headers.get("x-request-id")
        if trace.get("error") and not error:
            error = str(trace.pop("error"))
        return RequestRecord(
            service=self.telemetry.service,
            request_id=request_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
            route=getattr(route, "path", None) if route is not None else None,
            status=status,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            started_at=started_at,
            project=getattr(context, "project", None),
            env=getattr(context, "env", None),
            role=getattr(context, "role", None) or getattr(user, "kind", None),
            user=getattr(user, "identity", None)
            if getattr(user, "is_authenticated", False)
            else None,
            ip=client[0] if client else None,
            user_agent=headers.get("user-agent"),
            error=error,
            notes=trace,
        )


class Telemetry:
    """Installable request telemetry.

    Args:
        service: This service's name.
        capacity: How many recent requests to keep in memory.
    """

    name = "pawabase.telemetry"

    def __init__(self, service: str, *, capacity: int = 5_000) -> None:
        self.service = service
        self.records: deque[RequestRecord] = deque(maxlen=capacity)
        self.sinks: list[Sink] = []
        self.started_at = time.time()
        self.total = 0
        self.errors = 0

    def install(self, app: Any) -> Telemetry:
        app.state[self.name] = self
        # Registered in this order so the request id middleware is outermost
        # and the recorder can read the id it assigned.
        app.use(TelemetryRecorder(self))
        app.use(RequestIdMiddleware())
        return self

    def add_sink(self, sink: Sink) -> Sink:
        self.sinks.append(sink)
        return sink

    async def record(self, record: RequestRecord) -> None:
        self.records.append(record)
        self.total += 1
        if record.status >= 500:
            self.errors += 1
        for sink in self.sinks:
            try:
                await sink(record)
            except Exception:  # telemetry must never fail a request
                pass

    def query(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        env: str | None = None,
        status_min: int | None = None,
        request_id: str | None = None,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recent records, newest first, filtered."""
        results = []
        for item in reversed(self.records):
            if project and item.project != project:
                continue
            if env and item.env != env:
                continue
            if status_min and item.status < status_min:
                continue
            if request_id and item.request_id != request_id:
                continue
            if path_prefix and not item.path.startswith(path_prefix):
                continue
            results.append(item.to_dict())
            if len(results) >= limit:
                break
        return results

    def summary(self) -> dict[str, Any]:
        """Totals and latency percentiles over the in-memory window."""
        durations = sorted(r.duration_ms for r in self.records)

        def pct(p: float) -> float:
            if not durations:
                return 0.0
            index = min(len(durations) - 1, int(round(p * (len(durations) - 1))))
            return durations[index]

        by_status: dict[str, int] = {}
        for record in self.records:
            bucket = f"{record.status // 100}xx"
            by_status[bucket] = by_status.get(bucket, 0) + 1
        return {
            "service": self.service,
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "total": self.total,
            "errors": self.errors,
            "window": len(self.records),
            "by_status": by_status,
            "p50_ms": pct(0.5),
            "p95_ms": pct(0.95),
            "p99_ms": pct(0.99),
        }
