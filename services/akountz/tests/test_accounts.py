"""Accounts and sessions end to end."""

import pyotp

from pawabase_kit.tokens import verify_user_token


async def test_signup_signin_refresh_and_token_claims(akz):
    body = await akz.signup(name="Ada")
    assert body["user"]["email"] == "ada@example.com" and body["access_token"]
    claims = verify_user_token(
        body["access_token"], akz.settings.jwt_master_secret, project="acme", env="development"
    )
    assert (
        claims["sub"] == body["user"]["id"]
        and claims["email"] == "ada@example.com"
        and claims["sid"] == body["session_id"]
    )
    assert [e["name"] for e in akz.api.events][:2] == ["user.created", "user.signed_in"]

    duplicate = await akz.http.post(
        "/auth/v1/signup",
        json={"email": "ADA@example.com", "password": "correct-horse-1"},
        headers=akz.headers(),
    )
    assert duplicate.status_code == 409
    # The same address is free in another environment of the same project.
    other = await akz.http.post(
        "/auth/v1/signup",
        json={"email": "ada@example.com", "password": "correct-horse-1"},
        headers=akz.headers(env="production"),
    )
    assert other.status_code == 201

    wrong = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "nope"},
        headers=akz.headers(),
    )
    assert wrong.status_code == 400
    signed_in = (
        await akz.http.post(
            "/auth/v1/token",
            json={"email": "ada@example.com", "password": "correct-horse-1"},
            headers=akz.headers(),
        )
    ).json()

    me = await akz.http.get("/auth/v1/user", headers=akz.headers(token=signed_in["access_token"]))
    assert me.status_code == 200 and me.json()["name"] == "Ada"
    # A token from production is useless in development.
    other_token = other.json()["access_token"]
    assert (
        await akz.http.get("/auth/v1/user", headers=akz.headers(token=other_token))
    ).status_code == 401

    refreshed = (
        await akz.http.post(
            "/auth/v1/token",
            json={"grant_type": "refresh_token", "refresh_token": signed_in["refresh_token"]},
            headers=akz.headers(),
        )
    ).json()
    assert (
        refreshed["access_token"] != signed_in["access_token"]
        and refreshed["session_id"] == signed_in["session_id"]
    )
    # Replaying a used refresh token is treated as theft: the whole family is revoked.
    replay = await akz.http.post(
        "/auth/v1/token",
        json={"grant_type": "refresh_token", "refresh_token": signed_in["refresh_token"]},
        headers=akz.headers(),
    )
    assert replay.status_code == 401
    again = await akz.http.post(
        "/auth/v1/token",
        json={"grant_type": "refresh_token", "refresh_token": refreshed["refresh_token"]},
        headers=akz.headers(),
    )
    assert again.status_code == 401


async def test_sessions_logout_and_profile(akz):
    first = await akz.signup()
    second = (
        await akz.http.post(
            "/auth/v1/token",
            json={"email": "ada@example.com", "password": "correct-horse-1"},
            headers=akz.headers(),
        )
    ).json()
    token = second["access_token"]
    sessions = (await akz.http.get("/auth/v1/sessions", headers=akz.headers(token=token))).json()[
        "data"
    ]
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1

    updated = await akz.http.patch(
        "/auth/v1/user",
        json={"name": "Ada L.", "data": {"theme": "dark"}},
        headers=akz.headers(token=token),
    )
    assert updated.json()["user_metadata"] == {"theme": "dark"}
    bad_password = await akz.http.patch(
        "/auth/v1/user",
        json={"password": "another-pass-2", "current_password": "wrong"},
        headers=akz.headers(token=token),
    )
    assert bad_password.status_code == 400

    await akz.http.post(
        "/auth/v1/logout", json={"scope": "others"}, headers=akz.headers(token=token)
    )
    refresh_first = await akz.http.post(
        "/auth/v1/token",
        json={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]},
        headers=akz.headers(),
    )
    assert refresh_first.status_code == 401
    assert (
        len(
            (await akz.http.get("/auth/v1/sessions", headers=akz.headers(token=token))).json()[
                "data"
            ]
        )
        == 1
    )


async def test_lockout(akz):
    await akz.signup()
    for _ in range(akz.settings.lockout_threshold):
        await akz.http.post(
            "/auth/v1/token",
            json={"email": "ada@example.com", "password": "bad"},
            headers=akz.headers(),
        )
    locked = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "correct-horse-1"},
        headers=akz.headers(),
    )
    assert locked.status_code == 429


async def test_verification_recovery_and_magic_links(akz):
    akz.api.auth = {"require_email_verification": True, "site_url": "https://app.example.com"}
    body = await akz.signup()
    assert body["verification_required"] and body["session"] is None
    blocked = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "correct-horse-1"},
        headers=akz.headers(),
    )
    assert blocked.status_code == 403
    assert akz.api.last_link().startswith("https://app.example.com/auth/callback?")
    verified = await akz.http.post(
        "/auth/v1/verify", json={"token": akz.api.last_token()}, headers=akz.headers()
    )
    assert verified.status_code == 200 and verified.json()["user"]["email_verified"]
    reused = await akz.http.post(
        "/auth/v1/verify", json={"token": akz.api.last_token()}, headers=akz.headers()
    )
    assert reused.status_code == 400

    unknown = await akz.http.post(
        "/auth/v1/recover", json={"email": "nobody@example.com"}, headers=akz.headers()
    )
    known = await akz.http.post(
        "/auth/v1/recover", json={"email": "ada@example.com"}, headers=akz.headers()
    )
    assert unknown.json() == known.json()  # no account enumeration
    reset = await akz.http.post(
        "/auth/v1/recover/confirm",
        json={"token": akz.api.last_token(), "password": "brand-new-pass-3"},
        headers=akz.headers(),
    )
    assert reset.status_code == 200
    old = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "correct-horse-1"},
        headers=akz.headers(),
    )
    new = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "brand-new-pass-3"},
        headers=akz.headers(),
    )
    assert old.status_code == 400 and new.status_code == 200

    await akz.http.post(
        "/auth/v1/magic-link", json={"email": "grace@example.com"}, headers=akz.headers()
    )
    magic = await akz.http.post(
        "/auth/v1/magic-link/verify", json={"token": akz.api.last_token()}, headers=akz.headers()
    )
    assert magic.status_code == 200 and magic.json()["user"]["email"] == "grace@example.com"


async def test_keyless_link_page(akz):
    await akz.signup()
    await akz.http.post(
        "/auth/v1/magic-link", json={"email": "ada@example.com"}, headers=akz.headers()
    )
    link = akz.api.last_link()
    assert link.startswith("http://gateway.test/auth/v1/links/acme/development/magic?")
    page = await akz.http.get(link.replace("http://gateway.test", ""))
    assert page.status_code == 200 and "all set" in page.text


async def test_mfa(akz):
    body = await akz.signup()
    token = body["access_token"]
    enrolled = (
        await akz.http.post("/auth/v1/mfa/totp/enroll", headers=akz.headers(token=token))
    ).json()
    totp = pyotp.TOTP(enrolled["secret"])
    bad = await akz.http.post(
        "/auth/v1/mfa/totp/verify", json={"code": "000000"}, headers=akz.headers(token=token)
    )
    assert bad.status_code == 400
    confirmed = (
        await akz.http.post(
            "/auth/v1/mfa/totp/verify", json={"code": totp.now()}, headers=akz.headers(token=token)
        )
    ).json()
    assert confirmed["enabled"] and len(confirmed["recovery_codes"]) == 10

    challenge = (
        await akz.http.post(
            "/auth/v1/token",
            json={"email": "ada@example.com", "password": "correct-horse-1"},
            headers=akz.headers(),
        )
    ).json()
    assert challenge["mfa_required"] and "access_token" not in challenge
    # The TOTP step was already used during enrolment, so fall back to a recovery code.
    code = confirmed["recovery_codes"][0]
    done = await akz.http.post(
        "/auth/v1/token",
        json={"grant_type": "mfa", "mfa_token": challenge["mfa_token"], "code": code},
        headers=akz.headers(),
    )
    assert done.status_code == 200 and done.json()["mfa_method"] == "recovery_code"
    claims = verify_user_token(
        done.json()["access_token"],
        akz.settings.jwt_master_secret,
        project="acme",
        env="development",
    )
    assert claims["aal"] == "aal2"
    # Recovery codes are single use.
    challenge = (
        await akz.http.post(
            "/auth/v1/token",
            json={"email": "ada@example.com", "password": "correct-horse-1"},
            headers=akz.headers(),
        )
    ).json()
    reused = await akz.http.post(
        "/auth/v1/token",
        json={"grant_type": "mfa", "mfa_token": challenge["mfa_token"], "code": code},
        headers=akz.headers(),
    )
    assert reused.status_code == 400
