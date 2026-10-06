"""Self-hosted Studio username/password access tests."""

from __future__ import annotations

import pytest


@pytest.fixture
async def protected_studio(tmp_path):
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app
    from app.config import StudioSettings
    from tests.conftest import FakeAkountz, FakeApi

    dist = tmp_path / "frontend" / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / ".vite" / "manifest.json").write_text(
        '{"src/main.jsx": {"file": "assets/main-abc.js", "css": ["assets/main-abc.css"]}}'
    )
    settings = StudioSettings(
        _env_file=None,
        app_env="testing",
        frontend_dir=str(tmp_path / "frontend"),
        studio_username="operator",
        studio_password="correct-horse",
    )
    api = FakeApi()
    app = create_app(
        settings,
        clients={
            "api": api,
            "akountz": FakeAkountz(master=settings.jwt_master_secret),
            "angula": FakeApi(),
        },
    )
    await app._startup()
    http = AsyncTestClient(app, base_url="http://studio.test", follow_redirects=False)
    try:
        yield http
    finally:
        await http.aclose()
        await app._shutdown()


async def test_password_gate_shows_a_login_form(protected_studio):
    response = await protected_studio.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    login = await protected_studio.get("/login")
    assert login.status_code == 200
    assert "Sign in to Studio" in login.text


async def test_password_gate_accepts_the_configured_credentials(protected_studio):
    refused = await protected_studio.post("/login", content="username=operator&password=nope")
    assert refused.status_code == 401

    accepted = await protected_studio.post(
        "/login", content="username=operator&password=correct-horse"
    )
    assert accepted.status_code == 303
    assert "pb_studio_local=" in accepted.headers["set-cookie"]
    assert (await protected_studio.get("/")).status_code == 302


def test_password_gate_requires_a_complete_pair(tmp_path):
    from app.bootstrap import create_app
    from app.config import StudioSettings

    settings = StudioSettings(
        _env_file=None, frontend_dir=str(tmp_path), studio_username="operator"
    )
    with pytest.raises(ValueError, match="must be set together"):
        create_app(settings)
