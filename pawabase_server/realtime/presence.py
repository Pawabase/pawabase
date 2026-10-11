"""Who is present on which channel, across every instance.

Each instance keeps a replica of presence for all rooms. A member joining or leaving on any instance is announced to all of them through
the fan-out, and each applies it, so ``presence_state`` is the same wherever a client happens to connect. Joins and leaves are
idempotent, so an announcement that arrives twice, or after the same change was applied locally, does nothing.

An instance that dies cannot announce its members' departure. Every instance therefore sends a heartbeat, and entries owned by an instance
that has been silent for ``ttl`` seconds are dropped by :meth:`expire`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Member:
    presence_ref: str
    user_id: str | None
    meta: dict[str, Any]
    online_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    instance: str = ""

    def public(self) -> dict[str, Any]:
        """What clients see: the instance that owns the entry is an implementation detail."""
        data = asdict(self)
        data.pop("instance")
        return data


class PresenceRegistry:
    def __init__(
        self, instance: str, *, ttl: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.instance = instance
        self.ttl = ttl
        self._clock = clock
        self._rooms: dict[str, dict[str, Member]] = {}
        self._seen: dict[str, float] = {}

    # ── reading ──────────────────────────────────────────────────────────

    def members(self, room: str) -> list[dict[str, Any]]:
        return [member.public() for member in self._rooms.get(room, {}).values()]

    def count(self, room: str) -> int:
        return len(self._rooms.get(room, {}))

    def rooms(self) -> list[str]:
        return list(self._rooms)

    def owned(self, room: str, ref: str) -> bool:
        member = self._rooms.get(room, {}).get(ref)
        return member is not None and member.instance == self.instance

    # ── changes ──────────────────────────────────────────────────────────

    def join(self, room: str, member: Member) -> bool:
        """Add or update a member. Returns whether anything changed."""
        members = self._rooms.setdefault(room, {})
        existing = members.get(member.presence_ref)
        if (
            existing is not None
            and existing.meta == member.meta
            and existing.user_id == member.user_id
        ):
            return False
        members[member.presence_ref] = member
        return True

    def leave(self, room: str, ref: str) -> Member | None:
        members = self._rooms.get(room)
        if members is None:
            return None
        member = members.pop(ref, None)
        if not members:
            del self._rooms[room]
        return member

    def apply(
        self,
        room: str,
        joins: Iterable[dict[str, Any]],
        leaves: Iterable[dict[str, Any]],
        origin: str,
    ) -> None:
        """Apply a diff announced by *origin*."""
        for item in leaves:
            ref = item.get("presence_ref")
            if ref:
                self.leave(room, str(ref))
        for item in joins:
            ref = item.get("presence_ref")
            if not ref:
                continue
            self.join(
                room,
                Member(
                    presence_ref=str(ref),
                    user_id=item.get("user_id"),
                    meta=dict(item.get("meta") or {}),
                    online_at=item.get("online_at") or datetime.now(UTC).isoformat(),
                    instance=origin,
                ),
            )

    # ── liveness of other instances ──────────────────────────────────────

    def heard_from(self, instance: str) -> None:
        if instance != self.instance:
            self._seen[instance] = self._clock()

    def expire(self) -> dict[str, list[dict[str, Any]]]:
        """Drop the members of instances not heard from within ``ttl``. Returns, by room, the members that were dropped."""
        now = self._clock()
        silent = {name for name, seen in self._seen.items() if now - seen > self.ttl}
        removed: dict[str, list[dict[str, Any]]] = {}
        for room in list(self._rooms):
            for ref, member in list(self._rooms[room].items()):
                if member.instance in silent:
                    self.leave(room, ref)
                    removed.setdefault(room, []).append(member.public())
        for name in silent:
            del self._seen[name]
        return removed

    def instances(self) -> list[str]:
        return sorted({self.instance, *self._seen})
