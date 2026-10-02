"""Reusable response transformers.

A transformer turns stored data into what a consumer should see. Declarative
transformers are data, so Studio edits them::

    {
        "pick": ["id", "title", "author_id", "created_at"],
        "rename": {"author_id": "authorId"},
        "set": {"url": "https://example.com/posts/{{ record.id }}"},
        "case": "camel"
    }

Steps run in a fixed order: ``omit``, ``pick``, ``set``, ``rename``, ``case``.
Anything more involved is a Python transformer registered with
:func:`transformer`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from sillo.helpers.strings import camel_to_snake, snake_to_camel

from .templating import render

_python: dict[str, Callable[..., Any]] = {}

STEPS = ("omit", "pick", "set", "rename", "case")


class TransformerError(ValueError):
    """A transformer definition that cannot be applied."""


def transformer(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a Python transformer: ``(record, context) -> dict``."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        _python[name] = func
        return func

    return decorator


def python_transformers() -> dict[str, Callable[..., Any]]:
    """Every Python transformer registered in this process."""
    return dict(_python)


def validate_transformer(definition: Mapping[str, Any]) -> None:
    """Refuse an unknown step or a malformed argument."""
    for key, value in definition.items():
        if key not in STEPS:
            raise TransformerError(
                f"unknown transformer step {key!r}; expected one of {', '.join(STEPS)}"
            )
        if key in ("omit", "pick") and not isinstance(value, list):
            raise TransformerError(f"{key} takes a list of field names")
        if key in ("set", "rename") and not isinstance(value, Mapping):
            raise TransformerError(f"{key} takes an object")
        if key == "case" and value not in ("camel", "snake"):
            raise TransformerError("case is 'camel' or 'snake'")


def _apply_one(
    record: Mapping[str, Any], definition: Mapping[str, Any], context: Mapping[str, Any]
) -> dict[str, Any]:
    data = dict(record)
    if "omit" in definition:
        for key in definition["omit"]:
            data.pop(key, None)
    if "pick" in definition:
        data = {key: data[key] for key in definition["pick"] if key in data}
    if "set" in definition:
        state = {**context, "record": record}
        for key, template in definition["set"].items():
            data[key] = render(template, state)
    if "rename" in definition:
        for old, new in definition["rename"].items():
            if old in data:
                data[new] = data.pop(old)
    if definition.get("case") == "camel":
        data = {snake_to_camel(key): value for key, value in data.items()}
    elif definition.get("case") == "snake":
        data = {camel_to_snake(key): value for key, value in data.items()}
    return data


async def apply_transformer(
    data: Any,
    definition: Mapping[str, Any] | str | None,
    *,
    context: Mapping[str, Any] | None = None,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> Any:
    """Transform a record or a list of records.

    Args:
        data: A mapping or a list of mappings.
        definition: An inline definition, the name of a stored or Python
            transformer, or ``None`` for no change.
        context: Extra template state (``auth``, ``project``...).
        registry: Stored transformers by name.
    """
    if definition is None:
        return data
    context = context or {}
    if isinstance(definition, str):
        if definition in _python:
            func = _python[definition]
            if isinstance(data, list):
                results = [func(item, context) for item in data]
                return [await r if inspect.isawaitable(r) else r for r in results]
            result = func(data, context)
            return await result if inspect.isawaitable(result) else result
        if registry and definition in registry:
            definition = registry[definition]
        else:
            raise TransformerError(f"unknown transformer {definition!r}")
    if isinstance(data, list):
        return [_apply_one(item, definition, context) for item in data]
    if isinstance(data, Mapping):
        return _apply_one(data, definition, context)
    return data
