import json

from tests.conftest import ORG, PASSWORD

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
    assert (await studio.http.get("/")).headers["location"] == "/orgs/acme"
    home = await studio.http.get("/orgs/acme", headers=INERTIA)
    assert home.status_code == 200
    page = home.json()
    assert page["component"] == "Projects/Index"
    assert page["props"]["projects"] == [{"ref": "shop", "name": "Shop", "org": "acme"}]
    assert page["props"]["org"]["slug"] == "acme"
    assert page["props"]["orgs"][0]["slug"] == "acme"
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

    explorer = (await studio.http.get("/projects/shop/main/explorer", headers=INERTIA)).json()
    assert explorer["component"] == "Env/Explorer"

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
    assert (await studio.http.get("/orgs/acme", headers=INERTIA)).status_code == 200


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


async def test_explorer_uses_anonymous_context_and_optional_user_token(studio):
    await studio.login()
    signed_in = await studio.http.post(
        "/studio/api/explorer/sign-in",
        content=json.dumps(
            {
                "project": "shop",
                "env": "main",
                "email": "buyer@example.com",
                "password": "Sup3r-secret!pass",
            }
        ),
        headers={**studio.csrf(), "content-type": "application/json"},
    )
    assert signed_in.status_code == 200 and signed_in.json()["access_token"]
    response = await studio.http.post(
        "/studio/api/explorer/request",
        content=json.dumps(
            {
                "project": "shop",
                "env": "main",
                "version": "v2",
                "method": "POST",
                "path": "/orders",
                "query": {"expand": "items"},
                "body": {"total": 42},
                "headers": {"x-idempotency-key": "once", "apikey": "must-not-pass"},
                "access_token": "user-token",
            }
        ),
        headers={**studio.csrf(), "content-type": "application/json"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == 201 and result["body"] == {"id": 7, "total": 42}
    assert "set-cookie" not in result["headers"]
    call = studio.api.raw_call
    assert (call["method"], call["path"], call["params"]) == (
        "POST",
        "/rest/v2/orders",
        {"expand": "items"},
    )
    assert (call["context"].project, call["context"].env, call["context"].role) == (
        "shop",
        "main",
        "anon",
    )
    assert call["headers"]["Authorization"] == "Bearer user-token"
    assert "apikey" not in call["headers"]


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


async def test_nothing_exists_outside_an_organization(studio):
    """With no organization, every page sends the operator to create one."""
    studio.api.orgs = []
    await studio.login()
    assert (await studio.http.get("/")).headers["location"] == "/setup"
    assert (await studio.http.get("/projects/shop")).headers["location"] == "/setup"
    assert (await studio.http.get("/orgs/acme")).headers["location"] == "/setup"
    setup = (await studio.http.get("/setup", headers=INERTIA)).json()
    assert (setup["component"], setup["props"]["first"]) == ("Org/Create", True)
    # Once they have one, setup is done.
    studio.api.orgs = [dict(ORG)]
    assert (await studio.http.get("/setup")).headers["location"] == "/"


async def test_organization_pages(studio):
    await studio.login()
    team = (await studio.http.get("/orgs/acme/team", headers=INERTIA)).json()
    assert team["component"] == "Org/Team"
    assert [m["email"] for m in team["props"]["members"]] == ["root@pawabase.dev"]
    assert team["props"]["invitations"][0]["email"] == "new@example.com"
    settings = (await studio.http.get("/orgs/acme/settings", headers=INERTIA)).json()
    assert settings["component"] == "Org/Settings"
    audit = (await studio.http.get("/orgs/acme/audit", headers=INERTIA)).json()
    assert audit["component"] == "Audit"
    assert ("GET", "/platform/v1/audit", None, {"limit": 200, "org": "acme"}) in studio.api.calls
    assert (await studio.http.get("/audit")).headers["location"] == "/orgs/acme/audit"
    # Non-admins do not get the invitation list.
    studio.api.orgs = [{**ORG, "role": "viewer"}]
    studio.api.calls.clear()
    await studio.http.get("/orgs/acme/team", headers=INERTIA)
    assert not any(path.endswith("/invitations") for _, path, _, _ in studio.api.calls)


async def test_the_last_organization_is_remembered(studio):
    studio.api.orgs = [dict(ORG), {**ORG, "slug": "beta", "name": "Beta"}]
    await studio.login()
    await studio.http.get("/projects/shop", headers=INERTIA)
    assert (await studio.http.get("/")).headers["location"] == "/orgs/acme"


async def test_invitation_link_is_public_and_only_offers_its_own_address(studio):
    good = (await studio.http.get("/invite/good", headers=INERTIA)).json()
    assert good["component"] == "Auth/Invite"
    assert good["props"]["invitation"]["email"] == "new@example.com"
    assert "token" not in good["props"]["invitation"]
    bad = (await studio.http.get("/invite/stale", headers=INERTIA)).json()
    assert bad["props"]["invitation"] is None


async def test_signed_in_operator_accepts_an_invitation(studio):
    await studio.login()
    response = await studio.http.post("/invite/good", json={}, headers=studio.csrf())
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/orgs/acme"
    assert ("POST", "/platform/v1/invitations/good/accept", None, None) in studio.api.calls


async def test_login_returns_to_a_same_site_page_only(studio):
    await studio.http.get("/login")
    response = await studio.http.post(
        "/login",
        json={"email": "root@pawabase.dev", "password": PASSWORD, "next": "/invite/good"},
        headers=studio.csrf(),
    )
    assert response.headers["location"] == "/invite/good"
    await studio.http.post("/logout", headers=studio.csrf())
    await studio.http.get("/login")
    response = await studio.http.post(
        "/login",
        json={"email": "root@pawabase.dev", "password": PASSWORD, "next": "https://evil.example"},
        headers=studio.csrf(),
    )
    assert response.headers["location"] == "/"


async def test_paths_that_skip_the_api_still_respect_organizations(studio):
    """Realtime, identities, telemetry and the Explorer are forwarded without
    the API's organization check, so Studio makes it first."""
    await studio.login()
    other = "/studio/api/realtime/rival/main/channels"
    assert (await studio.http.get(other)).status_code == 404
    assert (await studio.http.get("/studio/api/auth/projects/rival/envs/main/users")).status_code == 404
    assert (await studio.http.get("/studio/api/auth/projects/_platform/envs/main/users")).status_code == 404
    assert (await studio.http.get("/studio/api/telemetry/requests")).status_code == 404
    assert (await studio.http.get("/projects/rival/main/realtime/ticket")).status_code == 404
    explored = await studio.http.post(
        "/studio/api/explorer/request",
        json={"project": "rival", "env": "main", "method": "GET", "path": "/x"},
        headers=studio.csrf(),
    )
    assert explored.status_code == 404
    mine = await studio.http.get("/studio/api/auth/projects/shop/envs/main/users")
    assert mine.status_code == 200


def test_mail_is_a_service_page_and_the_mail_templates_page_is_gone():
    from routes import DEFINITION_KINDS, SECTIONS

    assert SECTIONS["mail"] == "Env/Mail"
    assert "mail-templates" not in SECTIONS and "mail-templates" not in DEFINITION_KINDS
