"""``{{ path }}`` lookups in block configuration.

A template is a lookup, not a language: ``{{ steps.fetch.output.id }}`` reads a
value, and a small set of filters formats it. A string that is entirely one
template returns the value itself, not its text, so ``"{{ input.items }}"``
passes a list along unchanged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .policies.engine import lookup

_TEMPLATE = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_WHOLE = re.compile(r"^\{\{\s*(.+?)\s*\}\}$")


def _apply_filter(value: Any, name: str, argument: str | None) -> Any:
    if name == "default":
        return value if value not in (None, "") else _literal(argument)
    if name == "upper":
        return str(value).upper() if value is not None else value
    if name == "lower":
        return str(value).lower() if value is not None else value
    if name == "json":
        return json.dumps(value, default=str)
    if name == "length":
        return len(value) if value is not None else 0
    if name == "int":
        return int(value) if value not in (None, "") else None
    if name == "float":
        return float(value) if value not in (None, "") else None
    if name == "str":
        return "" if value is None else str(value)
    if name == "bool":
        return bool(value)
    if name == "first":
        return value[0] if value else None
    if name == "last":
        return value[-1] if value else None
    if name == "keys":
        return list(value.keys()) if isinstance(value, Mapping) else []
    raise ValueError(f"unknown template filter {name!r}")


def _literal(argument: str | None) -> Any:
    if argument is None:
        return None
    try:
        return json.loads(argument)
    except ValueError:
        return argument


def evaluate_expression(expression: str, state: Mapping[str, Any]) -> Any:
    """Evaluate ``path | filter:arg | filter``."""
    parts = [part.strip() for part in expression.split("|")]
    value = lookup(state, parts[0])
    for spec in parts[1:]:
        name, _, argument = spec.partition(":")
        value = _apply_filter(value, name.strip(), argument.strip() or None)
    return value


def render(value: Any, state: Mapping[str, Any]) -> Any:
    """Render every template inside *value* (strings, lists and dicts)."""
    if isinstance(value, str):
        whole = _WHOLE.match(value)
        if whole:
            return evaluate_expression(whole.group(1), state)
        if "{{" not in value:
            return value

        def substitute(match: re.Match[str]) -> str:
            result = evaluate_expression(match.group(1), state)
            if result is None:
                return ""
            if isinstance(result, (dict, list)):
                return json.dumps(result, default=str)
            return str(result)

        return _TEMPLATE.sub(substitute, value)
    if isinstance(value, list):
        return [render(item, state) for item in value]
    if isinstance(value, Mapping):
        return {key: render(item, state) for key, item in value.items()}
    return value
