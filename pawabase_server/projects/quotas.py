"""A project's plan limits beyond request rates: how much it may have, and how much it may do in a month.

A project's ``limits`` (set by whoever administers the installation: the command line, or Pawabase Cloud through the runtime agent)
name the most of each thing it may hold. The request limits (``daily_request_limit``, ``rate_limit``, …) live at the edge
(``edge/limits.py``); function admission and deployments in ``functions/``. This module enforces the rest, each where the thing is
made or used:

* **Counts** (API keys, webhooks, inbound hooks, schedules, environments, backups, users): counted across the project's environments
  when one more is made. A count is exact; making things is rare enough for the query not to matter.
* **Monthly metering** (function invocations, flow runs): a :class:`UsageCounter` row per project, metric and month, seeded from the
  run records the first time it is needed. Each process leases a few units at a time with one atomic ``UPDATE``, so a busy project does
  not contend on its row; at worst a few leased units per process go unused at the end of a month.
* **Stored bytes**: a gauge, changed by the size difference of every write and delete (:class:`MeteredBucket`), and recounted from the
  storage itself when it is missing or when asked.
* **Database bytes**: the environment's measured size, cached for a minute; writes that add data are refused while it is over.

The checks read ``project_id`` and ``project_limits`` from what they are given: an environment's state, or (in identity, which
does not hold a state) its :class:`~pawabase_server.identity.environment.AuthConfig`, which carries the same two.

Zero or absent means unlimited. A refusal is :class:`QuotaExceeded`: ``403`` with the code ``quota_exceeded`` and the limit, the
allowance and the usage in ``details``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sillo.storage import Bucket, FileNotFound, StorageError, Stored, normalise
from tortoise.exceptions import IntegrityError
from tortoise.expressions import F

from pawabase_server.core.errors import PawabaseError
from pawabase_server.db.models import (
    ApiKey,
    AuthUser,
    BackupSnapshot,
    Environment,
    FunctionRun,
    InboundHook,
    MfaFactor,
    Schedule,
    SessionInfo,
    UsageCounter,
    WebhookEndpoint,
)
from pawabase_server.db.models import FlowRun as FlowRunRecord

logger = logging.getLogger("pawabase.quotas")

#: What a limit counts, for messages.
NOUNS = {
    "max_api_keys": "API keys",
    "max_webhooks": "webhooks",
    "max_inbound_hooks": "inbound hooks",
    "max_scheduled_jobs": "schedules",
    "max_environments": "environments",
    "max_backups": "backups",
    "max_active_users": "users",
    "max_users_per_month": "monthly active users",
    "max_auth_sessions_per_user": "sessions per user",
    "max_mfa_factors_per_user": "second factors per user",
    "max_auth_providers": "sign-in providers",
    "max_function_invocations_per_month": "function invocations this month",
    "max_flow_runs_per_month": "flow runs this month",
    "max_storage_bytes": "bytes of storage",
    "max_upload_bytes": "bytes in one upload",
    "max_database_bytes": "bytes of database",
    "max_realtime_channels": "realtime channels in use",
    # What one function run may use (``functions/limits.py``): a run is held to them, never refused for them.
    "max_function_memory_mb": "MB of memory per function run",
    "max_function_cpu_seconds": "CPU seconds per function run",
    "max_function_timeout_seconds": "seconds per function run",
    "max_concurrent_runs_per_function": "runs of one function at once",
    "max_function_body_mb": "MB of request body per function call",
    "max_function_workers": "workers per HTTP function",
}


class QuotaExceeded(PawabaseError):
    """The project's plan does not allow this."""

    status = 403
    code = "quota_exceeded"

    def __init__(self, limit: str, allowed: int, used: int) -> None:
        noun = NOUNS.get(limit, limit)
        super().__init__(
            f"This project's plan allows {allowed:,} {noun}; it has {used:,}.",
            hint="Remove some, or move the project to a larger plan.",
            details={"limit": limit, "allowed": allowed, "used": used},
        )


def allowance(limits: dict[str, Any] | None, name: str) -> int:
    """The limit *name* in *limits*, or 0 (unlimited). Malformed values count as unlimited and are logged, never raised."""
    value = (limits or {}).get(name) or 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        logger.error("ignoring invalid project limit %s=%r", name, value)
        return 0
    return value


def require_room(name: str, allowed: int, used: int, adding: int = 1) -> None:
    if allowed and used + adding > allowed:
        raise QuotaExceeded(name, allowed, used)


def month(now: datetime | None = None) -> tuple[str, datetime]:
    now = now or datetime.now(UTC)
    return now.strftime("%Y-%m"), datetime(now.year, now.month, 1, tzinfo=UTC)


async def environment_names(project_id: str) -> list[str]:
    return list(await Environment.filter(project_id=project_id).values_list("name", flat=True))


# ── counts ──────────────────────────────────────────────────────────────


async def _definitions(model: Any, project_id: str) -> int:
    return await model.filter(environment__project_id=project_id).count()


COUNTERS: dict[str, Callable[[str], Awaitable[int]]] = {
    "max_api_keys": lambda pid: ApiKey.filter(environment__project_id=pid, revoked_at=None).count(),
    "max_webhooks": lambda pid: _definitions(WebhookEndpoint, pid),
    "max_inbound_hooks": lambda pid: _definitions(InboundHook, pid),
    "max_scheduled_jobs": lambda pid: _definitions(Schedule, pid),
    "max_environments": lambda pid: Environment.filter(project_id=pid).count(),
}


async def check_count(state: Any, name: str, adding: int = 1) -> None:
    """Refuse making *adding* more of what *name* counts when the project would go over its plan."""
    allowed = allowance(state.project_limits, name)
    if not allowed or not state.project_id:
        return
    require_room(name, allowed, await COUNTERS[name](state.project_id), adding)


async def check_project_count(project: Any, name: str, adding: int = 1) -> None:
    """:func:`check_count` for a :class:`~pawabase_server.db.models.Project` (making an environment, before it has a state)."""
    allowed = allowance(project.limits, name)
    if allowed:
        require_room(name, allowed, await COUNTERS[name](project.id), adding)


async def check_backups(state: Any) -> None:
    allowed = allowance(state.project_limits, "max_backups")
    if not allowed or not state.project_id:
        return
    names = await environment_names(state.project_id)
    used = await BackupSnapshot.filter(env__in=names).exclude(status="failed").count()
    require_room("max_backups", allowed, used)


async def oldest_backup_to_replace(state: Any) -> Any | None:
    """For a scheduled backup at the limit: the oldest unpinned snapshot to make room with, or ``None`` when there is room."""
    allowed = allowance(state.project_limits, "max_backups")
    if not allowed or not state.project_id:
        return None
    names = await environment_names(state.project_id)
    snapshots = BackupSnapshot.filter(env__in=names).exclude(status="failed")
    if await snapshots.count() < allowed:
        return None
    oldest = await snapshots.filter(pinned=False).order_by("created_at").first()
    if oldest is None:
        raise QuotaExceeded("max_backups", allowed, await snapshots.count())
    return oldest


async def check_users(state: Any, env: str) -> None:
    allowed = allowance(state.project_limits, "max_active_users")
    if not allowed or not state.project_id:
        return
    names = await environment_names(state.project_id) or [env]
    used = await AuthUser.filter(env__in=names, deleted_at=None).count()
    require_room("max_active_users", allowed, used)


async def check_monthly_user(state: Any, user: Any) -> None:
    """A sign-in by a user not yet active this month counts against ``max_users_per_month``; one already counted is free."""
    allowed = allowance(state.project_limits, "max_users_per_month")
    if not allowed or not state.project_id:
        return
    _, start = month()
    if await SessionInfo.filter(user_id=user.id, created_at__gte=start).exists():
        return
    names = await environment_names(state.project_id)
    active = (
        await SessionInfo.filter(env__in=names, created_at__gte=start)
        .distinct()
        .values_list("user_id", flat=True)
    )
    require_room("max_users_per_month", allowed, len(set(active)))


async def sessions_to_revoke(state: Any, user: Any) -> list[str]:
    """The families to revoke so that a new session keeps the user within ``max_auth_sessions_per_user``: oldest first."""
    allowed = allowance(state.project_limits, "max_auth_sessions_per_user")
    if not allowed:
        return []
    live = await SessionInfo.filter(user_id=user.id, revoked_at=None).order_by("created_at")
    surplus = len(live) - allowed + 1
    return [s.family for s in live[:surplus]] if surplus > 0 else []


async def check_mfa_factors(state: Any, user: Any) -> None:
    allowed = allowance(state.project_limits, "max_mfa_factors_per_user")
    if allowed:
        require_room(
            "max_mfa_factors_per_user", allowed, await MfaFactor.filter(user_id=user.id).count()
        )


def check_auth_providers(limits: dict[str, Any] | None, enabled: list[str]) -> None:
    allowed = allowance(limits, "max_auth_providers")
    if allowed and len(enabled) > allowed:
        raise QuotaExceeded("max_auth_providers", allowed, len(enabled))


# ── monthly metering ────────────────────────────────────────────────────

SEEDS: dict[str, Callable[[list[str], datetime], Awaitable[int]]] = {
    "function_invocations": lambda names, start: FunctionRun.filter(
        env__in=names, created_at__gte=start
    ).count(),
    "flow_runs": lambda names, start: FlowRunRecord.filter(
        env__in=names, created_at__gte=start
    ).count(),
}
METERED = {
    "max_function_invocations_per_month": "function_invocations",
    "max_flow_runs_per_month": "flow_runs",
}


class UsageMeter:
    """Monthly counters, leased a few units at a time (see the module docstring). One per process."""

    def __init__(self) -> None:
        self._leased: dict[tuple[str, str, str], int] = {}
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    @staticmethod
    def lease_size(allowed: int) -> int:
        # Small plans lease one at a time, so what a process holds never makes a real difference to the month.
        return max(1, min(16, allowed // 200))

    async def consume(self, state: Any, limit: str) -> None:
        """Count one use of what *limit* meters for *state*'s project, or raise :class:`QuotaExceeded` when the month is used up."""
        allowed = allowance(state.project_limits, limit)
        if not allowed or not state.project_id:
            return
        metric = METERED[limit]
        period, start = month()
        key = (state.project_id, metric, period)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self._leased.get(key, 0) > 0:
                self._leased[key] -= 1
                return
            granted = await self._lease(state.project_id, metric, period, start, allowed)
            if granted <= 0:
                row = await UsageCounter.get_or_none(
                    project_id=state.project_id, metric=metric, period=period
                )
                raise QuotaExceeded(limit, allowed, row.value if row else allowed)
            self._leased[key] = granted - 1
            for stale in [
                k for k in self._leased if k[0] == key[0] and k[1] == metric and k[2] != period
            ]:
                self._leased.pop(stale, None)

    async def _lease(
        self, project_id: str, metric: str, period: str, start: datetime, allowed: int
    ) -> int:
        row = await UsageCounter.get_or_none(project_id=project_id, metric=metric, period=period)
        if row is None:
            seed = await SEEDS[metric](await environment_names(project_id), start)
            try:
                row = await UsageCounter.create(
                    project_id=project_id, metric=metric, period=period, value=seed
                )
            except IntegrityError:
                row = await UsageCounter.get(project_id=project_id, metric=metric, period=period)
        want = self.lease_size(allowed)
        for _ in range(8):
            granted = min(want, allowed - row.value)
            if granted <= 0:
                return 0
            updated = await UsageCounter.filter(id=row.id, value__lte=allowed - granted).update(
                value=F("value") + granted
            )
            if updated:
                return granted
            await row.refresh_from_db(fields=["value"])
        return 0


# ── stored bytes ────────────────────────────────────────────────────────

STORAGE = "storage_bytes"


async def adjust_storage(project_id: str | None, delta: int) -> None:
    """Move the project's stored-bytes gauge by *delta*. Nothing is written for a project that has no gauge yet."""
    if project_id and delta:
        await UsageCounter.filter(project_id=project_id, metric=STORAGE, period="").update(
            value=F("value") + delta
        )


async def recount_storage(platform: Any, project_id: str) -> int:
    """Count every object in every bucket of the project's environments and store the gauge. Returns the total."""
    total = 0
    for name in await environment_names(project_id):
        state = await platform.state(name)
        for bucket in state.buckets:
            total += await _bucket_bytes(platform.storage.driver(state, bucket))
    row, created = await UsageCounter.get_or_create(
        project_id=project_id, metric=STORAGE, period="", defaults={"value": total}
    )
    if not created:
        row.value = total
        await row.save(update_fields=["value", "updated_at"])
    return total


async def _bucket_bytes(driver: Any) -> int:
    seen: set[str] = set()
    total = 0
    pending = [""]
    while pending:
        prefix = pending.pop()
        cursor = ""
        while True:
            page = await driver.page(prefix, cursor=cursor, limit=1000)
            for info in page.files:
                if info.key not in seen:
                    seen.add(info.key)
                    total += int(info.size or 0)
            pending.extend(p for p in page.prefixes if p != prefix)
            if not page.cursor:
                break
            cursor = page.cursor
    return total


async def storage_used(platform: Any, project_id: str) -> int:
    row = await UsageCounter.get_or_none(project_id=project_id, metric=STORAGE, period="")
    if row is None:
        return await recount_storage(platform, project_id)
    return max(0, row.value)


class MeteredBucket(Bucket):
    """A bucket that keeps the project's stored-bytes gauge and refuses writes past ``max_storage_bytes``.

    Every write path (the data API, signed uploads, Studio, functions) gets its bucket from the storage manager, so all of them are
    metered here. A replacement counts the difference; a delete gives the bytes back.
    """

    def __init__(self, *args: Any, platform: Any, state: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._platform = platform
        self._state = state

    async def _previous_size(self, key: str) -> int:
        try:
            return int((await self.driver.stat(normalise(key))).size or 0)
        except (FileNotFound, StorageError):
            return 0

    async def put(self, key: str, stream: AsyncIterator[bytes], **kwargs: Any) -> Stored:
        project_id = self._state.project_id
        if not project_id:
            return await super().put(key, stream, **kwargs)
        previous = await self._previous_size(key)
        allowed = allowance(self._state.project_limits, "max_storage_bytes")
        bucket_cap = self.max_bytes
        quota_cap = 0
        if allowed:
            used = await storage_used(self._platform, project_id)
            room = allowed - used + previous
            if room <= 0:
                raise QuotaExceeded("max_storage_bytes", allowed, used)
            quota_cap = room
            if not self.max_bytes or room < self.max_bytes:
                self.max_bytes = room
        try:
            stored = await super().put(key, stream, **kwargs)
        except StorageError as exc:
            # Sillo's stream cap ("object exceeds the bucket's limit of N bytes"), where N was the plan's room, not the bucket's.
            if (
                quota_cap
                and self.max_bytes == quota_cap
                and f"limit of {quota_cap} bytes" in str(exc)
            ):
                raise QuotaExceeded(
                    "max_storage_bytes", allowed, await storage_used(self._platform, project_id)
                ) from exc
            raise
        finally:
            self.max_bytes = bucket_cap
        await adjust_storage(project_id, int(stored.size or 0) - previous)
        return stored

    async def delete(self, key: str, **kwargs: Any) -> bool:
        project_id = self._state.project_id
        previous = await self._previous_size(key) if project_id else 0
        removed = await super().delete(key, **kwargs)
        if removed and previous:
            await adjust_storage(project_id, -previous)
        return removed


def upload_cap(state: Any, current: int) -> int:
    """The per-upload limit with the project's ``max_upload_bytes`` applied (the smaller wins; 0 is unlimited)."""
    allowed = allowance(state.project_limits, "max_upload_bytes")
    if not allowed:
        return current
    return min(current, allowed) if current else allowed


# ── database bytes ──────────────────────────────────────────────────────

_sizes: dict[str, tuple[float, int | None]] = {}
SIZE_SECONDS = 60.0


async def check_database(state: Any) -> None:
    """Refuse a write that adds data while the environment's database is over ``max_database_bytes``. Deleting stays possible."""
    allowed = allowance(state.project_limits, "max_database_bytes")
    if not allowed or not state.project_id:
        return
    now = time.monotonic()
    cached = _sizes.get(state.env_name)
    if cached is None or cached[0] < now:
        size = await (await state.source()).size_bytes()
        cached = _sizes[state.env_name] = (now + SIZE_SECONDS, size)
    size = cached[1]
    if size is not None and size >= allowed:
        raise QuotaExceeded("max_database_bytes", allowed, size)


def forget_database_size(env: str) -> None:
    _sizes.pop(env, None)


# ── reporting ───────────────────────────────────────────────────────────


async def usage_report(platform: Any, state: Any) -> list[dict[str, Any]]:
    """Every plan limit the project has, with what it allows and what is used now (``used`` is ``None`` where it is per user or per
    upload rather than a total). Limits enforced at the edge (requests, rates) are reported by the gateway, not here."""
    limits = {
        k: v for k, v in (state.project_limits or {}).items() if allowance(state.project_limits, k)
    }
    pid = state.project_id
    if not pid or not limits:
        return []
    names = await environment_names(pid)
    period, start = month()

    async def monthly(metric: str) -> int:
        row = await UsageCounter.get_or_none(project_id=pid, metric=metric, period=period)
        return row.value if row else await SEEDS[metric](names, start)

    async def database() -> int | None:
        return await (await state.source()).size_bytes()

    measures: dict[str, Callable[[], Awaitable[int | None]]] = {
        **{name: (lambda counter=counter: counter(pid)) for name, counter in COUNTERS.items()},
        "max_backups": lambda: (
            BackupSnapshot.filter(env__in=names).exclude(status="failed").count()
        ),
        "max_active_users": lambda: AuthUser.filter(env__in=names, deleted_at=None).count(),
        "max_users_per_month": lambda: (
            SessionInfo.filter(env__in=names, created_at__gte=start)
            .distinct()
            .values_list("user_id", flat=True)
            .count()
        ),
        "max_function_invocations_per_month": lambda: monthly("function_invocations"),
        "max_flow_runs_per_month": lambda: monthly("flow_runs"),
        "max_storage_bytes": lambda: storage_used(platform, pid),
        "max_database_bytes": database,
    }
    report = []
    for name, allowed in sorted(limits.items()):
        measure = measures.get(name)
        if name in (
            "daily_request_limit",
            "monthly_request_limit",
            "rate_limit",
            "rate_window",
            "max_active_connections",
        ):
            continue
        used = await measure() if measure else None
        report.append(
            {
                "limit": name,
                "label": NOUNS.get(name, name),
                "allowed": allowed,
                "used": used,
                "percent": round(100 * used / allowed, 1) if used is not None and allowed else None,
                "period": period if name.endswith("_per_month") else None,
            }
        )
    return report
