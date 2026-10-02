"""Custom functions on the platform side.

The decorator, the context and the loader are the public kit's (:mod:`pawabase.functions`): the code a developer deploys imports them from there, so the
server must use the very same objects or a deployed function would register somewhere the platform never looks. This module re-exports them for the
services, and adds what only the platform needs.

What ``pawabase deploy`` uploads lives under the registry key ``<project>/<env>`` (``<project>/<env>@<branch>`` for a feature branch). A function is looked
for on the branch first, then in the environment, then in code mounted for the whole project, so a branch only has to carry what it changed.
"""

from __future__ import annotations

from pawabase.functions import (
    FunctionContext,
    FunctionError,
    FunctionSpec,
    InputError,
    ProjectCode,
    _loading_project,
    clear_functions,
    function,
    get_exact,
    get_function,
    import_scope,
    list_functions,
    load_code_dir,
    load_functions,
    load_project_code,
    validate_input,
)

MAIN = "main"


def deployment_key(project: str, env: str, branch: str | None = None) -> str:
    """The registry name of what ``pawabase deploy`` uploaded for *project*'s *env* (on *branch*, when it is not ``main``)."""
    base = f"{project}/{env}"
    return base if not branch or branch == MAIN else f"{base}@{branch}"


def lookup_order(project: str, env: str, branch: str | None) -> list[str]:
    """Where a function is looked for, most specific first: the branch's deployment, the environment's, the project's mounted code, then shared code."""
    keys = [deployment_key(project, env, branch)] if branch and branch != MAIN else []
    return [*keys, deployment_key(project, env), project, "*"]


def resolve_function(project: str, env: str, branch: str | None, name: str) -> FunctionSpec | None:
    """The function *name* as *branch* of *project*'s *env* sees it."""
    for key in lookup_order(project, env, branch):
        spec = get_exact(key, name)
        if spec is not None:
            return spec
    return None


def list_resolved_functions(project: str, env: str, branch: str | None) -> list[FunctionSpec]:
    """Every function visible there, each from the most specific place that defines it."""
    merged: dict[str, FunctionSpec] = {}
    for key in reversed(lookup_order(project, env, branch)):
        for spec in list_functions(key):
            if spec.project == key:  # list_functions also returns shared ones; only this owner's count here
                merged[spec.name] = spec
    return sorted(merged.values(), key=lambda spec: spec.name)


__all__ = [
    "FunctionContext",
    "FunctionError",
    "FunctionSpec",
    "InputError",
    "MAIN",
    "ProjectCode",
    "_loading_project",
    "clear_functions",
    "deployment_key",
    "function",
    "get_exact",
    "get_function",
    "import_scope",
    "list_resolved_functions",
    "lookup_order",
    "resolve_function",
    "list_functions",
    "load_code_dir",
    "load_functions",
    "load_project_code",
    "validate_input",
]
