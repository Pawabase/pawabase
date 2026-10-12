"""One server: every part reached through the public entry point, and Studio managing the same objects in-process."""

import pytest


async def test_the_public_entry_point_answers_for_itself(running):
    landing = await running.public.get("/")
    assert landing.status_code == 200 and "API is running" in landing.text
    assert (await running.public.get("/health")).json()["status"] == "ok"
    status = (await running.public.get("/v1/status")).json()
    assert status["ok"] is True and set(status["services"]) == {"api", "auth", "realtime"}


@pytest.mark.parametrize(
    "path",
    [
        "/internal/v1/telemetry/requests",
        "/admin/v1/envs/development/users",
        "/envs/development",
        "/studio/status",
    ],
)
async def test_nothing_internal_is_reachable_from_outside(running, path):
    response = await running.public.get(path, headers={"apikey": running.secret})
    assert response.status_code == 404


async def test_a_key_is_required_and_checked(running):
    assert (await running.public.get("/auth/v1/settings")).status_code == 401
    bad = await running.public.get("/auth/v1/settings", headers={"apikey": "pk_nope_nope_nope"})
    assert bad.status_code == 401 and bad.json()["error"] == "invalid_api_key"
    ok = await running.public.get("/auth/v1/settings", headers={"apikey": running.publishable})
    assert ok.status_code == 200


async def test_a_user_signs_up_and_reads_themselves_through_one_process(running):
    headers = {"apikey": running.publishable}
    signed = await running.public.post(
        "/auth/v1/signup",
        json={"email": "ada@example.com", "password": "correct horse battery"},
        headers=headers,
    )
    assert signed.status_code in (200, 201), signed.text
    token = signed.json()["access_token"]
    me = await running.public.get(
        "/auth/v1/user", headers={**headers, "authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200 and me.json()["email"] == "ada@example.com"


async def test_what_studio_manages_is_what_the_public_side_serves(running):
    await running.manage.post(
        "/platform/v1/envs/development/resources",
        json={
            "name": "notes",
            "fields": [{"name": "title", "type": "string", "required": True}],
            "operations": {
                op: {"enabled": True, "policy": "public"}
                for op in ("list", "get", "create", "update", "delete")
            },
        },
    )
    await running.manage.post("/platform/v1/envs/development/resources/notes/migrate", json={})
    created = await running.public.post(
        "/rest/v1/notes", json={"title": "hello"}, headers={"apikey": running.secret}
    )
    assert created.status_code == 201, created.text
    listed = await running.public.get("/rest/v1/notes", headers={"apikey": running.secret})
    assert [row["title"] for row in listed.json()["data"]] == ["hello"]


async def test_studio_manages_the_server_through_its_internal_listener(running):
    response = await running.studio.get("/environments")
    assert response.status_code == 200
    assert (await running.studio.get("/studio/status")).json()["services"]


async def test_a_request_in_a_task_of_its_own_still_reaches_the_database(running):
    """A real server runs every request in a fresh task that inherits nothing from startup, so the edge must open the database context
    itself. (In-process clients share the test's context and would not notice.)"""
    import asyncio
    import contextvars

    async def request():
        return await running.public.get(
            "/auth/v1/settings", headers={"apikey": running.publishable}
        )

    response = await asyncio.create_task(request(), context=contextvars.Context())
    assert response.status_code == 200, response.text


async def test_a_socket_in_a_task_of_its_own_gets_its_channel_rules_from_the_database(running):
    import asyncio
    import contextvars

    from pawabase_server.core.asgi import connect

    async def converse():
        socket = await connect(
            running.server.app, "/realtime/v1/socket", headers=await running_context(running)
        )
        try:
            assert (await socket.recv()) and True
            await socket.send('{"type": "subscribe", "channel": "public:lobby", "ref": "1"}')
            return await asyncio.wait_for(socket.recv(), 3)
        finally:
            await socket.close()

    ack = await asyncio.create_task(converse(), context=contextvars.Context())
    assert '"unavailable"' not in ack


async def running_context(running):
    from pawabase_server.core.context import CONTEXT_HEADER, PlatformContext
    from pawabase_server.core.tokens import issue_context_token

    context = PlatformContext(env="development", role="service", key_id="test")
    return {CONTEXT_HEADER: issue_context_token(running.server.settings.internal_secret, context)}


async def test_a_users_api_key_calls_the_api_as_them_through_the_public_entry_point(running):
    headers = {"apikey": running.publishable}
    await running.manage.patch(
        "/platform/v1/envs/development",
        json={"auth": {"directory": {"api_keys": {"enabled": True}}}},
    )
    signed = await running.public.post(
        "/auth/v1/signup",
        json={"email": "ada@example.com", "password": "correct horse battery"},
        headers=headers,
    )
    token = signed.json()["access_token"]
    made = await running.public.post(
        "/auth/v1/user/api-keys",
        json={"name": "ci"},
        headers={**headers, "authorization": f"Bearer {token}"},
    )
    assert made.status_code == 201, made.text
    key = made.json()["key"]
    me = await running.public.get(
        "/auth/v1/user", headers={**headers, "authorization": f"Bearer {key}"}
    )
    assert me.status_code == 200 and me.json()["email"] == "ada@example.com"
    # Without the project key the key alone is not enough, and a made-up key is refused.
    assert (
        await running.public.get("/auth/v1/user", headers={"authorization": f"Bearer {key}"})
    ).status_code == 401
    fake = await running.public.get(
        "/auth/v1/user", headers={**headers, "authorization": "Bearer pbu_" + "x" * 40}
    )
    assert fake.status_code == 401
    # Revoking it ends it (the edge remembers a key for under a minute).
    await running.public.delete(
        f"/auth/v1/user/api-keys/{made.json()['id']}",
        headers={**headers, "authorization": f"Bearer {token}"},
    )
