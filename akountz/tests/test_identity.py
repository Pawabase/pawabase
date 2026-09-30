"""Social sign-in, organizations, roles, administration and platform operators."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from pawabase_kit.clients import ServiceError
from pawabase_kit.settings import PLATFORM_ENV, PLATFORM_PROJECT
from pawabase_kit.tokens import peek_claims, verify_user_token


@pytest.fixture
def google(akz):
    from app import oauth

    def provider(request: httpx.Request) -> httpx.Response:
        if "token" in str(request.url):
            return httpx.Response(
                200, json={"access_token": "provider-token", "token_type": "bearer"}
            )
        return httpx.Response(
            200,
            json={
                "sub": "g-123",
                "email": "grace@example.com",
                "email_verified": True,
                "name": "Grace",
            },
        )

    oauth.TRANSPORT = httpx.MockTransport(provider)
    akz.api.auth = {
        "providers": {"google": {"client_id": "cid", "client_secret": "csecret"}},
        "site_url": "https://app.example.com",
    }
    yield akz
    oauth.TRANSPORT = None


async def test_google_sign_in_creates_and_reuses_an_account(google):
    akz = google
    start = await akz.http.get(
        "/auth/v1/authorize/acme/development/google?redirect_to=https://app.example.com/after"
    )
    assert start.status_code in (302, 307), start.text
    location = urlparse(start.headers["location"])
    assert location.netloc == "accounts.google.com"
    state = parse_qs(location.query)["state"][0]
    cookie = start.cookies
    finished = await akz.http.get(
        f"/auth/v1/callback/acme/development/google?code=abc&state={state}", cookies=cookie
    )
    assert finished.status_code in (302, 307), finished.text
    fragment = parse_qs(urlparse(finished.headers["location"]).fragment)
    assert finished.headers["location"].startswith("https://app.example.com/after#")
    claims = verify_user_token(
        fragment["access_token"][0],
        akz.settings.jwt_master_secret,
        project="acme",
        env="development",
    )
    me = (
        await akz.http.get("/auth/v1/user", headers=akz.headers(token=fragment["access_token"][0]))
    ).json()
    assert (
        me["email"] == "grace@example.com"
        and me["email_verified"]
        and me["identities"][0]["provider"] == "google"
    )
    assert claims["sub"] == me["id"]

    # A tampered state is refused, and redirect targets outside the allowlist are refused.
    bad = await akz.http.get(
        f"/auth/v1/callback/acme/development/google?code=abc&state={state}x", cookies=cookie
    )
    assert "error=state_mismatch" in bad.headers.get("location", "")
    evil = await akz.http.get(
        "/auth/v1/authorize/acme/development/google?redirect_to=https://evil.example.com"
    )
    assert evil.status_code == 400
    disabled = await akz.http.get("/auth/v1/authorize/acme/development/github")
    assert disabled.status_code == 404


async def test_organizations_and_invitations(akz):
    owner = await akz.signup("ada@example.com")
    guest = await akz.signup("bob@example.com")
    owner_h = akz.headers(token=owner["access_token"])
    guest_h = akz.headers(token=guest["access_token"])
    org = await akz.http.post(
        "/auth/v1/orgs", json={"slug": "acme-inc", "name": "Acme Inc"}, headers=owner_h
    )
    assert org.status_code == 201 and org.json()["role"] == "owner"
    assert (await akz.http.get("/auth/v1/orgs/acme-inc", headers=guest_h)).status_code == 404

    invite = await akz.http.post(
        "/auth/v1/orgs/acme-inc/invitations",
        json={"email": "bob@example.com", "role": "admin"},
        headers=owner_h,
    )
    assert invite.status_code == 201
    token = akz.api.last_token()
    assert (
        await akz.http.post("/auth/v1/invitations/accept", json={"token": token}, headers=owner_h)
    ).status_code == 403
    accepted = await akz.http.post(
        "/auth/v1/invitations/accept", json={"token": token}, headers=guest_h
    )
    assert accepted.status_code == 200 and accepted.json()["role"] == "admin"
    members = (await akz.http.get("/auth/v1/orgs/acme-inc/members", headers=guest_h)).json()["data"]
    assert {m["email"]: m["role"] for m in members} == {
        "ada@example.com": "owner",
        "bob@example.com": "admin",
    }
    # An admin cannot demote the only owner.
    ada_id = owner["user"]["id"]
    assert (
        await akz.http.put(
            f"/auth/v1/orgs/acme-inc/members/{ada_id}", json={"role": "member"}, headers=guest_h
        )
    ).status_code == 403
    team = await akz.http.post(
        "/auth/v1/orgs/acme-inc/teams", json={"slug": "core", "name": "Core"}, headers=guest_h
    )
    assert team.status_code == 201


async def test_admin_roles_and_claims(akz):
    body = await akz.signup()
    user_id = body["user"]["id"]
    await akz.admin.put(
        "/admin/v1/projects/acme/envs/development/roles",
        json={
            "name": "editor",
            "description": "Edits posts",
            "permissions": ["posts.write", "posts.publish"],
        },
    )
    updated = await akz.admin.patch(
        f"/admin/v1/projects/acme/envs/development/users/{user_id}",
        json={"roles": ["editor"], "app_metadata": {"plan": "pro"}},
    )
    assert updated["roles"] == ["editor"] and sorted(updated["permissions"]) == [
        "posts.publish",
        "posts.write",
    ]
    signed_in = (
        await akz.http.post(
            "/auth/v1/token",
            json={"email": "ada@example.com", "password": "correct-horse-1"},
            headers=akz.headers(),
        )
    ).json()
    claims = verify_user_token(
        signed_in["access_token"], akz.settings.jwt_master_secret, project="acme", env="development"
    )
    assert claims["roles"] == ["editor"] and "posts.write" in claims["perms"]
    # Roles are per environment.
    prod_roles = await akz.admin.get("/admin/v1/projects/acme/envs/production/roles")
    assert prod_roles["data"] == []

    listing = await akz.admin.get("/admin/v1/projects/acme/envs/development/users?search=ada")
    assert listing["total"] == 1 and listing["data"][0]["app_metadata"] == {"plan": "pro"}
    await akz.admin.patch(
        f"/admin/v1/projects/acme/envs/development/users/{user_id}", json={"disabled": True}
    )
    refused = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "correct-horse-1"},
        headers=akz.headers(),
    )
    assert refused.status_code == 403
    stats = await akz.admin.get("/admin/v1/projects/acme/envs/development/stats")
    assert stats["users"] == 1 and stats["disabled"] == 1
    events = await akz.admin.get("/admin/v1/projects/acme/envs/development/events?failed=true")
    assert events["data"][0]["reason"] == "disabled"

    await akz.admin.delete(f"/admin/v1/projects/acme/envs/development/users/{user_id}")
    again = await akz.http.post(
        "/auth/v1/signup",
        json={"email": "ada@example.com", "password": "correct-horse-1"},
        headers=akz.headers(),
    )
    assert again.status_code == 201  # the address was released


async def test_admin_requires_a_service_token(akz):
    body = await akz.signup()
    response = await akz.http.get(
        "/admin/v1/projects/acme/envs/development/users",
        headers=akz.headers(token=body["access_token"]),
    )
    assert response.status_code == 401
    with pytest.raises(ServiceError) as missing:
        await akz.admin.get("/admin/v1/projects/nope/envs/development/users/1")
    assert missing.value.status == 404


async def test_platform_operator_bootstrap(akz):
    headers = akz.headers(project=PLATFORM_PROJECT, env=PLATFORM_ENV)
    signed_in = await akz.http.post(
        "/auth/v1/token",
        json={"email": "root@pawabase.dev", "password": "Sup3r-secret!pass"},
        headers=headers,
    )
    assert signed_in.status_code == 200
    claims = verify_user_token(
        signed_in.json()["access_token"],
        akz.settings.jwt_master_secret,
        project=PLATFORM_PROJECT,
        env=PLATFORM_ENV,
    )
    assert claims["roles"] == ["admin"]
    signup = await akz.http.post(
        "/auth/v1/signup",
        json={"email": "x@example.com", "password": "Another-pass1!"},
        headers=headers,
    )
    assert signup.status_code == 403  # nobody signs up to operate the platform


async def test_organization_scoped_tokens_and_app_claims(akz):
    owner = await akz.signup("ada@example.com")
    await akz.signup("bob@example.com")
    owner_h = akz.headers(token=owner["access_token"])
    for slug in ("acme-inc", "globex"):
        created = await akz.http.post(
            "/auth/v1/orgs", json={"slug": slug, "name": slug.title()}, headers=owner_h
        )
        assert created.status_code == 201, created.text

    def sign_in(email, **extra):
        return akz.http.post(
            "/auth/v1/token",
            json={"email": email, "password": "correct-horse-1", **extra},
            headers=akz.headers(),
        )

    # Signing in for an organization puts it, and the caller's role there, in the token.
    scoped = (await sign_in("ada@example.com", org="acme-inc")).json()
    assert scoped["org"] == "acme-inc" and scoped["org_role"] == "owner"
    claims = peek_claims(scoped["access_token"])
    assert claims["org"] == "acme-inc" and claims["org_role"] == "owner"

    # Refreshing keeps it; naming another org switches; null leaves every org.
    kept = (await akz.http.post("/auth/v1/token", json={"grant_type": "refresh_token", "refresh_token": scoped["refresh_token"]}, headers=akz.headers())).json()
    assert kept["org"] == "acme-inc"
    switched = (await akz.http.post("/auth/v1/token", json={"grant_type": "refresh_token", "refresh_token": kept["refresh_token"], "org": "globex"}, headers=akz.headers())).json()
    assert switched["org"] == "globex" and peek_claims(switched["access_token"])["org"] == "globex"
    left = (await akz.http.post("/auth/v1/token", json={"grant_type": "refresh_token", "refresh_token": switched["refresh_token"], "org": None}, headers=akz.headers())).json()
    assert left["org"] is None and peek_claims(left["access_token"])["org"] is None

    # Not a member: refused, on sign-in and on switch.
    assert (await sign_in("bob@example.com", org="acme-inc")).status_code == 403
    bob = (await sign_in("bob@example.com")).json()
    refused = await akz.http.post("/auth/v1/token", json={"grant_type": "refresh_token", "refresh_token": bob["refresh_token"], "org": "acme-inc"}, headers=akz.headers())
    assert refused.status_code == 403

    # A role change in the org applies on the next refresh.
    invite = await akz.http.post("/auth/v1/orgs/acme-inc/invitations", json={"email": "bob@example.com", "role": "viewer"}, headers=owner_h)
    assert invite.status_code == 201, invite.text
    accepted = await akz.http.post("/auth/v1/invitations/accept", json={"token": akz.api.last_token()}, headers=akz.headers(token=bob["access_token"]))
    assert accepted.status_code in (200, 201), accepted.text
    bob_scoped = (await sign_in("bob@example.com", org="acme-inc")).json()
    assert bob_scoped["org_role"] == "viewer"
    bob_id = bob_scoped["user"]["id"]
    await akz.http.put(f"/auth/v1/orgs/acme-inc/members/{bob_id}", json={"role": "admin"}, headers=owner_h)
    promoted = (await akz.http.post("/auth/v1/token", json={"grant_type": "refresh_token", "refresh_token": bob_scoped["refresh_token"]}, headers=akz.headers())).json()
    assert promoted["org_role"] == "admin"

    # app_metadata is admin-controlled and becomes the "app" claim; user_metadata stays "meta".
    await akz.admin.patch(
        f"/admin/v1/projects/acme/envs/development/users/{owner['user']['id']}",
        json={"app_metadata": {"family_id": "f-12"}},
    )
    await akz.http.patch("/auth/v1/user", json={"data": {"family_id": "forged"}}, headers=owner_h)
    fresh = peek_claims((await sign_in("ada@example.com")).json()["access_token"])
    assert fresh["app"] == {"family_id": "f-12"}
    assert fresh["meta"]["family_id"] == "forged"
