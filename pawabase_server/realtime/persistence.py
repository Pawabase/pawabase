"""Durable storage for channels whose rule says ``kind="durable"`` — the only place in realtime that writes to
a real table on the hot path. Everything else stays in-memory/Redis-backed, as it always has (see
``pawabase_server/realtime/fanout.py``'s module docstring).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any


async def store_durable(
    platform: Any,
    env: str,
    channel: str,
    event: str,
    payload: Any,
    *,
    client_msg_id: str | None,
    sender: str | None,
    retention_seconds: int,
) -> tuple[Any, bool]:
    """Store one durable message, or return the one already stored under *client_msg_id* for this channel.

    Returns ``(row, created)`` — ``created`` is ``False`` on a dedup hit, so the caller knows not to deliver
    again.
    """
    from pawabase_server.db.models import RealtimeMessage

    state = await platform.state(env)
    environment_id = state.environment.id
    if client_msg_id:
        existing = await RealtimeMessage.get_or_none(
            environment_id=environment_id, channel=channel, client_msg_id=client_msg_id
        )
        if existing is not None:
            return existing, False
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=retention_seconds) if retention_seconds > 0 else None
    )
    row = await RealtimeMessage.create(
        environment_id=environment_id,
        channel=channel,
        event=event,
        data=payload if isinstance(payload, (dict, list)) else {"value": payload},
        client_msg_id=client_msg_id,
        seq=int(time.time() * 1000),
        sender=sender,
        expires_at=expires_at,
    )
    return row, True


async def durable_history(
    platform: Any, env: str, channel: str, limit: int
) -> list[dict[str, Any]]:
    """The most recent durable messages on *channel*, newest-last (matching the in-memory backlog's order)."""
    from pawabase_server.db.models import RealtimeMessage

    state = await platform.state(env)
    rows = (
        await RealtimeMessage.filter(environment_id=state.environment.id, channel=channel)
        .order_by("-created_at")
        .limit(limit)
    )
    out = [
        {
            "type": "message",
            "id": row.id,
            "channel": channel,
            "event": row.event,
            "payload": row.data,
            "from": row.sender,
            "sent_at": row.created_at.isoformat() if row.created_at else None,
            "seq": row.seq,
        }
        for row in rows
    ]
    out.reverse()
    return out


async def purge_expired(batch_size: int = 500) -> int:
    """Delete durable messages whose retention has passed. Returns how many were removed, for the sweep log."""
    from pawabase_server.db.models import RealtimeMessage

    expired = (
        await RealtimeMessage.filter(expires_at__lt=datetime.now(UTC))
        .limit(batch_size)
        .values_list("id", flat=True)
    )
    if not expired:
        return 0
    await RealtimeMessage.filter(id__in=expired).delete()
    return len(expired)
