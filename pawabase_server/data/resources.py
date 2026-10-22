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
    """What follows a write the API made: invalidate cached reads, then announce it (events and realtime).

    A resource with ``capture`` on lets the database announce its changes instead (the triggers see this write too, and every
    other one), so the announcement is not made twice: the capture poller is nudged to deliver it at once.
    """
    definition = state.resources.get(resource)
    await platform.cache_invalidate(state, [resource_tag(resource)])
    if definition is None:
        return
    if definition.capture and await _captured(state):
        capture = getattr(platform, "capture", None)
        if capture is not None:
            capture.kick()
        return
    await announce(
        platform,
        state,
        resource,
        change,
        record,
        actor=actor,
        request_id=request_id,
        invalidate=False,
    )


async def _captured(state: EnvironmentState) -> bool:
    """Whether this environment's database can capture changes (so a resource's ``capture`` flag is in force)."""
    from pawabase_server.data import cdc

    return cdc.supported((await state.source()).dialect)


async def announce(
    platform: Platform,
    state: EnvironmentState,
    resource: str,
    change: str,
    record: dict[str, Any],
    *,
    actor: str | None = None,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
    invalidate: bool = True,
) -> None:
    """Tell the platform a record changed: the ``<resource>.<change>`` event and the ``resource:<name>`` channel.

    *extra* rides along in the realtime message (a captured change says which columns changed and that it came from the
    database). Cached reads are invalidated first unless the caller already did.
    """
    definition = state.resources.get(resource)
    if invalidate:
        await platform.cache_invalidate(state, [resource_tag(resource)])
    if definition is None:
        return
    if definition.events:
        await platform.emit(
            state,
            f"{resource}.{change}",
            {
                "resource": resource,
                "change": change,
                "record": public_record(state, definition, record),
                **(extra or {}),
            },
            actor=actor,
            request_id=request_id,
        )
    if definition.realtime:
        try:
            await platform.publish(
                state,
                f"resource:{resource}",
                change,
                {"resource": resource, "change": change, "record": record, **(extra or {})},
            )
        except Exception as exc:  # realtime is best-effort; the write already happened
            logger.warning("could not publish %s.%s to realtime: %s", resource, change, exc)
