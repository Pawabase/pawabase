"""Resource notifications reach only subscribers allowed to read the row; history is filtered the same way."""

import pytest

from pawabase_server.core.tokens import issue_user_token
from pawabase_server.realtime.connection import Connection
from pawabase_server.realtime.identity import identify

ENV = "/platform/v1/envs/development"

NOTES = {
    "name": "notes",
    "fields": [
        {"name": "text", "type": "string", "required": True},
        {"name": "secret", "type": "string", "write_only": True},
    ],
    "owner_field": "owner_id",
    "realtime": True,
    "operations": {
        "list": {"enabled": True, "policy": "owner"},
        "get": {"enabled": True, "policy": "owner"},
        "create": {"enabled": True, "policy": "authenticated"},
        "update": {"enabled": True, "policy": "owner"},
    },
}


class Peer:
    def __init__(self, name):
        self.id = name
        self.received = []

    def offer(self, envelope):
        self.received.append(envelope.payload)
        return True


def subscriber(api, user_id):
    token = issue_user_token(
        api.settings.jwt_master_secret,
        env="development",
        user_id=user_id,
        jti=f"j{user_id}",
        session_id="s",
    )
    identity = identify(token, api.settings.jwt_master_secret, "development")
    connection = Connection(
        peer=Peer(user_id),
        env="development",
        user_id=user_id,
        role="authenticated",
        auth=identity.auth,
        credential={"kind": "publishable"},
        channels={"resource:notes": {}},
    )
    api.platform.realtime.connections[connection.id] = connection
    return connection


@pytest.fixture
async def notes(api):
    await api.studio.post(f"{ENV}/resources", json=NOTES)
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    return api


def records(connection):
    return [m["payload"]["record"] for m in connection.peer.received]


async def test_each_subscriber_sees_only_rows_it_may_read(notes):
    api = notes
    ada, bob = subscriber(api, "1"), subscriber(api, "2")
    created = await api.http.post(
        "/rest/v1/notes",
        json={"text": "ada's", "secret": "s1"},
        headers=api.user_headers("development", "1"),
    )
    assert created.status_code == 201
    await api.http.post(
        "/rest/v1/notes", json={"text": "bob's"}, headers=api.user_headers("development", "2")
    )

    assert [r["text"] for r in records(ada)] == ["ada's"]
    assert [r["text"] for r in records(bob)] == ["bob's"]
    assert all("secret" not in r for r in records(ada))  # write-only fields never go out

    history = await api.platform.realtime.history(
        "development", "resource:notes", auth=bob.auth, credential=bob.credential
    )
    assert [m["payload"]["record"]["text"] for m in history] == ["bob's"]
    # Without an identity there is no history for a resource channel at all.
    assert await api.platform.realtime.history("development", "resource:notes") == []


async def test_history_does_not_outlive_lost_access(notes):
    api = notes
    ada = subscriber(api, "1")
    note = (
        await api.http.post(
            "/rest/v1/notes", json={"text": "mine"}, headers=api.user_headers("development", "1")
        )
    ).json()
    # The row is handed to someone else: Ada's earlier notification must not be replayed to her.
    store = await (await api.platform.state("development")).store("notes")
    await store.update(note["id"], {"owner_id": "2"})
    history = await api.platform.realtime.history(
        "development", "resource:notes", auth=ada.auth, credential=ada.credential
    )
    assert history == []


async def test_clients_cannot_publish_on_resource_channels(notes):
    api = notes
    ada = subscriber(api, "1")
    with pytest.raises(PermissionError):
        await api.platform.realtime.authorize(
            "development", "resource:notes", "publish", auth=ada.auth, credential=ada.credential
        )

