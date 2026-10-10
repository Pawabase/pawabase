"""Rotating secrets and API keys without an outage."""

ENV = "/platform/v1/envs/development"


async def test_a_secret_keeps_the_value_it_replaced(api):
    await api.studio.put(
        f"{ENV}/secrets/STRIPE_KEY", json={"value": "sk_one", "rotate_every_days": 30}
    )
    rows = {s["name"]: s for s in (await api.studio.get(f"{ENV}/secrets"))["data"]}
    assert rows["STRIPE_KEY"]["version"] == 1 and rows["STRIPE_KEY"]["has_previous"] is False
    assert rows["STRIPE_KEY"]["rotate_every_days"] == 30 and rows["STRIPE_KEY"]["due"] is False

    await api.studio.put(f"{ENV}/secrets/STRIPE_KEY", json={"value": "sk_two"})
    state = await api.platform.state("development")
    row = next(
        s for s in (await api.studio.get(f"{ENV}/secrets"))["data"] if s["name"] == "STRIPE_KEY"
    )
    assert row["version"] == 2 and row["has_previous"] is True and row["rotate_every_days"] == 30

    back = await api.studio.post(f"{ENV}/secrets/STRIPE_KEY/rollback")
    assert back["version"] == 3
    assert (await api.platform.state("development")).secret_values["STRIPE_KEY"] == "sk_one"
    del state


async def test_rotation_can_generate_a_value_and_shows_it_once(api):
    await api.studio.put(f"{ENV}/secrets/SIGNING", json={"value": "first"})
    made = await api.studio.post(f"{ENV}/secrets/SIGNING/rotate", json={})
    assert len(made["value"]) >= 32 and made["version"] == 2
    again = await api.studio.post(f"{ENV}/secrets/SIGNING/rotate", json={"value": "mine"})
    assert "value" not in again and again["version"] == 3
    assert (await api.platform.state("development")).secret_values["SIGNING"] == "mine"


async def test_the_rotation_reminder_comes_due(api):
    from datetime import UTC, datetime, timedelta

    from database.models import Secret

    await api.studio.put(f"{ENV}/secrets/OLD", json={"value": "x", "rotate_every_days": 7})
    await Secret.filter(name="OLD").update(rotated_at=datetime.now(UTC) - timedelta(days=9))
    row = next(s for s in (await api.studio.get(f"{ENV}/secrets"))["data"] if s["name"] == "OLD")
    assert row["due"] is True and row["age_days"] >= 9
    await api.studio.patch(f"{ENV}/secrets/OLD", json={"rotate_every_days": 0})
    row = next(s for s in (await api.studio.get(f"{ENV}/secrets"))["data"] if s["name"] == "OLD")
    assert row["due"] is False and row["rotate_every_days"] is None


async def test_an_api_key_is_replaced_and_the_old_one_ends_after_a_grace_period(api):
    made = await api.studio.post(f"{ENV}/keys", json={"name": "web", "role": "secret"})
    fresh = await api.studio.post(f"{ENV}/keys/{made['id']}/rotate", json={"grace_hours": 2})
    assert fresh["key"] != made["key"] and fresh["replaces"] == made["id"]
    keys = {k["id"]: k for k in (await api.studio.get(f"{ENV}/keys"))["data"]}
    assert keys[made["id"]]["expires_at"] is not None and keys[made["id"]]["active"] is True
    assert keys[fresh["id"]]["active"] is True and keys[fresh["id"]]["name"] == "web"
    now = await api.studio.post(f"{ENV}/keys/{fresh['id']}/rotate", json={"grace_hours": 0})
    old = {k["id"]: k for k in (await api.studio.get(f"{ENV}/keys"))["data"]}[fresh["id"]]
    assert old["active"] is False and now["replaces"] == fresh["id"]
