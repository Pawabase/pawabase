"""Saved backups: taken on demand or on a schedule, kept in object storage, checked, pruned and restored.

A snapshot is the same document ``app.backups`` produces, gzipped into the platform's storage under
``<env>/_backups/``. Its own checksum covers the document; the digest kept beside it covers the stored
bytes, so damage is found whether the storage or the document went wrong.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app import backups
from database.models import BackupSchedule, BackupSnapshot, Environment

if TYPE_CHECKING:
    from app.platform import Platform

log = logging.getLogger("pawabase.snapshots")

BUCKET = "_backups"
FREQUENCIES = ("hourly", "daily", "weekly")
#: Scheduled snapshots are the only ones retention removes.
PRUNED = "scheduled"


class SnapshotError(Exception):
    """A snapshot that can't be taken, read or trusted, and why."""


async def _driver(platform: Platform, env: str) -> Any:
    return platform.storage.driver(await platform.state(env), BUCKET)


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


def _key(snapshot_id: str) -> str:
    return f"{snapshot_id}.json.gz"


async def take(
    platform: Platform,
    env: str,
    *,
    include: list[str] | None = None,
    name: str = "",
    note: str = "",
    trigger: str = "manual",
) -> BackupSnapshot:
    environment = await Environment.get(name=env)
    parts = backups.parse_parts(include)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    snapshot = await BackupSnapshot.create(
        env=env,
        name=name or f"{trigger.replace('-', ' ').capitalize()} · {stamp}",
        note=note,
        trigger=trigger,
        include=parts,
        status="running",
    )
    try:
        document = await backups.create_backup(platform, environment, parts)
        raw = gzip.compress(json.dumps(document, separators=(",", ":")).encode(), 6)
        driver = await _driver(platform, env)
        await driver.write(_key(snapshot.id), _chunks(raw), content_type="application/gzip")
        snapshot.size_bytes = len(raw)
        snapshot.checksum = hashlib.sha256(raw).hexdigest()
        snapshot.storage_key = _key(snapshot.id)
        snapshot.counts = backups.summarise(document)
        snapshot.status = "complete"
    except Exception as exc:
        snapshot.status = "failed"
        snapshot.error = str(exc)[:500]
        log.warning("snapshot of %s failed: %s", env, exc)
    await snapshot.save()
    return snapshot


async def _raw(platform: Platform, snapshot: BackupSnapshot) -> bytes:
    if snapshot.status != "complete":
        raise SnapshotError(f"this snapshot is {snapshot.status}, there is nothing stored")
    driver = await _driver(platform, snapshot.env)
    try:
        raw = b"".join([chunk async for chunk in driver.read(snapshot.storage_key)])
    except Exception as exc:
        raise SnapshotError(f"the stored file could not be read: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != snapshot.checksum:
        raise SnapshotError(
            "the stored file does not match its checksum: it was changed or is damaged"
        )
    return raw


async def read(platform: Platform, snapshot: BackupSnapshot) -> dict[str, Any]:
    raw = await _raw(platform, snapshot)
    try:
        document = json.loads(gzip.decompress(raw))
        backups.verify(document)
    except (OSError, ValueError, backups.BackupError) as exc:
        raise SnapshotError(f"the snapshot cannot be used: {exc}") from exc
    return document


async def check(platform: Platform, snapshot: BackupSnapshot) -> dict[str, Any]:
    """Read it back and test the checksums, as a restore would."""
    try:
        document = await read(platform, snapshot)
    except SnapshotError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "parts": [p for p in backups.PARTS if p in document],
        "contents": backups.summarise(document),
    }


async def remove(platform: Platform, snapshot: BackupSnapshot) -> None:
    if snapshot.storage_key:
        try:
            await (await _driver(platform, snapshot.env)).delete(snapshot.storage_key)
        except Exception as exc:
            log.warning("could not delete stored snapshot %s: %s", snapshot.id, exc)
    await snapshot.delete()


async def prune(platform: Platform, schedule: BackupSchedule) -> int:
    """Remove scheduled snapshots past ``keep_last`` or ``keep_days``. Pinned ones stay."""
    rows = await BackupSnapshot.filter(env=schedule.env, trigger=PRUNED).order_by("-id")
    cutoff = datetime.now(UTC) - timedelta(days=schedule.keep_days) if schedule.keep_days else None
    removed = 0
    for position, snapshot in enumerate(rows):
        if snapshot.pinned:
            continue
        too_many = bool(schedule.keep_last) and position >= schedule.keep_last
        too_old = cutoff is not None and snapshot.created_at < cutoff
        if too_many or too_old:
            await remove(platform, snapshot)
            removed += 1
    return removed


def latest_slot(schedule: BackupSchedule, now: datetime) -> datetime:
    """The most recent moment the schedule says a snapshot should have been taken."""
    slot = now.replace(minute=0, second=0, microsecond=0)
    if schedule.frequency == "hourly":
        return slot
    slot = slot.replace(hour=schedule.hour)
    if schedule.frequency == "weekly":
        slot -= timedelta(days=(slot.weekday() - schedule.weekday) % 7)
    if slot > now:
        slot -= timedelta(days=7 if schedule.frequency == "weekly" else 1)
    return slot


def is_due(schedule: BackupSchedule, now: datetime) -> bool:
    if not schedule.enabled:
        return False
    last = schedule.last_run_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return last is None or last < latest_slot(schedule, now)


async def run_due(platform: Platform) -> int:
    """Take the snapshots whose time has come, then apply retention."""
    now = datetime.now(UTC)
    taken = 0
    for schedule in await BackupSchedule.filter(enabled=True):
        if not is_due(schedule, now):
            continue
        snapshot = await take(
            platform, schedule.env, include=schedule.include or None, trigger="scheduled"
        )
        schedule.last_run_at = now
        schedule.last_status = snapshot.status
        await schedule.save(update_fields=["last_run_at", "last_status"])
        await prune(platform, schedule)
        taken += 1
    return taken
