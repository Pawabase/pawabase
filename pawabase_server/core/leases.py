"""Named leases: one process at a time does a thing (the scheduler fires), and another takes over when it stops renewing.

A lease is a row with a holder and an expiry. Taking it is one conditional ``UPDATE`` (free, expired, or already ours), or an ``INSERT``
the first time; the database decides between two contenders. Works on every database the platform runs on, with or without Redis.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tortoise.exceptions import IntegrityError
from tortoise.expressions import Q

from pawabase_server.db.models import Lease


async def acquire(name: str, holder: str, ttl: float) -> bool:
    """Take or renew *name* for *holder* for *ttl* seconds. True when *holder* holds it afterwards."""
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=ttl)
    taken = await Lease.filter(Q(name=name) & (Q(holder=holder) | Q(expires_at__lt=now))).update(
        holder=holder, expires_at=expires
    )
    if taken:
        return True
    if await Lease.filter(name=name).exists():
        return False
    try:
        await Lease.create(name=name, holder=holder, expires_at=expires)
    except IntegrityError:
        return False
    return True


async def release(name: str, holder: str) -> None:
    """Give *name* up now, so another process need not wait for it to expire."""
    # Expired rather than deleted: the row is reused by the next holder.
    await Lease.filter(name=name, holder=holder).update(
        expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )


async def holder_of(name: str) -> str | None:
    lease = await Lease.get_or_none(name=name)
    if lease is None or lease.expires_at < datetime.now(UTC):
        return None
    return lease.holder
