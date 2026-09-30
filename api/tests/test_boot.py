async def test_boot_and_projects(api):
    assert (await api.http.get("/health")).status_code == 200
    assert (await api.http.get("/platform/v1/projects")).status_code == 401
    created = await api.studio.post("/platform/v1/projects", json={"ref": "acme", "name": "Acme"})
    assert set(created["keys"]) == {"development", "production"}
    listing = await api.studio.get("/platform/v1/projects")
    assert listing["data"][0]["ref"] == "acme"
    policy = await api.studio.post(
        "/platform/v1/projects/acme/envs/development/policies",
        json={"name": "editors", "condition": {"role": "editor"}},
    )
    assert policy["name"] == "editors"


async def test_updates_of_existing_rows(api):
    """Heartbeats after a restart, replacing a secret and promoting twice all
    update rows that already exist, the path Tortoise's update_or_create
    breaks on with Sillo models."""
    from datetime import UTC, datetime

    from database.models import Secret, WorkerHeartbeat
    from pawabase_kit.records import upsert

    for processed in (1, 2):
        await upsert(WorkerHeartbeat, name="w1", defaults={"kind": "worker", "queues": [], "status": "running", "started_at": datetime.now(UTC), "last_seen": datetime.now(UTC), "processed": processed, "concurrency": 1})
    assert (await WorkerHeartbeat.get(name="w1")).processed == 2

    await api.studio.post("/platform/v1/projects", json={"ref": "up", "name": "Up", "environments": ["dev", "prod"]})
    env = "/platform/v1/projects/up/envs/dev"
    await api.studio.put(f"{env}/secrets/TOKEN", json={"value": "one"})
    await api.studio.put(f"{env}/secrets/TOKEN", json={"value": "two", "description": "rotated"})
    assert await Secret.filter(name="TOKEN").count() == 1
    assert (await Secret.get(name="TOKEN")).description == "rotated"

    await api.studio.post(f"{env}/policies", json={"name": "p1", "condition": {"authenticated": True}})
    await api.studio.post(f"{env}/promote", json={"to": "prod"})
    await api.studio.put(f"{env}/policies/p1", json={"name": "p1", "condition": True, "description": "open"})
    await api.studio.post(f"{env}/promote", json={"to": "prod"})
    promoted = await api.studio.get("/platform/v1/projects/up/envs/prod/policies/p1")
    assert promoted["description"] == "open"
