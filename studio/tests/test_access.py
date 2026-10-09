import time

import jwt
import pytest

from tests.conftest import FakeAkountz, FakeApi, Studio

SECRET = "access-secret-for-the-tests-0123456789"


def link(secret=SECRET, *, ttl=60, jti="one", aud="studio", iss="pawabase-cloud"):
    return jwt.encode(
        {
            "iss": iss,
            "aud": aud,
            "jti": jti,
            "sub": "ada@example.com",
            "exp": int(time.time()) + ttl,
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture
async def gated(tmp_path):
    from sillo.testclient import AsyncTestClient

    from app.bootstrap import create_app
    from app.config import StudioSettings

    dist = tmp_path / "frontend" / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / ".vite" / "manifest.json").write_text(
        '{"src/main.jsx": {"file": "assets/main-abc.js"}}'
    )
    settings = StudioSettings(
        _env_file=None,
        app_env="testing",
        frontend_dir=str(tmp_path / "frontend"),
        studio_access_secret=SECRET,
    )
    api, akountz, angula = FakeApi(), FakeAkountz(master=settings.jwt_master_secret), FakeApi()
    app = create_app(settings, clients={"api": api, "akountz": akountz, "angula": angula})
    await app._startup()
    http = AsyncTestClient(app, base_url="http://studio.test", follow_redirects=False)
    try:
        yield Studio(app=app, http=http, api=api, akountz=akountz, angula=angula, settings=settings)
    finally:
        await http.aclose()
        await app._shutdown()


async def test_nothing_opens_without_a_session(gated):
    for path in ("/", "/environments", "/envs/main", "/studio/api/platform/runtime"):
        response = await gated.http.get(path)
        assert (
            response.status_code == 401 and response.json()["error"] == "studio_access_required"
        ), path
    assert (
        await gated.http.get("/health")
    ).status_code == 200  # the container's health check still works
    assert gated.api.calls == []  # nothing reached the backend


async def test_a_signed_link_starts_a_session_once(gated):
    opened = await gated.http.get(f"/studio-access?token={link()}")
    assert opened.status_code == 303 and opened.headers["location"] == "/"
    assert (
        "pb_studio=" in opened.headers["set-cookie"] and "HttpOnly" in opened.headers["set-cookie"]
    )
    assert (
        await gated.http.get("/")
    ).status_code == 302  # signed in: Studio redirects into the default environment
    # the same link does not work twice
    again = await gated.http.get(f"/studio-access?token={link()}")
    assert again.status_code == 403


async def test_bad_links_are_refused(gated):
    cases = {
        "wrong secret": link("another-secret-of-sufficient-length-0123"),
        "expired": link(ttl=-5, jti="a"),
        "too long lived": link(ttl=3600, jti="b"),
        "wrong audience": link(aud="api", jti="c"),
        "wrong issuer": link(iss="someone-else", jti="d"),
        "not a token": "nonsense",
        "none": "",
    }
    for name, token in cases.items():
        assert (await gated.http.get(f"/studio-access?token={token}")).status_code == 403, name
    assert (await gated.http.get("/")).status_code == 401


async def test_a_forged_or_expired_session_cookie_is_not_a_session(gated):
    for value in ("9999999999.deadbeef", f"{int(time.time()) - 5}.abc", "garbage", ""):
        gated.http.cookies.set("pb_studio", value)
        assert (await gated.http.get("/")).status_code == 401, value


async def test_an_ungated_studio_stays_open(studio):
    assert (
        await studio.http.get("/")
    ).status_code == 302  # no secret configured: unchanged behaviour


def test_the_environment_variable_that_turns_the_gate_on_is_the_documented_one(monkeypatch):
    """The name in docker-compose.hosted.yml must be the one Studio reads: a wrong name leaves Studio silently open."""
    from app.config import StudioSettings

    monkeypatch.setenv("PAWABASE_STUDIO_ACCESS_SECRET", "from-the-environment-0123456789abcdef")
    assert (
        StudioSettings(_env_file=None).studio_access_secret
        == "from-the-environment-0123456789abcdef"
    )
    monkeypatch.delenv("PAWABASE_STUDIO_ACCESS_SECRET")
    assert StudioSettings(_env_file=None).studio_access_secret == ""
