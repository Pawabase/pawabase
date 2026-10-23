"""Mounted Python routes use ``authorize`` to get the platform's policy check and a runtime that acts for the caller."""

import textwrap

import pytest

ENV = "/platform/v1/envs/development"

ROUTES = textwrap.dedent(
    """
    from sillo import HttpContext, Router

    from pawabase_server.extend import authorize

    router = Router(prefix="/x/shop")

    @router.get("/me")
    async def me(ctx: HttpContext):
        call = await authorize(ctx, "authenticated")
        return {"user": call.auth["user_id"], "env": call.env}

    @router.post("/notes")
    async def add_note(ctx: HttpContext):
        call = await authorize(ctx, "authenticated")
        body = await ctx.json
        note = await call.runtime.resource_create("notes", {"title": body["title"], "owner": call.auth["user_id"]})
        return {"data": note}

    @router.post("/sweep")
    async def sweep(ctx: HttpContext):
        call = await authorize(ctx, "service")
        return {"notes": (await call.runtime.resource_list("notes"))["total"]}

    @router.post("/small")
    async def small(ctx: HttpContext):
        body = await ctx.json
        await authorize(ctx, {"lte": ["$input.size", 5]}, input=body)
        return {"ok": True}
    """
)

NOTES = {
    "name": "notes",
    "fields": [{"name": "title", "type": "string"}, {"name": "owner", "type": "string"}],
    "operations": {},
}


@pytest.fixture
async def shop(api, tmp_path):
    (tmp_path / "code").mkdir(parents=True, exist_ok=True)
    (tmp_path / "code" / "routes.py").write_text(ROUTES)
    await api.studio.post(f"{ENV}/resources", json=NOTES)
    await api.studio.post(f"{ENV}/resources/notes/migrate")
    return api


async def test_a_mounted_route_knows_who_is_calling(shop):
    anon = shop.context_headers("development")
    ada = shop.user_headers("development", user_id="7")
    assert (await shop.http.get("/rest/v1/x/shop/me", headers=anon)).status_code == 401
    me = await shop.http.get("/rest/v1/x/shop/me", headers=ada)
    assert me.status_code == 200 and me.json() == {"user": "7", "env": "development"}


async def test_the_runtime_acts_for_the_caller(shop):
    ada = shop.user_headers("development", user_id="7")
    made = await shop.http.post("/rest/v1/x/shop/notes", json={"title": "hello"}, headers=ada)
    assert made.status_code == 200 and made.json()["data"]["owner"] == "7"
    service = shop.context_headers("development", role="service")
    assert (await shop.http.post("/rest/v1/x/shop/sweep", headers=service)).json() == {"notes": 1}


async def test_a_service_policy_refuses_signed_in_users(shop):
    ada = shop.user_headers("development", user_id="7")
    assert (await shop.http.post("/rest/v1/x/shop/sweep", headers=ada)).status_code == 403


async def test_conditions_on_the_body_are_checked(shop):
    ada = shop.user_headers("development", user_id="7")
    assert (await shop.http.post("/rest/v1/x/shop/small", json={"size": 3}, headers=ada)).status_code == 200
    assert (await shop.http.post("/rest/v1/x/shop/small", json={"size": 9}, headers=ada)).status_code in (401, 403)
