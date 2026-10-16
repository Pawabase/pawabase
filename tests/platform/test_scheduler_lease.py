"""One scheduler leads; schedules can skip an overlapping run and catch up a missed slot."""

from datetime import UTC, datetime, timedelta

from pawabase_server.automation.scheduler import PlatformScheduler
from pawabase_server.core import leases
from pawabase_server.db.models import Environment, JobRun, Schedule


async def test_one_holder_at_a_time_and_takeover_after_expiry(api):
    assert await leases.acquire("x", "a", 30)
    assert not await leases.acquire("x", "b", 30)
    assert await leases.acquire("x", "a", 30)  # renewing
    await leases.release("x", "a")
    assert await leases.acquire("x", "b", 30)
    assert await leases.holder_of("x") == "b"


async def schedule(api, **fields):
    environment = await Environment.get(name="development")
    return await Schedule.create(
        environment=environment,
        name="tick",
        cron="* * * * *",
        target_type="function",
        target="f",
        **fields,
    )


async def test_a_follower_does_not_fire(api, monkeypatch):
    sent = []

    async def dispatch(*args, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(api.platform, "dispatch", dispatch)
    row = await schedule(api)
    leader, follower = PlatformScheduler(api.platform), PlatformScheduler(api.platform)
    leader._name, follower._name = "leader", "follower"
    assert await leader.lead() and not await follower.lead()
    spec = next(s for s in (await leader.desired()).values() if s.get("id") == row.id)
    await follower.fire(spec)
    assert sent == []
    await leader.fire(spec)
    assert len(sent) == 1


async def test_overlap_skip_and_catch_up(api, monkeypatch):
    sent = []

    async def dispatch(*args, **kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(api.platform, "dispatch", dispatch)
    row = await schedule(
        api, overlap="skip", catch_up=True, last_run_at=datetime.now(UTC) - timedelta(hours=1)
    )
    scheduler = PlatformScheduler(api.platform)
    await JobRun.create(
        id="busy",
        env="development",
        queue="functions",
        job="RunFunctionJob",
        source="schedule",
        target="f",
        status="active",
    )
    assert (
        await scheduler.lead()
    )  # becoming leader catches up the missed slot, but the last run is still going
    assert sent == [] and (await Schedule.get(id=row.id)).last_status == "skipped"
    await JobRun.filter(id="busy").update(status="succeeded")
    await Schedule.filter(id=row.id).update(last_run_at=datetime.now(UTC) - timedelta(hours=1))
    assert await scheduler.catch_up() == 1 and len(sent) == 1
