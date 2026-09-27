"""Python functions and extension routes: the escape hatch.

A project's code lives in a directory the API mounts, one folder per project::

    code/
      acme/
        functions/
          billing.py      # @function("charge-order") …
        routes.py         # router = Router(prefix="/billing") (plain Sillo)
        policies.py       # @policy("same-team") …
        transformers.py   # @transformer("public-order") …

Functions are ordinary async Python::

    from pawabase_kit.functions import function

    @function("charge-order", description="Charge an order's card")
    async def charge_order(ctx):
        order = await ctx.runtime.resource_get("orders", ctx.input["order_id"])
        ...
        return {"charged": True}

Nothing here is a language of its own. ``ctx.runtime`` is the same capability
set blocks use, and anything else Python can import is available.
"""

from __future__ import annotations

import contextvars
import importlib.util
import inspect
import logging
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("pawabase.functions")

_loading_project: contextvars.ContextVar[str] = contextvars.ContextVar(
    "pawabase_loading_project", default="*"
)


@dataclass(slots=True)
class FunctionSpec:
    """A registered function.

    Attributes:
        name: How routes, flows, schedules and jobs refer to it.
        handler: ``async def handler(ctx) -> Any``.
        project: Which project registered it (``*`` for every project).
        description: Shown in Studio.
        policy: Who may invoke it over HTTP (``/functions/v1/<name>``).
        input_fields: Optional field definitions validating the input.
        timeout: Seconds before an invocation is cancelled.
    """

    name: str
    handler: Callable[..., Any]
    project: str = "*"
    description: str = ""
    policy: Any = "authenticated"
    input_fields: list[dict[str, Any]] | None = None
    timeout: float = 30.0

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project": self.project,
            "description": self.description,
            "policy": self.policy,
            "input_fields": self.input_fields,
            "timeout": self.timeout,
            "module": getattr(self.handler, "__module__", None),
        }


@dataclass(slots=True)
class FunctionContext:
    """What a function receives.

    Attributes:
        input: The invocation payload.
        auth: The caller's policy context (``authenticated``, ``user_id``…).
        project, env: Where it runs.
        runtime: Platform capabilities (see :class:`pawabase_kit.flows.Runtime`).
        trigger: ``http``, ``flow``, ``job``, ``schedule``, ``event`` or ``webhook``.
    """

    input: Any
    auth: Mapping[str, Any]
    project: str
    env: str
    runtime: Any
    trigger: str = "http"
    logs: list[dict[str, Any]] = field(default_factory=list)

    def log(self, message: str, **data: Any) -> None:
        self.logs.append({"message": message, **data})


_registry: dict[tuple[str, str], FunctionSpec] = {}


def function(
    name: str,
    *,
    description: str = "",
    policy: Any = "authenticated",
    input_fields: list[dict[str, Any]] | None = None,
    timeout: float = 30.0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register an async function under *name* for the project being loaded."""

    def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(handler):
            raise TypeError(f"function {name!r} must be async")
        project = _loading_project.get()
        _registry[(project, name)] = FunctionSpec(
            name=name,
            handler=handler,
            project=project,
            description=description or (handler.__doc__ or "").strip(),
            policy=policy,
            input_fields=input_fields,
            timeout=timeout,
        )
        return handler

    return decorator


def get_function(project: str, name: str) -> FunctionSpec | None:
    """The function *name* visible to *project* (its own first, then shared)."""
    return _registry.get((project, name)) or _registry.get(("*", name))


def list_functions(project: str) -> list[FunctionSpec]:
    """Every function visible to *project*."""
    own = {spec.name: spec for (owner, _), spec in _registry.items() if owner == project}
    shared = {
        spec.name: spec
        for (owner, _), spec in _registry.items()
        if owner == "*" and spec.name not in own
    }
    return sorted([*own.values(), *shared.values()], key=lambda spec: spec.name)


def clear_functions() -> None:
    """Forget every registered function. For tests."""
    _registry.clear()


@dataclass(slots=True)
class ProjectCode:
    """What loading one project's code directory produced."""

    project: str
    modules: list[str] = field(default_factory=list)
    router: Any = None
    errors: list[str] = field(default_factory=list)


def _import_file(module_name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_project_code(root: str | Path, project: str) -> ProjectCode:
    """Import ``<root>/<project>``: functions, policies, transformers and routes.

    Import errors are collected and reported, not raised: one broken module in
    one project must not stop the API from serving every other project.
    """
    result = ProjectCode(project=project)
    base = Path(root) / project
    if not base.is_dir():
        return result
    token = _loading_project.set(project)
    try:
        files = sorted((base / "functions").glob("*.py")) if (base / "functions").is_dir() else []
        files += [
            base / name
            for name in ("policies.py", "transformers.py", "routes.py")
            if (base / name).is_file()
        ]
        for path in files:
            if path.name.startswith("_"):
                continue
            module_name = (
                f"pawabase_code.{project}.{path.parent.name}.{path.stem}"
                if path.parent != base
                else f"pawabase_code.{project}.{path.stem}"
            )
            try:
                module = _import_file(module_name, path)
                result.modules.append(module_name)
                if path.name == "routes.py":
                    result.router = getattr(module, "router", None)
            except Exception as exc:
                logger.exception("could not load %s", path)
                result.errors.append(f"{path.relative_to(base)}: {type(exc).__name__}: {exc}")
    finally:
        _loading_project.reset(token)
    return result
