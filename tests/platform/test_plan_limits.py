"""A project's plan limits are enforced where things are made and used (projects/quotas.py)."""

import pytest

from pawabase_server.backup import snapshots
from pawabase_server.core.clients import ServiceError
from pawabase_server.db.models import BackupSnapshot, Environment, SessionInfo, UsageCounter
from pawabase_server.projects import quotas
from pawabase_server.projects.manager import ProjectManager
from tests.platform.test_realtime_rows import subscriber
from tests.platform.test_sandbox import archive

ENV = "/platform/v1/envs/development"
PASSWORD = "Correct-horse-9"

CODE = """
from pawabase_server.functions.registry import function

@function("pl.ping", policy="public")
async def ping(ctx):
    return {"pong": True}
"""

FLOW = {
    "name": "noop",
    "definition": {"nodes": [{"id": "done", "type": "flow.return", "config": {"value": 1}}]},
}


async def plan(api, **limits):
    environment = await Environment.get(name="development")
    manager = ProjectManager(api.settings, api.platform.box)
    await manager.update(environment.project_id, limits=limits)
    return environment.project_id


def refused(error: ServiceError, limit: str) -> None:
    assert error.status == 403, error
    assert "quota_exceeded" in str(error.body) and limit in str(error.body), error.body


async def test_api_keys_past_the_plan_are_refused(api):
    used = await quotas.COUNTERS["max_api_keys"](
        (await Environment.get(name="development")).project_id
    )
    await plan(api, max_api_keys=used + 1)
    await api.studio.post(f"{ENV}/keys", json={"name": "one more", "role": "secret"})
    with pytest.raises(ServiceError) as error:
        await api.studio.post(f"{ENV}/keys", json={"name": "too many", "role": "secret"})
    refused(error.value, "max_api_keys")


async def test_webhooks_and_schedules_past_the_plan_are_refused(api):
    await plan(api, max_webhooks=1, max_scheduled_jobs=1)
    hook = {"url": "https://example.com/hook", "events": ["*"]}
    await api.studio.post(f"{ENV}/webhooks", json={"name": "a", **hook})
    with pytest.raises(ServiceError) as error:
        await api.studio.post(f"{ENV}/webhooks", json={"name": "b", **hook})
    refused(error.value, "max_webhooks")
    schedule = {"cron": "0 * * * *", "target_type": "event", "target": "tick"}
    await api.studio.post(f"{ENV}/schedules", json={"name": "a", **schedule})
    with pytest.raises(ServiceError) as error:
        await api.studio.post(f"{ENV}/schedules", json={"name": "b", **schedule})
    refused(error.value, "max_scheduled_jobs")


async def test_function_invocations_are_metered_per_month(api):
    project = await plan(api, max_function_invocations_per_month=2)
    await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive(CODE)})
    for _ in range(2):
        body = await api.studio.post(f"{ENV}/functions/pl.ping/invoke", json={"input": {}})
        assert body["result"] == {"pong": True}, body
    body = await api.studio.post(f"{ENV}/functions/pl.ping/invoke", json={"input": {}})
    assert body["status"] == "failed" and body["code"] == "quota_exceeded", body
    counter = await UsageCounter.get(project_id=project, metric="function_invocations")
    assert counter.value == 2


async def test_a_new_month_counter_is_seeded_from_what_already_ran(api):
    await api.studio.post(f"{ENV}/function-deployments", json={"archive": archive(CODE)})
    await api.studio.post(
        f"{ENV}/functions/pl.ping/invoke", json={"input": {}}
    )  # unlimited: not metered
    project = await plan(api, max_function_invocations_per_month=5)
    await api.studio.post(f"{ENV}/functions/pl.ping/invoke", json={"input": {}})
    counter = await UsageCounter.get(project_id=project, metric="function_invocations")
    assert counter.value == 2  # the run before the limit, found in the run records, plus this one


async def test_stored_bytes_are_metered_and_capped(api):
    project = await plan(api, max_storage_bytes=100)
    await api.studio.post(f"{ENV}/buckets", json={"name": "files", "public": False})
    headers = api.context_headers("development", role="service")

    async def put(key, size):
        return await api.http.put(
            f"/storage/v1/object/files/{key}", content=b"x" * size, headers=headers
        )

    assert (await put("a.txt", 60)).status_code == 201
    over = await put("b.txt", 60)
    assert over.status_code == 403 and over.json()["error"] == "quota_exceeded", over.text
    assert (await put("a.txt", 90)).status_code == 201  # a replacement counts the difference
    gauge = await UsageCounter.get(project_id=project, metric="storage_bytes")
    assert gauge.value == 90
    deleted = await api.http.delete("/storage/v1/object/files/a.txt", headers=headers)
    assert deleted.status_code in (200, 204)
    assert (await UsageCounter.get(project_id=project, metric="storage_bytes")).value == 0
    assert (await put("b.txt", 60)).status_code == 201
    assert await quotas.recount_storage(api.platform, project) == 60


async def test_uploads_larger_than_the_plan_allows_are_refused(api):
    await plan(api, max_upload_bytes=10)
    await api.studio.post(f"{ENV}/buckets", json={"name": "files", "public": False})
    headers = api.context_headers("development", role="service")
    big = await api.http.put("/storage/v1/object/files/big.bin", content=b"x" * 11, headers=headers)
    assert big.status_code in (400, 413), big.text


async def test_users_monthly_users_and_sessions_follow_the_plan(api):
    await plan(api, max_active_users=2, max_users_per_month=1, max_auth_sessions_per_user=1)
    headers = api.context_headers("development")

    async def signup(email):
        return await api.http.post(
            "/auth/v1/signup", json={"email": email, "password": PASSWORD}, headers=headers
        )

    async def signin(email):
        return await api.http.post(
            "/auth/v1/token?grant_type=password",
            json={"email": email, "password": PASSWORD},
            headers=headers,
        )

    assert (await signup("ada@x.io")).status_code == 201  # Ada is this month's one active user
    # Bob may have an account, but not a session: the month's active users are used up.
    bob = await signup("bob@x.io")
    assert bob.status_code == 403 and bob.json()["error"] == "quota_exceeded", bob.text
    assert (await signup("cy@x.io")).status_code == 403  # two accounts exist: the user limit
    first = await signin("ada@x.io")
    assert first.status_code == 200  # Ada again is free
    assert await SessionInfo.filter(revoked_at=None, user__email="ada@x.io").count() == 1


async def test_backups_past_the_plan_are_refused_or_rotated(api):
    await plan(api, max_backups=1)
    first = await snapshots.take(api.platform, "development")
    with pytest.raises(quotas.QuotaExceeded):
        await snapshots.take(api.platform, "development")
    scheduled = await snapshots.take(api.platform, "development", trigger="scheduled")
    remaining = await BackupSnapshot.filter(env="development").values_list("id", flat=True)
    assert remaining == [scheduled.id] and first.id not in remaining


async def test_sign_in_providers_past_the_plan_are_refused(api):
    await plan(api, max_auth_providers=1)
    one = {"google": {"client_id": "a", "client_secret": "b"}}
    await api.studio.patch(ENV, json={"auth": {"providers": one}})
    two = {**one, "github": {"client_id": "c", "client_secret": "d"}}
    with pytest.raises(ServiceError) as error:
        await api.studio.patch(ENV, json={"auth": {"providers": two}})
    refused(error.value, "max_auth_providers")


async def test_writes_stop_when_the_database_is_over_the_plan(api):
    await api.studio.post(
        f"{ENV}/resources",
        json={
            "name": "notes",
            "fields": [{"name": "text", "type": "string"}],
            "operations": {"create": {"enabled": True, "policy": "public"}},
        },
    )
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    await plan(api, max_database_bytes=1)
    quotas.forget_database_size("development")
    created = await api.http.post(
        "/rest/v1/notes", json={"text": "x"}, headers=api.context_headers("development")
    )
    assert created.status_code == 403 and created.json()["error"] == "quota_exceeded", created.text
    # The console can still remove data to get back under the limit.
    await api.studio.post(
        f"{ENV}/database/query", json={"sql": "DELETE FROM notes", "allow_write": True}
    )
    with pytest.raises(ServiceError):
        await api.studio.post(
            f"{ENV}/database/query",
            json={"sql": "INSERT INTO notes (id, text) VALUES ('x', 'y')", "allow_write": True},
        )
    quotas.forget_database_size("development")


async def test_environments_past_the_plan_are_refused(api):
    used = await Environment.all().count()
    await plan(api, max_environments=used)
    with pytest.raises(ServiceError) as error:
        await api.studio.post("/platform/v1/envs", json={"name": "staging"})
    refused(error.value, "max_environments")


def test_lease_sizes_keep_small_plans_exact():
    assert quotas.UsageMeter.lease_size(100) == 1
    assert quotas.UsageMeter.lease_size(1000) == 5
    assert quotas.UsageMeter.lease_size(1_000_000) == 16


async def test_realtime_channels_in_use_follow_the_plan(api):
    await plan(api, max_realtime_channels=1)
    realtime = api.platform.realtime
    ada, bob = subscriber(api, "1"), subscriber(api, "2")
    ada.channels.clear()
    bob.channels.clear()
    ada.channels["public:lobby"] = {}
    await realtime._check_channel_quota(bob, "public:lobby")  # already open in the project: free
    with pytest.raises(PermissionError, match="realtime channels"):
        await realtime._check_channel_quota(bob, "public:other")
    ada.channels.clear()
    await realtime._check_channel_quota(bob, "public:other")


async def test_plan_usage_reports_each_limit(api):
    await plan(api, max_webhooks=3, max_function_invocations_per_month=100, daily_request_limit=10)
    await api.studio.post(
        f"{ENV}/webhooks", json={"name": "a", "url": "https://example.com/h", "events": ["*"]}
    )
    report = {row["limit"]: row for row in (await api.studio.get(f"{ENV}/plan-usage"))["data"]}
    assert report["max_webhooks"]["used"] == 1 and report["max_webhooks"]["percent"] == 33.3
    assert report["max_function_invocations_per_month"]["used"] == 0
    assert "daily_request_limit" not in report  # the gateway reports request limits
    policies = (await api.studio.get("/platform/v1/retry-policies"))["data"]
    webhook = next(p for p in policies if p["job"] == "DeliverWebhookJob")
    assert webhook["max_attempts"] == 5 and webhook["waits_seconds"][:2] == [10.0, 60.0]
