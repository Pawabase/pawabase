"""Durable delivery: events through the job queue, retries scheduled in the queue, webhooks on a long schedule."""

import json
import time

import fakeredis.aioredis
import pytest
from sillo.work.queue.connection import RedisConnection

from pawabase_server.automation import webhooks
from pawabase_server.automation.event_processor import EventProcessor, EventRoutingFailed
from pawabase_server.automation.jobs.base import ATTEMPT_KEY, RECORD_KEY, PawabaseJob
from pawabase_server.automation.jobs.webhooks import RETRY_SCHEDULE, DeliverWebhookJob
from pawabase_server.core.events import PlatformEvent
from pawabase_server.db.models import EventLog, JobRun, WebhookDelivery, WebhookEndpoint

ENV = "/platform/v1/envs/development"


class Flaky(PawabaseJob):
    """Fails until it has been attempted ``succeed_on`` times."""

    queue = "default"
    tries = 3
    backoff = 30

    async def perform(self):
        if self.attempt < self.params["succeed_on"]:
            raise RuntimeError(f"attempt {self.attempt} failed")
        return {"ok": self.attempt}


def capture_dispatch(api, monkeypatch):
    sent = []

    async def dispatch(job, *, env, queue=None, delay=0, target="", source="api", **kwargs):
        sent.append({"job": job, "env": env, "delay": delay, **kwargs})
        return f"queued-{len(sent)}"

    monkeypatch.setattr(api.platform, "dispatch", dispatch)
    return sent


async def run(job_class, record_id, **params):
    job = job_class("development", **params)
    job._job_id = record_id
    await job.fire()
    return job


async def test_a_failed_attempt_is_rescheduled_in_the_queue_not_slept_on(api, monkeypatch):
    await JobRun.create(
        id="rec-1", env="development", queue="default", job="Flaky", status="queued"
    )
    sent = capture_dispatch(api, monkeypatch)
    started = time.monotonic()
    await run(Flaky, "rec-1", succeed_on=3)
    assert time.monotonic() - started < 1  # no 30-second sleep inside the worker
    assert len(sent) == 1
    retry = sent[0]
    assert retry["job"] is Flaky and retry[ATTEMPT_KEY] == 2 and retry[RECORD_KEY] == "rec-1"
    assert 24 <= retry["delay"] <= 36  # 30 s backoff with ±20% jitter
    record = await JobRun.get(id="rec-1")
    assert record.status == "retrying" and "attempt 1 failed" in record.error
    assert record.available_at is not None

    # The second attempt carries the record id: it reports to the same row, and fails into a third.
    job = Flaky(
        "development", **{k: v for k, v in retry.items() if k not in ("job", "env", "delay")}
    )
    job._job_id = "queue-id-2"
    await job.fire()
    assert sent[-1][ATTEMPT_KEY] == 3 and 48 <= sent[-1]["delay"] <= 72
    assert (await JobRun.get(id="rec-1")).attempts == 2
    assert not await JobRun.filter(id="queue-id-2").exists()

    # The third succeeds.
    job = Flaky(
        "development", **{k: v for k, v in sent[-1].items() if k not in ("job", "env", "delay")}
    )
    job._job_id = "queue-id-3"
    await job.fire()
    record = await JobRun.get(id="rec-1")
    assert record.status == "completed" and record.attempts == 3


async def test_the_last_attempt_settles_as_a_failed_job(api, monkeypatch):
    await JobRun.create(
        id="rec-2", env="development", queue="default", job="Flaky", status="queued"
    )
    sent = capture_dispatch(api, monkeypatch)
    await run(Flaky, "rec-2", succeed_on=99, **{ATTEMPT_KEY: 3, RECORD_KEY: "rec-2"})
    assert sent == []
    assert (await JobRun.get(id="rec-2")).status == "failed"


async def test_an_interrupted_event_resumes_without_serving_a_consumer_twice(api, monkeypatch):
    await api.studio.post(
        f"{ENV}/subscriptions",
        json={"name": "first", "event": "order.*", "target_type": "flow", "target": "a"},
    )
    await api.studio.post(
        f"{ENV}/subscriptions",
        json={"name": "second", "event": "order.*", "target_type": "flow", "target": "b"},
    )
    calls = []
    real = api.platform.dispatch
    broken = {"b": True}

    async def dispatch(job, **kwargs):
        calls.append(kwargs.get("target"))
        if kwargs.get("target") == "b" and broken["b"]:
            raise ConnectionError("queue unavailable")
        return await real(job, **kwargs)

    monkeypatch.setattr(api.platform, "dispatch", dispatch)
    event = PlatformEvent(name="order.paid", env="development", payload={"id": 1})
    processor = EventProcessor(api.platform)
    with pytest.raises(EventRoutingFailed):
        await processor.handle(event, strict=True)
    log = await EventLog.get(event_id=event.id)
    assert log.routed_at is None
    assert [c["status"] for c in log.consumers] == ["queued", "failed"]

    broken["b"] = False
    calls.clear()
    await processor.handle(event, strict=True)
    assert calls == ["b"]  # the first flow was served by the first attempt
    log = await EventLog.get(event_id=event.id)
    assert log.routed_at is not None and [c["status"] for c in log.consumers] == [
        "queued",
        "queued",
    ]

    calls.clear()
    # A duplicate delivery of a routed event does nothing.
    await processor.handle(event, strict=True)
    assert calls == []


async def test_events_are_published_as_durable_jobs(api):
    state = await api.platform.state("development")
    event_id = await api.platform.emit(state, "thing.happened", {"n": 1})
    record = (
        await JobRun.filter(env="development", job="ProcessEventJob")
        .order_by("-created_at")
        .first()
    )
    assert record is not None and record.queue == "events" and record.target == "thing.happened"
    await api.drain()
    log = await EventLog.get(event_id=event_id)
    assert log.routed_at is not None


async def make_endpoint(api, attempts):
    await api.studio.post(
        f"{ENV}/webhooks",
        json={
            "name": "receiver",
            "url": "http://127.0.0.1:9/hook",
            "events": ["order.*"],
            "max_attempts": attempts,
        },
    )


async def test_webhook_retries_follow_the_endpoint_and_a_long_schedule(api, monkeypatch):
    await make_endpoint(api, attempts=7)
    sent = capture_dispatch(api, monkeypatch)
    state = await api.platform.state("development")
    assert (
        await webhooks.queue_deliveries(
            api.platform, state, event_id="ev1", event="order.paid", payload={"id": 1}
        )
        == 1
    )
    # A retried event does not create a second delivery for the same endpoint.
    assert (
        await webhooks.queue_deliveries(
            api.platform, state, event_id="ev1", event="order.paid", payload={"id": 1}
        )
        == 0
    )
    queued = sent[0]
    assert queued["max_attempts"] == 7
    delivery = await WebhookDelivery.get(id=queued["delivery_id"])

    job = DeliverWebhookJob("development", delivery_id=delivery.id, max_attempts=7)
    job._job_id = "w-1"
    await job.fire()  # nothing listens on port 9: the attempt fails
    retry = sent[-1]
    assert retry[ATTEMPT_KEY] == 2 and 8 <= retry["delay"] <= 12
    delivery = await WebhookDelivery.get(id=delivery.id)
    assert (
        delivery.status == "retrying"
        and delivery.next_attempt_at is not None
        and delivery.attempts == 1
    )

    for n, base in enumerate(RETRY_SCHEDULE, start=1):
        assert 0.8 * base <= DeliverWebhookJob("e", delivery_id="x").retry_delay(n) <= 1.2 * base
    final = DeliverWebhookJob(
        "development",
        delivery_id=delivery.id,
        max_attempts=7,
        **{ATTEMPT_KEY: 7, RECORD_KEY: "w-1"},
    )
    final._job_id = "w-7"
    await final.fire()
    delivery = await WebhookDelivery.get(id=delivery.id)
    assert delivery.status == "failed" and delivery.next_attempt_at is None


async def test_failed_deliveries_can_be_redelivered_in_bulk(api):
    await make_endpoint(api, attempts=1)
    endpoint = await WebhookEndpoint.get(name="receiver")
    for n in range(3):
        await WebhookDelivery.create(
            endpoint=endpoint, event_id=f"e{n}", event="order.paid", payload={}, status="failed"
        )
    await WebhookDelivery.create(
        endpoint=endpoint, event_id="ok", event="order.paid", payload={}, status="delivered"
    )
    dry = await api.studio.post(f"{ENV}/webhook-deliveries/redeliver", json={"dry_run": True})
    assert dry == {"matched": 3, "queued": 0, "dry_run": True}
    done = await api.studio.post(
        f"{ENV}/webhook-deliveries/redeliver", json={"endpoint": "receiver"}
    )
    assert done["matched"] == 3 and done["queued"] == 3 and len(done["deliveries"]) == 3


async def test_a_job_held_by_a_worker_that_died_is_taken_again():
    """The events queue's crash recovery: a job popped and never acknowledged returns after its visibility timeout."""
    connection = RedisConnection(prefix="t:", visibility_timeout=0.2)
    connection._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await connection.push("events", json.dumps({"job": "x"}))
    first = await connection.pop("events")
    assert first is not None
    assert await connection.pop("events") is None  # held by the (dead) worker
    time.sleep(0.3)
    again = await connection.pop("events")
    assert again is not None and again[0] == first[0]
    await connection.ack("events", again[0])
    time.sleep(0.3)
    assert await connection.pop("events") is None
