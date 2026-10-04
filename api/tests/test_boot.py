import pytest

from pawabase_core.clients import ServiceError


async def test_boot_into_a_single_runtime_with_its_default_environment(api):
    assert (await api.http.get("/health")).status_code == 200
    # The management plane is not for anonymous callers.
    assert (await api.http.get("/platform/v1/runtime")).status_code == 401
    runtime = await api.studio.get("/platform/v1/runtime")
    assert runtime["name"] == "Pawabase"
    # A fresh runtime is usable at once: one default environment, no project to create first.
    assert [(e["name"], e["is_default"]) for e in runtime["environments"]] == [("development", True)]
    # There is no project or organization to manage.
    for gone in ("/platform/v1/projects", "/platform/v1/orgs"):
        assert (await api.http.get(gone)).status_code in (401, 404)
        with pytest.raises(ServiceError) as missing:
            await api.studio.get(gone)
        assert missing.value.status == 404
    policy = await api.studio.post(
        "/platform/v1/envs/development/policies",
        json={"name": "editors", "condition": {"role": "editor"}},
    )
    assert policy["name"] == "editors"


async def test_updates_of_existing_rows(api):
    """Heartbeats after a restart, replacing a secret and promoting twice all
    update rows that already exist, the path Tortoise's update_or_create
    breaks on with Sillo models."""
    from datetime import UTC, datetime

    from database.models import Secret, WorkerHeartbeat
    from pawabase_core.records import upsert

    for processed in (1, 2):
        await upsert(WorkerHeartbeat, name="w1", defaults={"kind": "worker", "queues": [], "status": "running", "started_at": datetime.now(UTC), "last_seen": datetime.now(UTC), "processed": processed, "concurrency": 1})
    assert (await WorkerHeartbeat.get(name="w1")).processed == 2

    await api.studio.post("/platform/v1/envs", json={"name": "dev"})
    await api.studio.post("/platform/v1/envs", json={"name": "prod"})
    env = "/platform/v1/envs/dev"
    await api.studio.put(f"{env}/secrets/TOKEN", json={"value": "one"})
    await api.studio.put(f"{env}/secrets/TOKEN", json={"value": "two", "description": "rotated"})
    assert await Secret.filter(name="TOKEN").count() == 1
    assert (await Secret.get(name="TOKEN")).description == "rotated"

    await api.studio.post(f"{env}/policies", json={"name": "p1", "condition": {"authenticated": True}})
    await api.studio.post(f"{env}/promote", json={"to": "prod"})
    await api.studio.put(f"{env}/policies/p1", json={"name": "p1", "condition": True, "description": "open"})
    await api.studio.post(f"{env}/promote", json={"to": "prod"})
    promoted = await api.studio.get("/platform/v1/envs/prod/policies/p1")
    assert promoted["description"] == "open"


async def test_deploy_preview_is_an_expiring_isolated_environment(api):
    source = "/platform/v1/envs/development"
    await api.studio.post(
        f"{source}/policies", json={"name": "public", "condition": True}
    )
    preview = await api.studio.post(
        f"{source}/previews",
        json={"ref": "123", "expires_in_hours": 24, "infra": {"database_url": "sqlite://preview.db"}},
    )
    assert preview["name"] == "pr-123"
    assert preview["preview_source"] == "development"
    assert preview["preview_expires_at"] is not None
    assert preview["infra"] == {"database_url": "sqlite://preview.db"}
    assert set(preview["keys"]) == {"publishable", "secret"}
    policies = await api.studio.get(
        "/platform/v1/envs/pr-123/policies"
    )
    assert policies["data"][0]["name"] == "public"


async def test_api_key_restrictions_are_resolved_for_the_gateway(api, settings):
    from pawabase_core.clients import ServiceClient

    key = await api.studio.post(
        "/platform/v1/envs/development/keys",
        json={
            "name": "CI deployer",
            "role": "secret",
            "allowed_ips": ["10.0.0.0/8"],
            "allowed_routes": ["POST /functions/v1/*", "GET /rest/v1/*"],
        },
    )
    service = ServiceClient(
        "http://x", secret=settings.internal_secret, issuer="gateway", audience="api", app=api.app
    )
    try:
        resolved = await service.post("/internal/v1/keys/resolve", json={"key": key["key"]})
    finally:
        await service.close()
    assert resolved["allowed_ips"] == ["10.0.0.0/8"]
    assert resolved["allowed_routes"] == ["POST /functions/v1/*", "GET /rest/v1/*"]
