"""Custom functions on the platform side.

The decorator, the context and the loader are the public kit's (:mod:`pawabase.functions`): the code a developer deploys imports them from there, so the
server must use the very same objects or a deployed function would register somewhere the platform never looks. This module re-exports them for the
services, and adds what only the platform needs.

What ``pawabase deploy`` uploads lives under the registry key ``runtime/<env>`` (``runtime/<env>@<branch>`` for a feature branch). A function is looked
for on the branch first, then in the environment, then in code mounted on the runtime, so a branch only has to carry what it changed.

The public kit's registry names each owner with a string, a leftover of when one installation hosted many projects. There is one owner here, :data:`RUNTIME`.
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


#: The registry owner of everything this runtime loads.
RUNTIME = "runtime"


def deployment_key(env: str, branch: str | None = None) -> str:
    """The registry name of what ``pawabase deploy`` uploaded for *env* (on *branch*, when it is not ``main``)."""
    base = f"{RUNTIME}/{env}"
    return base if not branch or branch == MAIN else f"{base}@{branch}"


def lookup_order(env: str, branch: str | None) -> list[str]:
    """Where a function is looked for, most specific first: the branch's deployment, the environment's, the mounted code, then shared code."""
    keys = [deployment_key(env, branch)] if branch and branch != MAIN else []
    return [*keys, deployment_key(env), RUNTIME, "*"]


def resolve_function(env: str, branch: str | None, name: str) -> FunctionSpec | None:
    """The function *name* as *branch* of *env* sees it."""
    for key in lookup_order(env, branch):
        spec = get_exact(key, name)
        if spec is not None:
            return spec
    return None


def list_resolved_functions(env: str, branch: str | None) -> list[FunctionSpec]:
    """Every function visible there, each from the most specific place that defines it."""
    merged: dict[str, FunctionSpec] = {}
    for key in reversed(lookup_order(env, branch)):
        for spec in list_functions(key):
            if spec.project == key:  # list_functions also returns shared ones; only this owner's count here
                merged[spec.name] = spec
    return sorted(merged.values(), key=lambda spec: spec.name)


__all__ = [
    "RUNTIME",
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
