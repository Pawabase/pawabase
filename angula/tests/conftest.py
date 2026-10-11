"""Fixtures that build an Angula app with a fake API, so tests can set limits per test."""

from __future__ import annotations

import pytest

from tests.helpers import FakeApi


@pytest.fixture
def make_app():
    """``make_app(**settings)`` builds an app and yields nothing until used; every app made is closed at the end of the test."""
    from sillo.testclient import TestClient

    from app.bootstrap import create_app
    from app.config import AngulaSettings
    from app.realtime import Realtime

    opened: list[TestClient] = []

    def build(**overrides):
        settings = AngulaSettings(_env_file=None, app_env="testing", **overrides)
        fake = FakeApi()
        realtime = Realtime(settings, api=fake)
        app = create_app(settings, realtime=realtime)
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
