"""Queueing recorded jobs again, one at a time or by filter."""

from database.models import JobRun

ENV = "/platform/v1/envs/development"


async def _failed(job_id, *, job="SendMailJob", payload=None, status="failed", queue="mail"):
    await JobRun.create(
        id=job_id,
        env="development",
        queue=queue,
        job=job,
        status=status,
        payload=payload if payload is not None else {"to": ["a@example.com"], "subject": "hi"},
        error="boom" if status == "failed" else None,
    )


async def test_failed_jobs_are_replayed_by_filter_and_the_rest_are_reported(api):
    await _failed("j1")
    await _failed("j2")
    await _failed("j3", payload={"truncated": True})
    await _failed("j4", status="succeeded")

    look = await api.studio.post(f"{ENV}/jobs/replay", json={"dry_run": True})
    assert (look["matched"], look["queued"], len(look["skipped"])) == (3, 2, 1)
    assert look["skipped"][0]["job"] == "j3"

    done = await api.studio.post(f"{ENV}/jobs/replay", json={})
    assert done["queued"] == 2 and done["dry_run"] is False
    await api.drain()
    replayed = await JobRun.filter(env="development", source="replay").count()
    assert replayed == 2


async def test_named_jobs_replay_whatever_their_status(api):
    await _failed("ok1", status="succeeded")
    done = await api.studio.post(f"{ENV}/jobs/replay", json={"ids": ["ok1"]})
    assert done["queued"] == 1


async def test_a_dead_letter_can_be_dismissed(api):
    await _failed("dead")
    await api.studio.delete(f"{ENV}/failed-jobs/dead")
