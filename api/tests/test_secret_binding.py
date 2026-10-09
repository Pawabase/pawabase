"""Secrets are sealed for their environment, and everything that copies one re-seals it."""

import pytest
from cryptography.fernet import InvalidToken

from database.models import Environment, InboundHook, Secret, WebhookEndpoint

ENV = "/platform/v1/envs/development"


async def ciphertext(model, **where):
    return (await model.filter(**where).first()).__dict__


async def test_a_stored_secret_is_sealed_for_its_environment(api):
    await api.studio.put(f"{ENV}/secrets/API_TOKEN", json={"value": "tok_123"})
    row = await Secret.get(name="API_TOKEN")
    assert api.platform.box.is_bound(row.ciphertext)
    with pytest.raises(InvalidToken):
        api.platform.box.open(row.ciphertext, "staging")
    api.platform.envs.forget("development")
    state = await api.platform.state("development")
    assert state.secret_values["API_TOKEN"] == "tok_123"


async def test_a_secret_sealed_before_environments_had_keys_still_loads(api):
    environment = await Environment.get(name="development")
    await Secret.create(
        environment=environment, name="OLD_STYLE", ciphertext=api.platform.box.seal("from-before")
    )
    api.platform.envs.forget("development")
    state = await api.platform.state("development")
    assert state.secret_values["OLD_STYLE"] == "from-before"


async def test_a_ciphertext_copied_into_another_environment_is_not_read(api):
    """Moving a row between environments, the mistake being guarded against."""
    await api.studio.post("/platform/v1/envs", json={"name": "staging"})
    await api.studio.put(f"{ENV}/secrets/API_TOKEN", json={"value": "tok_123"})
    staging = await Environment.get(name="staging")
    row = await Secret.get(name="API_TOKEN")
    await Secret.create(environment=staging, name="API_TOKEN", ciphertext=row.ciphertext)
    api.platform.envs.forget("staging")
    state = await api.platform.state("staging")
    assert "API_TOKEN" not in state.secret_values  # refused, not decrypted with the wrong key


async def test_promoting_a_webhook_reseals_its_signing_secret_for_the_target(api):
    await api.studio.post("/platform/v1/envs", json={"name": "staging"})
    created = await api.studio.post(
        f"{ENV}/webhooks",
        json={"name": "orders", "url": "https://hooks.example/orders", "events": ["order.created"]},
    )
    signing = created["secret"]
    await api.studio.post(f"{ENV}/promote", json={"to": "staging", "include": ["webhooks"]})
    box = api.platform.box
    development = await WebhookEndpoint.get(name="orders", environment__name="development")
    staging = await WebhookEndpoint.get(name="orders", environment__name="staging")
    assert box.open(development.secret_ciphertext, "development") == signing
    assert (
        box.open(staging.secret_ciphertext, "staging") == signing
    )  # the same secret, sealed for staging
    assert staging.secret_ciphertext != development.secret_ciphertext
    with pytest.raises(InvalidToken):
        box.open(staging.secret_ciphertext, "development")


async def test_cloning_an_environment_reseals_inbound_hook_secrets(api):
    await api.studio.post(
        f"{ENV}/inbound-hooks",
        json={
            "slug": "stripe",
            "verification": "hmac-sha256",
            "target_type": "event",
            "target": "stripe.event",
        },
    )
    await api.studio.post("/platform/v1/envs", json={"name": "clone", "copy_from": "development"})
    copied = await InboundHook.get(slug="stripe", environment__name="clone")
    assert api.platform.box.open(copied.secret_ciphertext, "clone")


async def test_a_legacy_webhook_secret_is_carried_over_when_promoted(api):
    await api.studio.post("/platform/v1/envs", json={"name": "staging"})
    environment = await Environment.get(name="development")
    await WebhookEndpoint.create(
        environment=environment,
        name="old",
        url="https://hooks.example/old",
        events=["x"],
        secret_ciphertext=api.platform.box.seal(
            "whsec_legacy"
        ),  # sealed before environments had keys
    )
    await api.studio.post(f"{ENV}/promote", json={"to": "staging", "include": ["webhooks"]})
    staging = await WebhookEndpoint.get(name="old", environment__name="staging")
    assert api.platform.box.open(staging.secret_ciphertext, "staging") == "whsec_legacy"


async def test_reseal_binds_legacy_values_and_is_safe_to_repeat(api):
    from app.reseal import reseal

    environment = await Environment.get(name="development")
    legacy = api.platform.box.seal("from-before")
    await Secret.create(environment=environment, name="OLD", ciphertext=legacy)
    await WebhookEndpoint.create(
        environment=environment,
        name="w",
        url="https://h.example/w",
        events=["x"],
        secret_ciphertext=api.platform.box.seal("whsec_old"),
    )
    await Secret.create(environment=environment, name="UNREADABLE", ciphertext="not-a-ciphertext")

    preview = await reseal(api.platform.box, dry_run=True)
    assert preview["Secret"]["sealed"] == 1 and preview["Secret"]["unreadable"] == 1
    assert (await Secret.get(name="OLD")).ciphertext == legacy  # a dry run changes nothing

    done = await reseal(api.platform.box)
    assert done["Secret"]["sealed"] == 1 and done["WebhookEndpoint"]["sealed"] == 1
    assert (
        api.platform.box.open((await Secret.get(name="OLD")).ciphertext, "development")
        == "from-before"
    )
    again = await reseal(api.platform.box)
    assert again["Secret"]["sealed"] == 0 and again["Secret"]["already_bound"] >= 1
    assert (await Secret.get(name="UNREADABLE")).ciphertext == "not-a-ciphertext"
