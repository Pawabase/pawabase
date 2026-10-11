"""Self-hosted Studio username/password access tests."""

from __future__ import annotations

import pytest


@pytest.fixture
async def protected_studio(tmp_path):
    from sillo.testclient import AsyncTestClient

    from studio.app import create_studio
    from studio.config import StudioSettings
    from tests.studio.conftest import fake_core

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
    app = create_studio(settings, client=fake_core(settings))
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
    assert 'alt="Pawabase"' in login.text
    assert "box-shadow" not in login.text


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
    from studio.app import create_studio
    from studio.config import StudioSettings

    settings = StudioSettings(
        _env_file=None, frontend_dir=str(tmp_path), studio_username="operator"
    )
    with pytest.raises(ValueError, match="must be set together"):
        create_studio(settings)


async def test_a_failed_sign_in_keeps_the_username_and_escapes_it(protected_studio):
    hostile = '"><script>alert(1)</script>'
    from urllib.parse import quote

    response = await protected_studio.post("/login", content=f"username={quote(hostile)}&password=nope")
    assert response.status_code == 401
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert 'role="alert"' in response.text and "Invalid username or password." in response.text


async def test_repeated_failures_are_slowed_and_a_success_resets_the_count(protected_studio):
    for _ in range(5):
        assert (await protected_studio.post("/login", content="username=operator&password=wrong")).status_code == 401
    blocked = await protected_studio.post("/login", content="username=operator&password=correct-horse")
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert "Too many failed sign-in attempts." in blocked.text and "Try again in" in blocked.text


def test_the_failure_log_expires_old_failures_and_stays_bounded():
    from studio.password_access import MAX_TRACKED, FailureLog

    now = [0.0]
    log = FailureLog(limit=2, window=60, clock=lambda: now[0])
    log.record("a"), log.record("a")
    assert log.blocked_for("a") > 0 and log.blocked_for("b") == 0
    now[0] = 61
    assert log.blocked_for("a") == 0
    for index in range(MAX_TRACKED + 50):
        log.record(f"ip-{index}")
    assert len(log._failures) <= MAX_TRACKED
    log.record("a"), log.clear("a")
    assert log.blocked_for("a") == 0


async def test_the_form_refuses_an_oversized_body_and_sets_defensive_headers(protected_studio):
    big = await protected_studio.post("/login", content="username=" + "x" * 20000)
    assert big.status_code == 413
    page = await protected_studio.get("/login")
    assert page.headers["x-frame-options"] == "DENY" and page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["cache-control"] == "no-store"
