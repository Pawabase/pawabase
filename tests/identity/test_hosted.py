"""The hosted sign-in pages: branding, the redirect flow, and the code exchange."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest

from pawabase_server.identity import hosted, signup_form

APP = "https://app.example.com"


@pytest.fixture
def branded(akz):
    akz.api.auth = {
        "site_url": APP,
        "branding": {
            "name": "Acme Cloud",
            "primary": "#e11d48",
            "login_title": "Hello again",
            "radius": 8,
            "terms_url": "https://acme.test/terms",
        },
    }
    return akz


def test_branding_keeps_only_safe_tokens():
    assert hosted.clean_branding(
        {
            "primary": "red",
            "radius": 99,
            "font": "comic",
            "logo_url": "javascript:alert(1)",
            "name": "Acme",
        }
    ) == {"name": "Acme"}
    with pytest.raises(ValueError) as problems:
        hosted.clean_branding({"primary": "red", "logo_url": "http://x.test/a.png"}, strict=True)
    assert "primary" in str(problems.value) and "logo_url" in str(problems.value)


async def test_the_page_is_served_without_a_key_and_wears_the_branding(branded):
    page = await branded.http.get("/auth/v1/hosted/development/login")
    assert page.status_code == 200
    assert "Hello again" in page.text and "#e11d48" in page.text and "Acme Cloud" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert (await branded.http.get("/auth/v1/hosted/development/nonsense")).status_code == 404
    assert (await branded.http.get("/auth/v1/hosted/elsewhere/login")).status_code == 404


async def test_markup_in_branding_is_escaped(akz):
    akz.api.auth = {
        "branding": {
            "name": "<script>alert(1)</script>",
            "login_title": "<img src=x onerror=alert(1)>",
        }
    }
    page = (await akz.http.get("/auth/v1/hosted/development/login")).text
    assert "<script>alert(1)" not in page and "<img src=x" not in page and "&lt;script&gt;" in page


async def test_a_page_will_not_serve_a_redirect_that_is_not_allowed(branded):
    refused = await branded.http.get(
        "/auth/v1/hosted/development/login", params={"redirect_to": "https://evil.test/x"}
    )
    assert refused.status_code == 400


async def test_sign_in_returns_a_code_the_app_exchanges_once(branded):
    await branded.signup()
    verifier = "a-long-random-verifier-0123456789"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    done = await branded.http.post(
        "/auth/v1/hosted/development/signin",
        json={
            "email": "ada@example.com",
            "password": "correct-horse-1",
            "redirect_to": f"{APP}/callback",
            "state": "xyz",
            "code_challenge": challenge,
        },
    )
    assert done.status_code == 200, done.text
    target = urlparse(done.json()["redirect"])
    assert target.netloc == "app.example.com" and parse_qs(target.query)["state"] == ["xyz"]
    code = parse_qs(target.query)["code"][0]
    assert "access_token" not in done.text  # tokens never travel in the URL

    wrong = await branded.http.post(
        "/auth/v1/token/exchange",
        json={"code": code, "code_verifier": "not-it"},
        headers=branded.headers(),
    )
    assert wrong.status_code == 400
    # The wrong verifier spent the code: a thief who has the code but not the verifier gets nothing, and neither does a retry.
    again = await branded.http.post(
        "/auth/v1/token/exchange",
        json={"code": code, "code_verifier": verifier},
        headers=branded.headers(),
    )
    assert again.status_code == 400


async def test_a_good_exchange_gives_a_session(branded):
    await branded.signup()
    verifier = "another-long-random-verifier-98765"
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    done = await branded.http.post(
        "/auth/v1/hosted/development/signin",
        json={
            "email": "ada@example.com",
            "password": "correct-horse-1",
            "redirect_to": APP,
            "code_challenge": challenge,
        },
    )
    code = parse_qs(urlparse(done.json()["redirect"]).query)["code"][0]
    session = await branded.http.post(
        "/auth/v1/token/exchange",
        json={"code": code, "code_verifier": verifier},
        headers=branded.headers(),
    )
    assert session.status_code == 200, session.text
    body = session.json()
    assert body["access_token"] and body["user"]["email"] == "ada@example.com"
    replay = await branded.http.post(
        "/auth/v1/token/exchange",
        json={"code": code, "code_verifier": verifier},
        headers=branded.headers(),
    )
    assert replay.status_code == 400


async def test_wrong_passwords_and_closed_redirects_are_refused(branded):
    await branded.signup()
    bad = await branded.http.post(
        "/auth/v1/hosted/development/signin",
        json={"email": "ada@example.com", "password": "nope-nope-1", "redirect_to": APP},
    )
    assert bad.status_code == 400
    elsewhere = await branded.http.post(
        "/auth/v1/hosted/development/signin",
        json={
            "email": "ada@example.com",
            "password": "correct-horse-1",
            "redirect_to": "https://evil.test",
        },
    )
    assert elsewhere.status_code == 400


async def test_the_preview_shows_unsaved_branding(akz):
    raw = (
        base64.urlsafe_b64encode(json.dumps({"name": "Draft Co", "primary": "#0a7d4f"}).encode())
        .decode()
        .rstrip("=")
    )
    page = await akz.http.get("/auth/v1/hosted/development/preview/login", params={"b": raw})
    assert "Draft Co" in page.text and "#0a7d4f" in page.text and "Preview" in page.text


async def test_signup_asks_the_configured_questions_and_keeps_the_answers(akz):
    akz.api.auth = {
        "site_url": APP,
        "branding": {
            "terms_url": "https://acme.test/terms",
            "signup_fields": [
                {"key": "name", "label": "Full name", "type": "text", "required": True},
                {"key": "company", "label": "Company", "type": "text"},
                {
                    "key": "team_size",
                    "label": "Team size",
                    "type": "select",
                    "options": ["1-5", "6-20", "21+"],
                    "required": True,
                },
                {"key": "updates", "label": "Send me product news", "type": "checkbox"},
            ],
        },
    }
    page = (await akz.http.get("/auth/v1/hosted/development/signup")).text
    for label in ("Full name", "Company", "Team size", "Send me product news", "I agree to the"):
        assert label in page

    async def go(**extra):
        body = {
            "email": "ada@example.com",
            "password": "correct-horse-1",
            "redirect_to": APP,
            "accepted_terms": True,
            **extra,
        }
        return await akz.http.post("/auth/v1/hosted/development/signup", json=body)

    missing = await go(fields={"company": "Acme"})
    assert missing.status_code == 422 and "Full name is required" in missing.text
    no_terms = await go(fields={"name": "Ada Lovelace", "team_size": "1-5"}, accepted_terms=False)
    assert no_terms.status_code == 422 and "accept the terms" in no_terms.text
    wrong = await go(fields={"name": "Ada", "team_size": "500"})
    assert wrong.status_code == 422

    created = await go(
        fields={
            "name": "Ada Lovelace",
            "company": "Acme",
            "team_size": "6-20",
            "updates": True,
            "sneaky": "x",
        }
    )
    assert created.status_code == 200, created.text
    assert "redirect" in created.json()
    users = await akz.admin.get("/admin/v1/envs/development/users")
    ada = next(u for u in users["data"] if u["email"] == "ada@example.com")
    assert ada["name"] == "Ada Lovelace"
    assert ada["user_metadata"] == {
        "company": "Acme",
        "team_size": "6-20",
        "updates": True,
    }


async def test_a_recovery_link_opens_the_branded_reset_page_and_sets_the_password(branded):
    await branded.signup()
    from pawabase_server.identity import links

    await branded.http.post(
        "/auth/v1/recover", json={"email": "ada@example.com"}, headers=branded.headers()
    )
    token = branded.api.last_token()
    opened = await branded.http.get("/auth/v1/links/development/recovery", params={"token": token})
    assert (
        opened.status_code in (302, 307)
        and "/hosted/development/reset" in opened.headers["location"]
    )
    page = await branded.http.get(opened.headers["location"])
    assert "Choose a new password" in page.text and "Acme Cloud" in page.text
    done = await branded.http.post(
        "/auth/v1/hosted/development/reset", json={"token": token, "password": "a-brand-new-pass-9"}
    )
    assert done.status_code == 200 and done.json()["changed"] is True
    signed = await branded.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "a-brand-new-pass-9"},
        headers=branded.headers(),
    )
    assert signed.status_code == 200
    del links


def test_signup_fields_are_validated_when_branding_is_saved():
    with pytest.raises(ValueError) as problems:
        hosted.clean_branding(
            {
                "signup_fields": [
                    {"key": "Bad Key", "label": "x"},
                    {"key": "size", "label": "Size", "type": "select"},
                ]
            },
            strict=True,
        )
    assert "Bad Key" in str(problems.value) and "needs at least one option" in str(problems.value)


FORM = {
    "sign_in_with": ["email", "username", "phone"],
    "attributes": {
        "given_name": "required",
        "family_name": "required",
        "birthdate": "optional",
        "country": "optional",
    },
    "custom": [
        {
            "key": "seats",
            "label": "Seats",
            "type": "number",
            "min": 1,
            "max": 500,
            "required": True,
        },
        {"key": "site", "label": "Site", "type": "url"},
    ],
}


async def test_a_cognito_style_form_picks_attributes_identifiers_and_rules(akz):
    akz.api.auth = {
        "site_url": APP,
        "signup_form": FORM,
        "password_rules": {"upper": True, "digit": True},
    }
    page = (await akz.http.get("/auth/v1/hosted/development/signup")).text
    for expected in (
        "First name",
        "Last name",
        "Seats",
        "Date of birth",
        "Username",
        "Phone number",
        "An uppercase letter",
        "data-step",
    ):
        assert expected in page, expected
    login = (await akz.http.get("/auth/v1/hosted/development/login")).text
    assert "Email, username or phone" in login

    async def go(**fields):
        return await akz.http.post(
            "/auth/v1/hosted/development/signup",
            json={
                "email": "grace@example.com",
                "password": "Password-1",
                "redirect_to": APP,
                "fields": fields,
            },
        )

    assert (await go(given_name="Grace")).status_code == 422
    bad = await go(
        given_name="Grace",
        family_name="Hopper",
        username="grace",
        phone="+234 801 234 5678",
        seats="0",
    )
    assert bad.status_code == 422 and "Seats must be at least 1" in bad.text
    weak = await akz.http.post(
        "/auth/v1/hosted/development/signup",
        json={
            "email": "g@example.com",
            "password": "alllowercase",
            "redirect_to": APP,
            "fields": {
                "given_name": "G",
                "family_name": "H",
                "username": "gh1",
                "phone": "+2348012345678",
                "seats": "3",
            },
        },
    )
    assert weak.status_code == 422 and "uppercase" in weak.text
    ok = await go(
        given_name="Grace",
        family_name="Hopper",
        username="Grace",
        phone="+234 801 234 5678",
        seats="12",
        birthdate="1906-12-09",
        country="Nigeria",
        site="https://navy.test",
    )
    assert ok.status_code == 200, ok.text
    users = await akz.admin.get("/admin/v1/envs/development/users")
    grace = next(u for u in users["data"] if u["email"] == "grace@example.com")
    assert grace["name"] == "Grace Hopper" and grace["username"] == "grace"
    assert grace["user_metadata"]["seats"] == 12 and grace["user_metadata"]["country"] == "Nigeria"

    for who in ("grace@example.com", "grace", "+2348012345678"):
        signed = await akz.http.post(
            "/auth/v1/hosted/development/signin",
            json={"email": who, "password": "Password-1", "redirect_to": APP},
        )
        assert signed.status_code == 200 and "redirect" in signed.json(), who


async def test_a_form_is_validated_before_it_is_stored():
    with pytest.raises(ValueError):
        signup_form.clean({"attributes": {"shoe_size": "required"}}, strict=True)
    with pytest.raises(ValueError):
        signup_form.clean({"sign_in_with": ["username"]}, strict=True)
    cleaned = signup_form.clean({"sign_in_with": ["email", "phone"]}, strict=True)
    assert cleaned["attributes"]["phone"] == "required"


async def test_every_screen_can_be_previewed_and_its_source_read(akz):
    akz.api.auth = {
        "branding": {"name": "Acme", "login_button": "Let me in", "forgot_title": "Lost it?"}
    }
    for page, expected in (
        ("login", "Let me in"),
        ("signup", "Create account"),
        ("forgot", "Lost it?"),
        ("reset", "Choose a new password"),
        ("mfa", "Two-step verification"),
        ("check-email", "Check your email"),
        ("signed-in", "all set"),
        ("error", "not allowed"),
    ):
        response = await akz.http.get(f"/auth/v1/hosted/development/preview/{page}")
        assert response.status_code == 200 and expected in response.text, page
    assert (
        await akz.http.get("/auth/v1/hosted/development/mfa")
    ).status_code == 404  # preview only
    source = await akz.admin.post(
        "/admin/v1/envs/development/hosted-source",
        json={"page": "login", "branding": {"name": "Acme", "primary": "#0f766e"}},
    )
    assert source["live"] is True and "#0f766e" in source["html"] and "<form" in source["html"]


async def test_custom_css_may_style_but_not_load_or_escape(akz):
    from pawabase_server.identity import hosted

    assert hosted.clean_branding({"custom_css": ".primary{border-radius:99px}"}) == {
        "custom_css": ".primary{border-radius:99px}"
    }
    for bad in (
        "@import 'https://evil.test/x.css';",
        "a{background:url(https://evil.test/x)}",
        "</style><script>alert(1)</script>",
        "a{color:red",
        "a{content:\\22 x}",
    ):
        assert hosted.clean_branding({"custom_css": bad}) == {}
        with pytest.raises(ValueError):
            hosted.clean_branding({"custom_css": bad}, strict=True)
    akz.api.auth = {"branding": {"custom_css": ".primary{letter-spacing:.2em}"}}
    page = (await akz.http.get("/auth/v1/hosted/development/login")).text
    assert ".primary{letter-spacing:.2em}" in page
