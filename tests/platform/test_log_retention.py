"""Each kind of log is kept for the days its project's plan allows, and for the platform's setting where the plan says nothing."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from pawabase_server.db.models import EventLog, LoginEvent
from pawabase_server.observability import retention
from tests.platform.test_plan_limits import plan


def ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def sign_in(env: str, days: int) -> str:
    event = await LoginEvent.create(env=env, kind="login")
    await LoginEvent.filter(id=event.id).update(created_at=ago(days))
    return event.id


async def test_a_plan_decides_how_long_sign_ins_are_kept(api):
    await plan(api, login_event_retention_days=7)
    old, fresh = await sign_in("development", 30), await sign_in("development", 2)
    elsewhere = await sign_in("an-environment-with-no-project", 30)

    removed = await retention.sweep(SimpleNamespace(login_event_retention_days=0))

    assert removed == {"login_event_retention_days": 1}
    assert not await LoginEvent.filter(id=old).exists()
    assert await LoginEvent.filter(id=fresh).exists()
    assert await LoginEvent.filter(id=elsewhere).exists()  # no plan, no setting: kept


async def test_the_platform_setting_covers_what_no_plan_does(api):
    await plan(api, login_event_retention_days=90)
    scoped = await sign_in("development", 30)  # within its plan's 90 days
    elsewhere = await sign_in("an-environment-with-no-project", 30)

    await retention.sweep(SimpleNamespace(login_event_retention_days=10))

    assert await LoginEvent.filter(id=scoped).exists()
    assert not await LoginEvent.filter(id=elsewhere).exists()


async def test_kinds_written_as_text_are_swept_too(api):
    await plan(api, event_log_retention_days=3)
    for name, days in (("old", 10), ("new", 1)):
        await EventLog.create(
            event_id=name,
            env="development",
            name="x.created",
            source="test",
            occurred_at=ago(days).isoformat(),
        )

    await retention.sweep(SimpleNamespace())

    assert [row.event_id async for row in EventLog.filter(env="development")] == ["new"]


def test_every_kind_is_a_limit_and_a_setting():
    from pawabase_server.projects.catalogue import BY_KEY
    from pawabase_server.settings import Settings

    for kind in retention.KINDS:
        assert BY_KEY[kind.name].kind == "retention"
        assert kind.name in Settings.model_fields or hasattr(Settings(), kind.name)
