"""A Studio session started for one project of a shared runtime sees that project's environments and nothing else."""

import time

import jwt
import pytest

from studio.scope import body_allowed, permitted
from tests.studio.conftest import Studio, fake_core

SECRET = "access-secret-for-the-tests-0123456789"
MINE, THEIRS = "01JPROJECTMINE0000000001", "01JPROJECTTHEIRS00000000002"


def link(project=MINE, jti="one"):
    claims = {"iss": "pawabase-cloud", "aud": "studio", "jti": jti, "exp": int(time.time()) + 60}
    if project:
        claims["project"] = project
    return jwt.encode(claims, SECRET, algorithm="HS256")


@pytest.fixture
async def confined(tmp_path):
    from sillo.testclient import AsyncTestClient

    from studio.app import create_studio
    from studio.config import StudioSettings

    dist = tmp_path / "frontend" / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / ".vite" / "manifest.json").write_text('{"src/main.jsx": {"file": "assets/main-abc.js"}}')
    settings = StudioSettings(
        _env_file=None,
        app_env="testing",
        frontend_dir=str(tmp_path / "frontend"),
        studio_access_secret=SECRET,
    )
    core = fake_core(settings)
    core.environments[:] = [
        {"name": "main", "is_default": True, "project_id": MINE},
        {"name": "mine-staging", "is_default": False, "project_id": MINE},
        {"name": "other-prod", "is_default": False, "project_id": THEIRS},
    ]
    app = create_studio(settings, client=core)
    await app._startup()
    http = AsyncTestClient(app, base_url="http://studio.test", follow_redirects=False)
    try:
        yield Studio(app=app, http=http, api=core, accounts=core.identity, angula=core, settings=settings)
    finally:
        await http.aclose()
        await app._shutdown()


def test_the_rule_is_an_allow_list_that_fails_closed():
    envs = {"main", "mine-staging"}
    yes = [
        ("/", "GET"),
        ("/environments", "GET"),
        ("/envs/main", "GET"),
        ("/envs/main/functions", "GET"),
        ("/envs/main/flows/new", "GET"),
        ("/studio/api/platform/envs/main/resources", "POST"),
        ("/studio/api/platform/runtime", "GET"),
        ("/studio/api/project/usage", "GET"),
        ("/studio/api/platform/usage", "GET"),
        ("/studio/api/gateway/usage", "GET"),
        ("/studio/api/auth/envs/main/users", "GET"),
        ("/studio/api/realtime/main/publish", "POST"),
        ("/assets/main-abc.js", "GET"),
    ]
    no = [
        ("/audit", "GET"),
        ("/envs/other-prod", "GET"),
        ("/envs/other-prod/users", "GET"),
        ("/envs/main/promote", "POST"),
        ("/studio/api/platform/envs", "GET"),
        ("/studio/api/platform/envs", "POST"),
        ("/studio/api/platform/envs/other-prod/keys", "GET"),
        ("/studio/api/platform/envs/main", "DELETE"),
        ("/studio/api/platform/envs/main/previews", "POST"),
        ("/studio/api/platform/envs/main/promote", "POST"),
        ("/studio/api/platform/workers", "GET"),
        ("/studio/api/platform/runtime", "POST"),
        ("/studio/api/platform/envs/../envs/other-prod/keys", "GET"),
        ("/studio/api/auth/envs/other-prod/users", "GET"),
        ("/studio/api/auth/users", "GET"),
        ("/studio/api/realtime/other-prod/publish", "POST"),
        ("/studio/api/telemetry/overview", "GET"),
        ("/studio/api/unknown/x", "GET"),
        ("/internal/v1/anything", "GET"),
    ]
    for path, method in yes:
        assert permitted(path, method, envs), (method, path)
    for path, method in no:
        assert not permitted(path, method, envs), (method, path)


def test_an_environments_infra_cannot_be_written_from_a_confined_session():
    assert not body_allowed("/envs/main", "PATCH", {"infra": {"database_url": "postgres://x"}})
    assert not body_allowed("/envs", "POST", {"name": "x", "infra": {}})
    assert body_allowed("/envs/main", "PATCH", {"auth": {"site_url": "https://a.test"}})
    assert body_allowed("/envs/main/resources", "POST", {"infra": "a column named infra"})


async def test_a_confined_session_sees_only_its_projects_environments(confined):
    assert (await confined.http.get(f"/studio-access?token={link()}")).status_code == 303
    assert (await confined.http.get("/envs/main")).status_code == 200
    assert (await confined.http.get("/envs/mine-staging")).status_code == 200
    for path in ("/envs/other-prod", "/envs/other-prod/users", "/audit"):
        response = await confined.http.get(path)
        assert response.status_code == 403 and response.json()["error"] == "outside_project", path
    assert ("GET", "/platform/v1/envs/other-prod/overview", None, None) not in confined.api.calls


async def test_the_runtime_listing_and_the_pages_are_cut_down_to_the_project(confined):
    await confined.http.get(f"/studio-access?token={link()}")
    listing = (await confined.http.get("/studio/api/platform/runtime")).json()
    assert [e["name"] for e in listing["environments"]] == ["main", "mine-staging"]
    redirect = await confined.http.get("/")
    assert redirect.status_code == 302 and redirect.headers["location"] == "/envs/main"
    page = await confined.http.get("/environments", headers={"X-Inertia": "true"})
    assert "other-prod" not in page.text


async def test_a_confined_session_can_read_its_project_usage(confined):
    await confined.http.get(f"/studio-access?token={link()}")
    usage = await confined.http.get("/studio/api/project/usage")
    assert usage.status_code == 200


async def test_the_bridge_and_the_explorer_refuse_other_environments_and_infra_writes(confined):
    await confined.http.get(f"/studio-access?token={link()}")
    csrf = await confined.csrf()
    other = await confined.http.get("/studio/api/platform/envs/other-prod/keys")
    assert other.status_code == 403 and other.json()["error"] == "outside_project"
    bridged = await confined.http.get("/studio/api/auth/envs/other-prod/users")
    assert bridged.status_code == 403 and bridged.json()["error"] == "outside_project"
    explored = await confined.http.post(
        "/studio/api/explorer/request", json={"env": "other-prod", "path": "/orders"}, headers=csrf
    )
    assert explored.status_code == 403 and "not part of your project" in explored.json()["detail"]
    signed = await confined.http.post(
        "/studio/api/explorer/sign-in",
        json={"env": "other-prod", "email": "a@b.c", "password": "x"},
        headers=csrf,
    )
    assert signed.status_code == 403 and "not part of your project" in signed.json()["detail"]
    infra = await confined.http.patch(
        "/studio/api/platform/envs/main",
        json={"infra": {"database_url": "postgres://elsewhere"}},
        headers=csrf,
    )
    assert infra.status_code == 403 and "managed by your hosting service" in infra.json()["detail"]
    assert not any(c[0] == "PATCH" for c in confined.api.calls)
    ok = await confined.http.post(
        "/studio/api/explorer/request", json={"env": "main", "path": "/orders"}, headers=csrf
    )
    assert ok.status_code == 200
    settings = await confined.http.patch(
        "/studio/api/platform/envs/main", json={"auth": {"site_url": "https://a.test"}}, headers=csrf
    )
    assert settings.status_code == 200


async def test_the_project_cannot_be_changed_in_the_cookie(confined):
    opened = await confined.http.get(f"/studio-access?token={link()}")
    value = opened.headers["set-cookie"].split("pb_studio=")[1].split(";")[0]
    expires, project, signature = value.split(".")
    for forged in (f"{expires}.{THEIRS}.{signature}", f"{expires}.{signature}"):
        confined.http.cookies.set("pb_studio", forged)
        assert (await confined.http.get("/envs/other-prod")).status_code in (401, 403)


async def test_a_link_without_a_project_is_unconfined_as_before(confined):
    await confined.http.get(f"/studio-access?token={link(project=None, jti='two')}")
    assert (await confined.http.get("/envs/other-prod")).status_code == 200
    assert (await confined.http.get("/audit")).status_code == 200


async def test_when_the_runtime_cannot_say_what_the_project_has_nothing_is_shown(confined):
    await confined.http.get(f"/studio-access?token={link()}")

    async def broken(*a, **k):
        raise RuntimeError("runtime down")

    confined.api.request = broken
    assert (await confined.http.get("/envs/main")).status_code == 403
