"""Server-Sent Events: the same channel/message core as the WebSocket route, over plain HTTP streaming.

``httpx.ASGITransport`` (what the ``api`` fixture's test client uses) awaits the whole ASGI app call to
completion before handing back a response — it has no notion of partial delivery, so it cannot exercise a
genuinely-infinite SSE stream (see ``httpx._transports.asgi.ASGITransport.handle_async_request``: it always
awaits ``self.app(scope, receive, send)`` in full). The happy-path delivery test below instead runs the real
app over a real TCP socket with uvicorn, where streaming actually streams.
"""

import asyncio
import json
import socket

import httpx
import pytest
import uvicorn

ENV = "development"


async def read_events(lines, count):
    """``lines`` is one shared ``aiter_lines()`` iterator — calling ``aiter_lines()`` again on a response
    already being streamed raises ``httpx.StreamConsumed``."""
    out = []
    async for line in lines:
        if not line.startswith("data:"):
            continue
        out.append(json.loads(line[len("data:") :].strip()))
        if len(out) >= count:
            return out
    return out


def realtime_token(settings, user_id="1"):
    """Realtime's WS/SSE endpoints identify the caller from a ``?token=`` query param (a short-lived access
    token), separate from the ``Authorization`` header REST calls use — see ``realtime.identify`` and
    ``routes/realtime/ws.py``."""
    from pawabase_server.core.tokens import issue_user_token

    return issue_user_token(
        settings.jwt_master_secret, env=ENV, user_id=user_id, jti=f"j{user_id}", session_id="s"
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def live_server(api):
    """The same app the ``api`` fixture built, served over a real TCP socket so streaming is real."""
    port = _free_port()
    config = uvicorn.Config(
        api.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            yield client
    finally:
        server.should_exit = True
        await task


async def test_subscribing_over_sse_then_publishing_delivers_the_message(api, live_server):
    headers = api.context_headers(ENV)
    async with live_server.stream(
        "GET",
        f"/realtime/v1/sse?channels=public:lobby&token={realtime_token(api.settings)}",
        headers=headers,
        timeout=10,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.aiter_lines()
        welcome = (await asyncio.wait_for(read_events(lines, 1), timeout=5))[0]
        assert welcome["type"] == "welcome" and welcome["channels"] == ["public:lobby"]

        publish = asyncio.create_task(
            api.http.post(
                "/realtime/v1/publish",
                json={"channel": "public:lobby", "event": "chat", "payload": {"text": "hi"}},
                headers=api.user_headers(ENV, "1"),
            )
        )
        try:
            message = (await asyncio.wait_for(read_events(lines, 1), timeout=5))[0]
        finally:
            await publish
        assert message["type"] == "message" and message["payload"] == {"text": "hi"}


async def test_sse_requires_at_least_one_channel(api):
    response = await api.http.get("/realtime/v1/sse", headers=api.user_headers(ENV, "1"))
    assert response.status_code == 422


async def test_sse_refuses_a_channel_the_caller_may_not_subscribe_to(api):
    # No channel rule matches "private:x", so it falls back to the environment's default policy
    # ("authenticated") — an anonymous caller fails it.
    response = await api.http.get(
        "/realtime/v1/sse?channels=private:x", headers=api.context_headers(ENV, role="anon")
    )
    assert response.status_code == 403
