"""What happens after a Resource record changes.

Every write, from the REST API, a flow, a function or a transaction, goes
through :func:`after_write`: cached reads are invalidated by tag, a
``<resource>.<change>`` event is published (when the Resource publishes events),
and resource subscribers on the ``resource:<name>`` realtime channel are told
(when realtime is enabled).
"""

from __future__ import annotations

import logging
from typing import Any

from pawabase_server.data.presentation import public_record
from pawabase_server.environments.state import EnvironmentState
from pawabase_server.platform import Platform

logger = logging.getLogger("pawabase.resources")


def resource_tag(name: str) -> str:
    return f"resource:{name}"


async def after_write(
    platform: Platform,
    state: EnvironmentState,
    resource: str,
    change: str,
    record: dict[str, Any],
    *,
    actor: str | None = None,
    request_id: str | None = None,
) -> None:
    definition = state.resources.get(resource)
    await platform.cache_invalidate(state, [resource_tag(resource)])
    if definition is None:
        return
    if definition.events:
        await platform.emit(
            state,
            f"{resource}.{change}",
            {"resource": resource, "change": change, "record": public_record(state, definition, record)},
            actor=actor,
            request_id=request_id,
        )
    if definition.realtime:
        try:
            await platform.publish(
                state,
                f"resource:{resource}",
                change,
                {"resource": resource, "change": change, "record": record},
            )
        except Exception as exc:  # realtime is best-effort; the write already happened
            logger.warning("could not publish %s.%s to realtime: %s", resource, change, exc)
