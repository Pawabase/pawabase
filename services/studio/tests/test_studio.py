import json

INERTIA = {"X-Inertia": "true"}


async def test_pages_require_sign_in(studio):
    response = await studio.http.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    bridge = await studio.http.get("/studio/api/platform/projects")
    assert bridge.status_code == 401


async def test_login_page_is_a_full_document_with_built_assets(studio):
    response = await studio.http.get("/login")
    assert response.status_code == 200
    assert "/assets/main-abc.js" in response.text
    assert "/assets/main-abc.css" in response.text
    assert 'data-page="app"' in response.text
    asset = await studio.http.get("/assets/main-abc.js")
    assert asset.status_code == 200
    assert "studio" in asset.text


async def test_sign_in_and_render_pages(studio):
    await studio.login()
    home = await studio.http.get("/", headers=INERTIA)
    assert home.status_code == 200
    page = home.json()
    assert page["component"] == "Projects/Index"
    assert page["props"]["projects"] == [{"ref": "shop", "name": "Shop"}]
    assert page["props"]["operator"] == {
        "id": "1",
        "email": "root@pawabase.dev",
        "roles": ["admin"],
    }
    # The operator's tokens never reach the browser.
    assert "access_token" not in home.text

    env = (await studio.http.get("/projects/shop/main", headers=INERTIA)).json()
    assert env["component"] == "Env/Overview"
    assert env["props"]["overview"] == {"resources": 2}

    kind = (await studio.http.get("/projects/shop/main/policies", headers=INERTIA)).json()
    assert (kind["component"], kind["props"]["kind"]) == ("Env/Definitions", "policies")

    editor = (await studio.http.get("/projects/shop/main/flows/new", headers=INERTIA)).json()
    assert editor["component"] == "Flows/Editor"
    assert editor["props"]["flow"] is None
    assert editor["props"]["blocks"] == [{"name": "trigger.http"}]

    missing = await studio.http.get("/projects/nope", headers=INERTIA)
    assert missing.status_code == 404
    assert missing.json()["component"] == "Errors/NotFound"
    unknown = await studio.http.get("/projects/shop/main/bogus", headers=INERTIA)
    assert unknown.status_code == 404


async def test_wrong_password_flashes_an_error(studio):
    await studio.http.get("/login")
    response = await studio.http.post(
        "/login",
        json={"email": "root@pawabase.dev", "password": "nope"},
        headers={**studio.csrf(), "Referer": "/login"},
    )
    assert response.status_code == 303
    page = (await studio.http.get("/login", headers=INERTIA)).json()
    assert page["props"]["errors"] == {"email": "invalid email or password"}


async def test_mfa_challenge(studio):
    studio.akountz.mfa = True
    await studio.http.get("/login")
    response = await studio.http.post(
        "/login",
        json={"email": "root@pawabase.dev", "password": "Sup3r-secret!pass"},
        headers=studio.csrf(),
    )
    assert response.headers["location"] == "/login?mfa_token=challenge"
    done = await studio.http.post(
        "/login", json={"mfa_token": "challenge", "code": "123456"}, headers=studio.csrf()
    )
    assert done.headers["location"] == "/"
    assert (await studio.http.get("/", headers=INERTIA)).status_code == 200


async def test_bridge_forwards_as_the_operator(studio):
    await studio.login()
    listed = await studio.http.get(
        "/studio/api/platform/projects/shop/envs/main/jobs?status=failed"
    )
    assert listed.status_code == 200
    assert studio.api.calls[-1] == (
        "GET",
        "/platform/v1/projects/shop/envs/main/jobs",
        None,
        {"status": "failed"},
    )

    created = await studio.http.post(
        "/studio/api/platform/projects/shop/envs/main/schemas",
        content=json.dumps({"name": "order", "fields": {}}),
        headers={**studio.csrf(), "content-type": "application/json"},
    )
    assert created.status_code == 200
    invalid = await studio.http.post(
        "/studio/api/platform/projects/shop/envs/main/schemas",
        content="{}",
        headers={**studio.csrf(), "content-type": "application/json"},
    )
    assert invalid.status_code == 422
    assert invalid.json() == {"detail": "name is required"}

    users = await studio.http.get("/studio/api/auth/projects/shop/envs/main/users")
    assert users.json()["data"][0]["email"] == "u@example.com"
    assert (await studio.http.get("/studio/api/other/x")).status_code == 404


async def test_mutations_need_the_csrf_token(studio):
    await studio.login()
    response = await studio.http.post(
        "/studio/api/platform/projects", content="{}", headers={"content-type": "application/json"}
    )
    assert response.status_code == 403


async def test_logout(studio):
    await studio.login()
    response = await studio.http.post("/logout", headers=studio.csrf())
    assert response.headers["location"] == "/login"
    assert (await studio.http.get("/")).status_code == 302


async def test_realtime_publish_carries_the_environment(studio):
    await studio.login()
    response = await studio.http.post(
        "/studio/api/realtime/shop/main/publish",
        content=json.dumps({"channel": "room:1", "payload": {"x": 1}}),
        headers={**studio.csrf(), "content-type": "application/json"},
    )
    assert response.status_code == 200
    method, path, body, _ = studio.angula.calls[-1]
    assert (method, path, body["channel"]) == ("POST", "/internal/v1/publish", "room:1")
    context = studio.angula.last_kwargs["context"]
    assert (context.project, context.env, context.role) == ("shop", "main", "service")
    listed = await studio.http.get("/studio/api/realtime/shop/main/channels")
    assert listed.status_code == 200
    assert studio.angula.calls[-1][1] == "/internal/v1/realtime/shop/main/channels"
    telemetry = await studio.http.get("/studio/api/telemetry/requests?project=shop")
    assert telemetry.status_code == 200
    assert studio.api.calls[-1][1] == "/internal/v1/telemetry/requests"
