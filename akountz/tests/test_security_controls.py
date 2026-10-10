"""Per-environment sign-up restrictions, lockout, session limits, and env-wide session and invitation admin."""

import pytest

from pawabase_core.clients import ServiceError

BASE = "/admin/v1/envs/development"


async def _signup(akz, email):
    return await akz.http.post(
        "/auth/v1/signup",
        json={"email": email, "password": "correct-horse-1"},
        headers=akz.headers(),
    )


async def _sign_in(akz, email, password="correct-horse-1"):
    return await akz.http.post(
        "/auth/v1/token",
        json={"grant_type": "password", "email": email, "password": password},
        headers=akz.headers(),
    )


async def test_sign_ups_can_be_limited_by_email_domain(akz):
    akz.api.auth = {
        "allowed_email_domains": ["acme.com"],
        "blocked_email_domains": ["spam.acme.com"],
    }
    assert (await _signup(akz, "ada@example.com")).status_code == 403
    assert (await _signup(akz, "ada@spam.acme.com")).status_code == 403
    assert (await _signup(akz, "ada@acme.com")).status_code == 201
    # An operator can still create anyone.
    made = await akz.admin.post(
        f"{BASE}/users", json={"email": "bob@example.com", "password": "correct-horse-1"}
    )
    assert made["email"] == "bob@example.com"


async def test_lockout_follows_the_environment_setting(akz):
    akz.api.auth = {"lockout_threshold": 2, "lockout_minutes": 5}
    await akz.signup("ada@example.com")
    for _ in range(2):
        assert (await _sign_in(akz, "ada@example.com", "wrong-password-1")).status_code == 400
    assert (await _sign_in(akz, "ada@example.com")).status_code == 429
    locked = await akz.admin.get(f"{BASE}/users", params={"status": "locked"})
    assert [u["email"] for u in locked["data"]] == ["ada@example.com"]
    user_id = locked["data"][0]["id"]
    await akz.admin.patch(f"{BASE}/users/{user_id}", json={"unlock": True})
    assert (await _sign_in(akz, "ada@example.com")).status_code == 200


async def test_the_oldest_session_ends_over_the_limit(akz):
    akz.api.auth = {"max_sessions": 2}
    await akz.signup("ada@example.com")
    assert (await _sign_in(akz, "ada@example.com")).status_code == 200
    assert (await _sign_in(akz, "ada@example.com")).status_code == 200
    sessions = (await akz.admin.get(f"{BASE}/sessions"))["data"]
    assert len(sessions) == 2 and {s["email"] for s in sessions} == {"ada@example.com"}
    await akz.admin.delete(f"{BASE}/sessions/{sessions[0]['id']}")
    assert len((await akz.admin.get(f"{BASE}/sessions"))["data"]) == 1
    with pytest.raises(ServiceError) as gone:
        await akz.admin.delete(f"{BASE}/sessions/{sessions[0]['id']}")
    assert gone.value.status == 404


async def test_pending_invitations_are_listed_and_can_be_revoked(akz):
    owner = await akz.signup("ada@example.com")
    headers = akz.headers(token=owner["access_token"])
    await akz.http.post("/auth/v1/orgs", json={"slug": "acme-inc", "name": "Acme"}, headers=headers)
    sent = await akz.http.post(
        "/auth/v1/orgs/acme-inc/invitations",
        json={"email": "bob@example.com", "role": "member"},
        headers=headers,
    )
    assert sent.status_code == 201
    pending = (await akz.admin.get(f"{BASE}/invitations"))["data"]
    assert [(i["email"], i["org"]) for i in pending] == [("bob@example.com", "acme-inc")]
    await akz.admin.delete(f"{BASE}/invitations/{pending[0]['id']}")
    assert (await akz.admin.get(f"{BASE}/invitations"))["data"] == []


async def test_an_operator_invites_and_resends(akz):
    from pawabase_core.clients import ServiceError

    ada = await akz.signup("ada@example.com")
    await akz.http.post(
        "/auth/v1/orgs",
        json={"slug": "acme-inc", "name": "Acme"},
        headers=akz.headers(token=ada["access_token"]),
    )
    sent = await akz.admin.post(
        f"{BASE}/orgs/acme-inc/invitations", json={"email": "bob@example.com", "role": "admin"}
    )
    assert sent["email"] == "bob@example.com" and sent["role"] == "admin"
    first = akz.api.last_token()
    with pytest.raises(ServiceError) as already:
        await akz.admin.post(f"{BASE}/orgs/acme-inc/invitations", json={"email": "ada@example.com"})
    assert already.value.status == 409
    with pytest.raises(ServiceError) as bad:
        await akz.admin.post(
            f"{BASE}/orgs/acme-inc/invitations", json={"email": "bob@example.com", "role": "king"}
        )
    assert bad.value.status == 422

    await akz.admin.post(f"{BASE}/invitations/{sent['id']}/resend", json={})
    second = akz.api.last_token()
    assert second != first
    bob = await akz.signup("bob@example.com")
    headers = akz.headers(token=bob["access_token"])
    old = await akz.http.post("/auth/v1/invitations/accept", json={"token": first}, headers=headers)
    assert old.status_code == 400
    new = await akz.http.post(
        "/auth/v1/invitations/accept", json={"token": second}, headers=headers
    )
    assert new.status_code == 200 and new.json()["role"] == "admin"
