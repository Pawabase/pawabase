"""Saved backups: taking, checking, pinning, retention, scheduling and restoring."""

from datetime import UTC, datetime, timedelta

import pytest

from app import snapshots
from database.models import BackupSchedule, BackupSnapshot

ENV = "/platform/v1/envs/development"


async def _note_resource(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "notes",
            "fields": [{"name": "text", "type": "string", "required": True}],
            "operations": {"create": {"enabled": True, "policy": "public"}},
        },
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")


async def test_a_snapshot_is_stored_checked_and_downloadable(api):
    snap = await api.studio.post(
        f"{ENV}/snapshots", json={"name": "before launch", "include": ["definitions", "settings"]}
    )
    assert snap["status"] == "complete" and snap["size_bytes"] > 0 and len(snap["checksum"]) == 64
    assert snap["include"] == ["definitions", "settings"]

    listing = await api.studio.get(f"{ENV}/snapshots")
    assert [row["name"] for row in listing["data"]] == ["before launch"]
    assert listing["stored_bytes"] == snap["size_bytes"] and listing["schedule"]["enabled"] is False

    verdict = await api.studio.post(f"{ENV}/snapshots/{snap['id']}/verify")
    assert verdict["ok"] is True and verdict["parts"] == ["definitions", "settings"]
    document = await api.studio.get(f"{ENV}/snapshots/{snap['id']}/download")
    assert document["format"] == "pawabase.backup" and "definitions" in document

    pinned = await api.studio.patch(
        f"{ENV}/snapshots/{snap['id']}", json={"pinned": True, "note": "keep"}
    )
    assert pinned["pinned"] is True and pinned["note"] == "keep"


async def test_a_damaged_snapshot_is_caught_by_verify_and_refused_by_restore(api):
    snap = await api.studio.post(f"{ENV}/snapshots", json={"include": ["definitions"]})
    driver = await snapshots._driver(api.platform, "development")

    async def garbage():
        yield b"not what was stored"

    await driver.write(snap["storage_key"], garbage())
    verdict = await api.studio.post(f"{ENV}/snapshots/{snap['id']}/verify")
    assert verdict["ok"] is False and "checksum" in verdict["error"]
    with pytest.raises(Exception) as refused:
        await api.studio.post(f"{ENV}/snapshots/{snap['id']}/restore", json={})
    assert "checksum" in str(refused.value)


async def test_restore_takes_a_safety_snapshot_first_and_undoes_changes(api):
    await _note_resource(api)
    snap = await api.studio.post(f"{ENV}/snapshots", json={"include": ["definitions"]})
    await api.studio.delete(f"{ENV}/resources/notes")

    dry = await api.studio.post(f"{ENV}/snapshots/{snap['id']}/restore", json={"dry_run": True})
    assert dry["dry_run"] is True and dry["would_restore"] == ["definitions"]
    assert await BackupSnapshot.filter(trigger="before-restore").count() == 0

    report = await api.studio.post(f"{ENV}/snapshots/{snap['id']}/restore", json={})
    safety = await BackupSnapshot.get(id=report["safety_snapshot"])
    assert safety.trigger == "before-restore" and safety.status == "complete"
    names = [r["name"] for r in (await api.studio.get(f"{ENV}/resources"))["data"]]
    assert "notes" in names


async def test_replace_needs_the_environment_name(api):
    snap = await api.studio.post(f"{ENV}/snapshots", json={"include": ["settings"]})
    with pytest.raises(Exception) as refused:
        await api.studio.post(f"{ENV}/snapshots/{snap['id']}/restore", json={"strategy": "replace"})
    assert "confirm" in str(refused.value)


async def test_retention_removes_old_scheduled_snapshots_but_not_pinned_or_manual(api):
    schedule = await api.studio.put(
        f"{ENV}/backup-schedule",
        json={"enabled": True, "keep_last": 2, "keep_days": 0, "include": ["settings"]},
    )
    made = [
        await snapshots.take(api.platform, "development", include=["settings"], trigger="scheduled")
        for _ in range(4)
    ]
    manual = await snapshots.take(api.platform, "development", include=["settings"])
    await BackupSnapshot.filter(id=made[0].id).update(pinned=True)

    removed = await snapshots.prune(api.platform, await BackupSchedule.get(id=schedule["id"]))
    left = {row.id for row in await BackupSnapshot.all()}
    assert removed == 1 and made[0].id in left and manual.id in left
    assert made[1].id not in left and made[2].id in left and made[3].id in left


async def test_a_schedule_is_due_once_per_slot(api):
    schedule = BackupSchedule(env="development", enabled=True, frequency="daily", hour=3)
    day = datetime(2026, 10, 10, 14, 0, tzinfo=UTC)
    assert snapshots.latest_slot(schedule, day) == datetime(2026, 10, 10, 3, tzinfo=UTC)
    assert snapshots.latest_slot(schedule, day.replace(hour=2)) == datetime(
        2026, 10, 9, 3, tzinfo=UTC
    )
    assert snapshots.is_due(schedule, day)
    schedule.last_run_at = day - timedelta(hours=1)
    assert not snapshots.is_due(schedule, day)
    schedule.last_run_at = day - timedelta(days=1)
    assert snapshots.is_due(schedule, day)
    weekly = BackupSchedule(env="development", enabled=True, frequency="weekly", hour=3, weekday=0)
    assert snapshots.latest_slot(weekly, day).weekday() == 0
    hourly = BackupSchedule(env="development", enabled=True, frequency="hourly")
    assert snapshots.latest_slot(hourly, day) == day
    assert not snapshots.is_due(BackupSchedule(env="development", enabled=False), day)


async def test_run_due_takes_the_snapshot_and_records_it(api):
    await api.studio.put(
        f"{ENV}/backup-schedule",
        json={"enabled": True, "frequency": "hourly", "include": ["settings"]},
    )
    assert await snapshots.run_due(api.platform) == 1
    assert await snapshots.run_due(api.platform) == 0
    row = await BackupSnapshot.get(env="development")
    assert row.trigger == "scheduled" and row.status == "complete"
    assert (await BackupSchedule.get(env="development")).last_status == "complete"
