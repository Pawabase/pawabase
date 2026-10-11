"""Fixtures that build the realtime routes on a stand-in platform, so tests can set limits per test."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from tests.realtime.helpers import FakeApi


@pytest.fixture
def make_app():
    """``make_app(**settings)`` builds an app and yields nothing until used; every app made is closed at the end of the test."""
    from sillo.testclient import TestClient

    from pawabase_server.app import housekeeping
    from pawabase_server.core.auth import UserBackend
    from pawabase_server.core.service import create_service
    from pawabase_server.realtime import Realtime
    from pawabase_server.routes.realtime import register_routes
    from pawabase_server.settings import Settings

    opened: list[TestClient] = []

    def build(**overrides):
        settings = Settings(_env_file=None, app_env="testing", **overrides)
        fake = FakeApi()
        realtime = Realtime(settings, platform=fake)
        app = create_service(
            "pawabase",
            settings,
            title="Realtime under test",
            description="Only the realtime routes, on a stand-in platform.",
            backends=[UserBackend(settings.jwt_master_secret)],
        )
        app.state["realtime"] = realtime
        tasks: list[asyncio.Task] = []

        @app.on_startup
        async def start() -> None:
            await realtime.start()
            tasks.append(asyncio.create_task(housekeeping(realtime, settings.sweep_interval)))

        @app.on_shutdown
        async def stop() -> None:
            for task in tasks:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            await realtime.stop()

        register_routes(app, realtime)
        client = TestClient(app)
        client.__enter__()
        opened.append(client)
        return client, settings, fake, realtime

    yield build
    for client in reversed(opened):
        client.__exit__(None, None, None)


@pytest.fixture
def realtime_app(make_app):
    return make_app()
