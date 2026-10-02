"""Python policies: the escape hatch for rules a JSON condition cannot express.

    from pawabase_core.policies import policy

    @policy("same-team", description="Callers on the record's team")
    async def same_team(context):
        team = await lookup_team(context["record"]["team_id"])
        return context["auth"]["user_id"] in team.members

A registered policy is referenced by name exactly like a stored one.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .engine import Policy

_registry: dict[str, Policy] = {}


def policy(
    name: str, *, description: str = ""
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a Python policy under *name*."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _registry[name] = Policy(
            name=name, handler=func, description=description or (func.__doc__ or "").strip()
        )
        return func

    return decorator


def python_policies() -> dict[str, Policy]:
    """Every Python policy registered in this process."""
    return dict(_registry)


def clear_python_policies() -> None:
    """Forget every registered Python policy. For tests."""
    _registry.clear()
