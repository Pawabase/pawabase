"""Creating a scoped API key through the management API."""

import pytest

from pawabase_server.core.clients import ServiceError

ENV = "/platform/v1/envs/development"


async def test_creating_keys_validates_scopes_and_lists_the_catalog(api):
    with pytest.raises(ServiceError) as error:
        await api.studio.post(f"{ENV}/keys", json={"name": "x", "role": "secret", "scopes": ["nope:read"]})
    assert error.value.status == 422
    made = await api.studio.post(
        f"{ENV}/keys", json={"name": "users", "role": "secret", "scopes": ["users:read"]}
    )
    assert made["scopes"] == ["users:read"]
    catalog = await api.studio.get("/platform/v1/key-scopes")
    assert {f["id"] for f in catalog["families"]} >= {"data", "users", "webhooks", "admin"}
