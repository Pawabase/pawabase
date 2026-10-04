import json

from tests.conftest import PASSWORD

INERTIA = {"X-Inertia": "true"}


async def test_studio_opens_straight_into_the_default_environment(studio):
    """No sign-in, no organization, no project: start it and you are managing the backend."""
    response = await studio.http.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/envs/main"
    page = (await studio.http.get("/envs/main", headers=INERTIA)).json()
    assert page["component"] == "Env/Overview"
    assert page["props"]["overview"] == {"resources": 2, "version": 3}
    assert page["props"]["runtime"]["name"] == "Shop"
    assert [e["name"] for e in page["props"]["envs"]] == ["main", "staging"]
    # What the old platform asked a developer to do first no longer exists.
    for gone in ("/login", "/signup", "/logout", "/setup", "/orgs/new", "/orgs/acme", "/projects/shop", "/invite/x"):
        assert (await studio.http.get(gone, headers=INERTIA)).status_code == 404, gone
    assert "operator" not in page["props"] and "orgs" not in page["props"]


async def test_studio_with_no_environment_lists_environments(studio):
    studio.api.environments = []
    assert (await studio.http.get("/")).headers["location"] == "/environments"
    page = (await studio.http.get("/environments", headers=INERTIA)).json()
    assert (page["component"], page["props"]["envs"]) == ("Environments", [])


async def test_pages_are_a_full_document_with_built_assets(studio):
    response = await studio.http.get("/envs/main")
    assert response.status_code == 200
    assert "/assets/main-abc.js" in response.text
    assert "/assets/main-abc.css" in response.text
    assert 'data-page="app"' in response.text
    asset = await studio.http.get("/assets/main-abc.js")
    assert asset.status_code == 200 and "studio" in asset.text


async def test_the_sections_render_and_unknown_things_are_404(studio):
    kind = (await studio.http.get("/envs/main/policies", headers=INERTIA)).json()
    assert (kind["component"], kind["props"]["kind"]) == ("Env/Definitions", "policies")
    assert (await studio.http.get("/envs/main/explorer", headers=INERTIA)).json()["component"] == "Env/Explorer"

    editor = (await studio.http.get("/envs/main/flows/new", headers=INERTIA)).json()
    assert editor["component"] == "Flows/Editor"
    assert editor["props"]["flow"] is None
    assert editor["props"]["blocks"] == [{"name": "trigger.http"}]

    missing = await studio.http.get("/envs/nope", headers=INERTIA)
    assert missing.status_code == 404
    assert missing.json()["component"] == "Errors/NotFound"
    assert (await studio.http.get("/envs/main/bogus", headers=INERTIA)).status_code == 404

    audit = (await studio.http.get("/audit", headers=INERTIA)).json()
    assert audit["component"] == "Audit" and audit["props"]["entries"][0]["actor"] == "studio"
    assert ("GET", "/platform/v1/audit", None, {"limit": 200}) in studio.api.calls


async def test_api_docs_come_from_the_api_and_point_at_the_gateway(studio):
    spec = (await studio.http.get("/envs/main/api-docs/openapi.json")).json()
    assert spec["servers"][0]["url"] == studio.settings.public_gateway_url
    page = await studio.http.get("/envs/main/api-docs")
    assert page.status_code == 200 and "Shop API" in page.text


async def test_the_bridge_forwards_with_a_service_token_and_no_operator(studio):
    listed = await studio.http.get("/studio/api/platform/envs/main/jobs?status=failed")
    assert listed.status_code == 200
    assert studio.api.calls[-1] == ("GET", "/platform/v1/envs/main/jobs", None, {"status": "failed"})
    assert "operator" not in studio.api.last_kwargs

    created = await studio.http.post(
        "/studio/api/platform/envs/main/schemas",
        content=json.dumps({"name": "order", "fields": {}}),
        headers={**await studio.csrf(), "content-type": "application/json"},
    )
    assert created.status_code == 200
    invalid = await studio.http.post(
        "/studio/api/platform/envs/main/schemas",
        content="{}",
        headers={**await studio.csrf(), "content-type": "application/json"},
    )
    assert invalid.status_code == 422 and invalid.json() == {"detail": "name is required"}

    users = await studio.http.get("/studio/api/auth/envs/main/users")
    assert users.json()["data"][0]["email"] == "u@example.com"
    assert studio.akountz.calls[-1][0] == "GET /admin/v1/envs/main/users"
    assert (await studio.http.get("/studio/api/other/x")).status_code == 404


async def test_the_explorer_uses_an_anonymous_context_and_an_optional_user_token(studio):
    signed_in = await studio.http.post(
        "/studio/api/explorer/sign-in",
        content=json.dumps({"env": "main", "email": "buyer@example.com", "password": PASSWORD}),
        headers={**await studio.csrf(), "content-type": "application/json"},
    )
    assert signed_in.status_code == 200 and signed_in.json()["access_token"]
    assert studio.akountz.last_kwargs["context"].env == "main"
    response = await studio.http.post(
        "/studio/api/explorer/request",
        content=json.dumps(
            {
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
        headers={**await studio.csrf(), "content-type": "application/json"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == 201 and result["body"] == {"id": 7, "total": 42}
    assert "set-cookie" not in result["headers"]
    call = studio.api.raw_call
    assert (call["method"], call["path"], call["params"]) == ("POST", "/rest/v2/orders", {"expand": "items"})
    assert (call["context"].env, call["context"].role) == ("main", "anon")
    assert call["headers"]["Authorization"] == "Bearer user-token"
    assert "apikey" not in call["headers"]
    bad = await studio.http.post(
        "/studio/api/explorer/request",
        json={"env": "Not A Name", "method": "GET", "path": "/x"},
        headers=await studio.csrf(),
    )
    assert bad.status_code == 400


async def test_mutations_need_the_csrf_token(studio):
    """With no sign-in, the CSRF token is what stops another website acting through the developer's browser."""
    response = await studio.http.post(
        "/studio/api/platform/envs", content="{}", headers={"content-type": "application/json"}
    )
    assert response.status_code == 403


async def test_realtime_publish_carries_the_environment(studio):
    response = await studio.http.post(
        "/studio/api/realtime/main/publish",
        content=json.dumps({"channel": "room:1", "payload": {"x": 1}}),
        headers={**await studio.csrf(), "content-type": "application/json"},
    )
    assert response.status_code == 200
    method, path, body, _ = studio.angula.calls[-1]
    assert (method, path, body["channel"]) == ("POST", "/internal/v1/publish", "room:1")
    context = studio.angula.last_kwargs["context"]
    assert (context.env, context.role) == ("main", "service")
    listed = await studio.http.get("/studio/api/realtime/main/channels")
    assert listed.status_code == 200
    assert studio.angula.calls[-1][1] == "/internal/v1/realtime/main/channels"
    telemetry = await studio.http.get("/studio/api/telemetry/requests")
    assert telemetry.status_code == 200
    assert studio.api.calls[-1][1] == "/internal/v1/telemetry/requests"


async def test_the_realtime_console_ticket_is_a_short_lived_context_for_one_environment(studio):
    from pawabase_core.tokens import verify_context_token

    ticket = (await studio.http.get("/envs/staging/realtime/ticket")).json()["ticket"]
    context = verify_context_token(ticket, studio.settings.internal_secret)
    assert (context.env, context.key_id) == ("staging", "studio-console-ticket")


def test_mail_is_a_service_page_and_the_mail_templates_page_is_gone():
    from routes import DEFINITION_KINDS, SECTIONS

    assert SECTIONS["mail"] == "Env/Mail"
    assert "mail-templates" not in SECTIONS and "mail-templates" not in DEFINITION_KINDS
