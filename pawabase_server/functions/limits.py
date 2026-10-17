"""Resource limits: what every run of a function may use, so one project's functions cannot starve another's (noisy neighbours).

Limits belong to the **project**. They are part of its limits, set when the project is created and changed only by whoever operates
the installation: Pawabase Cloud through the runtime agent, or an operator with ``pawabase-server project limits set``. Neither
Studio nor a function's code can raise them. Where a project sets none, the installation's defaults apply, and nothing may exceed the
installation's maximums.

Every run of every function (Python and TypeScript ``fn()``, HTTP functions) gets:

``timeout``      seconds of wall-clock time. A function may ask for less in its code (``timeout=…``); never more than the project's
                 ``max_function_timeout_seconds``.
``memory``       megabytes its worker may hold: the project's ``max_function_memory_mb``.
``cpu``          seconds of CPU time per run: the project's ``max_function_cpu_seconds`` (as long as the timeout when unset). For an HTTP
                 function, whose worker answers many requests at once, it is the longest it may compute without yielding.
``concurrency``  runs of one function at once in an environment: the project's ``max_concurrent_runs_per_function`` (``0``: no limit of
                 its own; the project-wide ``max_function_concurrency`` still applies).
``max_body``     megabytes of request body an HTTP function accepts: the project's ``max_function_body_mb``.
``workers``      workers one HTTP function may run at once: the project's ``max_function_workers``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

from pawabase_server.projects import quotas
from pawabase_server.settings import Settings

#: Each limit and the project limit that sets it.
PROJECT_KEYS = {
    "timeout": "max_function_timeout_seconds",
    "memory": "max_function_memory_mb",
    "cpu": "max_function_cpu_seconds",
    "concurrency": "max_concurrent_runs_per_function",
    "max_body": "max_function_body_mb",
    "workers": "max_function_workers",
}


@dataclass(frozen=True)
class RunLimits:
    timeout: float
    memory: int
    cpu: float
    concurrency: int
    max_body: int
    workers: int
    #: For each limit: ``project`` (the project's limits), ``default`` (the installation's default), ``code`` (a shorter timeout the
    #: function asked for) or ``installation`` (the installation's maximum lowered the project's value).
    sources: dict[str, str] = field(default_factory=dict, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunLimits:
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})


def installation(settings: Settings, *, kind: str) -> tuple[dict[str, float], dict[str, float]]:
    """``(defaults, maximums)`` of this installation, for a ``call`` or an ``http`` function."""
    defaults = {
        "timeout": float(settings.function_default_timeout_seconds if kind == "call" else 150),
        "memory": float(settings.function_default_memory_mb),
        "cpu": float(settings.function_default_cpu_seconds),
        "concurrency": 0.0,
        "max_body": float(settings.functions_max_body_mb),
        "workers": float(settings.functions_max_workers),
    }
    maximums = {
        "timeout": float(settings.functions_max_timeout_seconds),
        "memory": float(settings.function_max_memory_mb),
        "cpu": float(settings.function_max_cpu_seconds),
        "concurrency": 0.0,  # no installation maximum: 0 means unlimited
        "max_body": float(settings.functions_max_body_mb),
        "workers": float(settings.functions_max_workers),
    }
    return defaults, maximums


def resolve(
    settings: Settings,
    *,
    kind: str,
    project: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> RunLimits:
    """The limits one run gets: the project's, else the installation's defaults, held under the installation's maximums.

    *timeout* is what the function's code asks for; it may only shorten the project's.
    """
    defaults, maximums = installation(settings, kind=kind)
    values: dict[str, float] = {}
    sources: dict[str, str] = {}
    for name, key in PROJECT_KEYS.items():
        chosen = quotas.allowance(project, key)
        values[name], sources[name] = (float(chosen), "project") if chosen else (defaults[name], "default")
        if maximums[name] and values[name] > maximums[name]:
            values[name], sources[name] = maximums[name], "installation"
    if timeout and 0 < float(timeout) < values["timeout"]:
        values["timeout"], sources["timeout"] = float(timeout), "code"
    if values["cpu"] <= 0 or values["cpu"] > values["timeout"]:
        values["cpu"] = values["timeout"]  # one run cannot use more CPU than it has time; unset means exactly that much
    return RunLimits(
        timeout=values["timeout"],
        memory=int(values["memory"]),
        cpu=round(values["cpu"], 3),
        concurrency=int(values["concurrency"]),
        max_body=int(values["max_body"]),
        workers=max(1, int(values["workers"])),
        sources=sources,
    )


def timeout_of(spec: Any) -> float | None:
    """The timeout a function's code asks for: an HTTP function's ``config.timeout``, an ``fn()``'s ``timeout``."""
    config = getattr(spec, "config", None)
    if config is not None:
        return float(config.timeout)
    timeout = getattr(spec, "timeout", None)
    return float(timeout) if timeout else None


class ConcurrencyLimitReached(Exception):
    """As many runs of the function as the project allows are already running, and none finished in time."""

    def __init__(self, function: str, limit: int) -> None:
        super().__init__(f"{function} may run {limit} time{'s' if limit != 1 else ''} at once in this project; all are busy")
        self.function = function
        self.limit = limit


class FunctionGates:
    """At most ``concurrency`` runs of one function in one environment at once, in this process.

    A run that finds them all busy waits up to *wait* seconds for one to finish, then is refused (``429``). A changed limit takes effect
    for the next run; runs already holding a slot keep it.
    """

    def __init__(self) -> None:
        self._gates: dict[tuple[str, str], tuple[int, asyncio.Semaphore]] = {}

    @contextlib.asynccontextmanager
    async def slot(self, env: str, function: str, limit: int, *, wait: float) -> AsyncIterator[None]:
        if limit <= 0:
            yield
            return
        key = (env, function)
        known = self._gates.get(key)
        if known is None or known[0] != limit:
            known = self._gates[key] = (limit, asyncio.Semaphore(limit))
        semaphore = known[1]
        try:
            await asyncio.wait_for(semaphore.acquire(), wait)
        except TimeoutError as exc:
            raise ConcurrencyLimitReached(function, limit) from exc
        try:
            yield
        finally:
            semaphore.release()

    def in_use(self, env: str, function: str) -> tuple[int, int] | None:
        """``(running, limit)`` for a function with a concurrency limit, or ``None``."""
        known = self._gates.get((env, function))
        if known is None:
            return None
        limit, semaphore = known
        return limit - semaphore._value, limit  # noqa: SLF001 - asyncio exposes no public count
