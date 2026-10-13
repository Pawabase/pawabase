"""Confining a Studio session to one project of a shared runtime.

A session started by a link with a ``project`` claim may see and change that project's environments and nothing else. The rule is an
allow-list, applied to every request before any route runs, and it fails closed: a path that is not known to be safe is refused. The list of
a project's environments is read from the runtime (cached for a few seconds), so an environment added by the control plane is reachable at once.

What is refused, and why:

* ``/audit``, ``/studio/api/platform/usage`` and every other platform path that is not under ``envs/{env}``: they cover the whole runtime.
* ``promote`` and ``previews`` under an environment, and deleting an environment: they name or create *another* environment.
* A write to an environment's ``infra``: it can repoint the environment's database or storage at someone else's.
"""

from __future__ import annotations

import re
import time
from typing import Any

from pawabase_server.core.clients import ServiceClient

CACHE_SECONDS = 5.0
ENV = re.compile(r"[a-z][a-z0-9_-]{0,62}")
#: Pages and assets that need no environment.
OPEN_PATHS = {"/", "/environments", "/studio/status", "/studio-access"}
# ``platform/usage`` and ``gateway/usage`` are retained for an already-open
# Studio bundle; bridge.py narrows the latter to the signed-link project.
PROJECT_OPEN = {
    "/studio/api/project/usage",
    "/studio/api/platform/usage",
    "/studio/api/gateway/usage",
}
OPEN_PREFIXES = ("/assets/",)
#: Bridge targets whose paths are ``envs/{env}/…`` (platform, auth) or ``{env}/…`` (the others).
ENV_UNDER = {"platform": "envs", "auth": "envs"}
ENV_FIRST = {"realtime", "telemetry", "gateway"}
#: Platform paths that are the same for everyone and carry no tenant data (the flow editor's block catalogue), plus the runtime listing, which
#: the bridge filters to the project.
PLATFORM_OPEN = {"blocks", "runtime"}
#: Path segments under an environment that reach into another environment.
FORBIDDEN_SEGMENTS = {"promote", "previews"}
WRITES = {"POST", "PUT", "PATCH", "DELETE"}


def permitted(path: str, method: str, envs: frozenset[str] | set[str]) -> bool:
    """Whether a Studio request for *path* may run in a session confined to the environments *envs*."""
    path = path.rstrip("/") or "/"
    if path in OPEN_PATHS or path in PROJECT_OPEN or path.startswith(OPEN_PREFIXES):
        return True
    segments = [s for s in path.split("/") if s]
    if any(s in ("..", ".") for s in segments):
        return False
    # Pages: /envs/{env}[/...]
    if segments[:1] == ["envs"] and len(segments) >= 2:
        return segments[1] in envs and not FORBIDDEN_SEGMENTS.intersection(segments[2:])
    if segments[:2] == ["studio", "api"] and len(segments) >= 3:
        target, rest = segments[2], segments[3:]
        if target == "explorer":
            return True  # the handlers check the environment named in the body
        if target == "platform" and len(rest) == 1 and rest[0] in PLATFORM_OPEN and method == "GET":
            return True
        if target in ENV_UNDER:
            if rest[:1] != [ENV_UNDER[target]] or len(rest) < 2 or rest[1] not in envs:
                return False
            if FORBIDDEN_SEGMENTS.intersection(rest[2:]):
                return False
            return not (len(rest) == 2 and method == "DELETE")
        if target in ENV_FIRST:
            return len(rest) >= 1 and rest[0] in envs
        return False
    return segments[:2] == ["studio", "ws"]  # a ticket is for one environment


def body_allowed(path: str, method: str, body: Any) -> bool:
    """The part of the rule that needs the request body: an environment's ``infra`` is not the customer's to set."""
    segments = [s for s in path.split("/") if s]
    if method in WRITES and isinstance(body, dict) and "infra" in body:
        # PATCH /envs/{env}, or POST /envs: the only places infra is written.
        return not (segments[-2:-1] == ["envs"] or segments[-1:] == ["envs"])
    return True


class ProjectScope:
    """Which environments a project has, asked of the runtime and kept for a few seconds."""

    def __init__(self, core: ServiceClient) -> None:
        self.core = core
        self._cache: dict[str, tuple[float, frozenset[str]]] = {}

    async def environments(self, project: str) -> frozenset[str]:
        cached = self._cache.get(project)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        runtime = await self.core.request("GET", "/platform/v1/runtime")
        found = frozenset(
            e["name"] for e in runtime.get("environments", []) if e.get("project_id") == project
        )
        self._cache[project] = (time.monotonic() + CACHE_SECONDS, found)
        return found

    async def guard(self, scope: dict[str, Any], project: str) -> bool:
        """The access gate's question for every request of a confined session. Sets what the handlers need to confine themselves."""
        path = str(scope.get("path") or "").rstrip("/") or "/"
        if path in PROJECT_OPEN:
            # These routes are independently narrowed to the signed project ID;
            # requiring a runtime environment lookup first makes usage disappear
            # during a harmless, transient control-plane lookup failure.
            scope["studio_project"] = project
            scope["studio_envs"] = frozenset()
            return True
        try:
            envs = await self.environments(project)
        except Exception:  # noqa: BLE001 - if the runtime cannot say what the project has, nothing is shown
            return False
        scope["studio_project"] = project
        scope["studio_envs"] = envs
        return permitted(scope.get("path", ""), scope.get("method", "GET"), envs)


def narrow_runtime(runtime: dict[str, Any], project: str) -> dict[str, Any]:
    """A ``/runtime`` answer cut down to one project: its record and its environments."""
    return {
        "name": runtime.get("name"),
        "app_env": runtime.get("app_env"),
        "projects": [p for p in runtime.get("projects", []) if p.get("id") == project],
        "environments": [
            e for e in runtime.get("environments", []) if e.get("project_id") == project
        ],
    }
