"""Record helpers Sillo's models need.

Sillo's ``Model.update_from_dict`` is a coroutine (it applies the dict and
saves), but Tortoise's ``update_or_create`` calls it synchronously and chains
``.save()`` on the result, so it raises on every *update*, the path that only
runs once a row exists (a heartbeat after a restart, a secret being
replaced). :func:`upsert` is the replacement.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def upsert(
    model: Any, defaults: Mapping[str, Any] | None = None, **lookup: Any
) -> tuple[Any, bool]:
    """Update the row matching *lookup* with *defaults*, or create it.

    Updates go through ``save()`` so field hooks such as ``updated_at`` run.
    A concurrent insert of the same row is retried as an update.

    Returns:
        ``(instance, created)``, like ``update_or_create``.
    """
    from tortoise.exceptions import IntegrityError

    values = dict(defaults or {})

    async def update() -> Any | None:
        instance = await model.filter(**lookup).first()
        if instance is None:
            return None
        for name, value in values.items():
            setattr(instance, name, value)
        await instance.save()
        return instance

    instance = await update()
    if instance is not None:
        return instance, False
    try:
        return await model.create(**lookup, **values), True
    except IntegrityError:
        instance = await update()
        if instance is None:
            raise
        return instance, False
