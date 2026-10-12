import httpx
import pyotp
import pytest

from pawabase_server.core.tokens import verify_user_token
from pawabase_server.identity import accounts as accounts_module
from pawabase_server.identity import user_keys
from pawabase_server.identity.environment import AuthConfig

PASSWORD = "correct-horse-1"


def password_change(akz, token, new, current=PASSWORD):
    return akz.http.patch(
        "/auth/v1/user",
        json={"password": new, "current_password": current},
        headers=akz.headers(token=token),
    )


async def test_password_history_blocks_reuse_and_personal_details(akz):
    akz.api.auth = {"directory": {"passwords": {"history": 2, "forbid_personal": True}}}
    token = (await akz.signup())["access_token"]
    assert (await password_change(akz, token, "second-horse-22")).status_code == 200
    reuse = await password_change(akz, token, PASSWORD, current="second-horse-22")
    assert reuse.status_code == 422 and "reuse" in reuse.text
    personal = await password_change(akz, token, "ada-is-great-77", current="second-horse-22")
    assert personal.status_code == 422 and "name, username or email" in personal.text


async def test_breached_passwords_are_refused_and_an_outage_is_not_a_refusal(akz):
    import hashlib

    digest = hashlib.sha1(b"hunter2-hunter2").hexdigest().upper()  # noqa: S324
    accounts_module.BREACH_TRANSPORT = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=f"{digest[5:]}:9000\r\n0000000000000000000000000000000000A:1"
        )
    )
    try:
        akz.api.auth = {"directory": {"passwords": {"breach_check": True}}}
        refused = await akz.http.post(
            "/auth/v1/signup",
            json={"email": "a@example.com", "password": "hunter2-hunter2"},
            headers=akz.headers(),
        )
        assert refused.status_code == 422 and "breach" in refused.text
        accounts_module.BREACH_TRANSPORT = httpx.MockTransport(lambda request: httpx.Response(503))
        ok = await akz.http.post(
            "/auth/v1/signup",
            json={"email": "a@example.com", "password": "hunter2-hunter2"},
            headers=akz.headers(),
        )
        assert ok.status_code == 201
    finally:
        accounts_module.BREACH_TRANSPORT = None


async def test_an_expired_password_cannot_sign_in_until_it_is_reset(akz):
    from datetime import UTC, datetime, timedelta

    from pawabase_server.db.models import AuthUser

    akz.api.auth = {"directory": {"passwords": {"max_age_days": 30}}}
    await akz.signup()
    await AuthUser.filter(email="ada@example.com").update(
        password_changed_at=datetime.now(UTC) - timedelta(days=45)
    )
    refused = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": PASSWORD},
        headers=akz.headers(),
    )
    assert refused.status_code == 403 and "password_expired" in refused.text
    await akz.http.post(
        "/auth/v1/recover", json={"email": "ada@example.com"}, headers=akz.headers()
    )
    done = await akz.http.post(
        "/auth/v1/recover/confirm",
        json={"token": akz.api.last_token(), "password": "brand-new-pass-9"},
        headers=akz.headers(),
    )
    assert done.status_code == 200
    again = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": "brand-new-pass-9"},
        headers=akz.headers(),
    )
    assert again.status_code == 200
    assert any(m["subject"].endswith("password was changed") for m in akz.api.mail)


async def test_recovery_can_be_turned_off(akz):
    akz.api.auth = {"directory": {"recovery": {"enabled": False}}}
    await akz.signup()
    refused = await akz.http.post(
        "/auth/v1/recover", json={"email": "ada@example.com"}, headers=akz.headers()
    )
    assert refused.status_code == 403


async def test_devices_are_tracked_noticed_and_forgotten(akz):
    akz.api.auth = {"directory": {"devices": {"notify_new": True}}}
    first = await akz.signup()
    assert first["device"]["new"] is True
    token = first["access_token"]
    laptop = {"x-device-id": "laptop-device-id-0000001"}
    signed = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": PASSWORD},
        headers={
            **akz.headers(),
            **laptop,
            "user-agent": "Mozilla/5.0 (Macintosh; Mac OS X) Chrome/120 Safari/537",
        },
    )
    assert signed.status_code == 200 and signed.json()["device"]["new"] is True
    assert any("New sign-in" in m["subject"] for m in akz.api.mail)
    listed = (await akz.http.get("/auth/v1/user/devices", headers=akz.headers(token=token))).json()
    assert len(listed["data"]) == 2
    chrome = next(d for d in listed["data"] if d["label"] == "Chrome on macOS")
    gone = await akz.http.delete(
        f"/auth/v1/user/devices/{chrome['id']}", headers=akz.headers(token=token)
    )
    assert gone.status_code == 204
    left = (await akz.http.get("/auth/v1/user/devices", headers=akz.headers(token=token))).json()
    assert len(left["data"]) == 1


async def test_a_remembered_device_skips_the_second_factor(akz):
    akz.api.auth = {"directory": {"devices": {"remember_days": 30}}}
    body = await akz.signup()
    token = body["access_token"]
    enrolled = (
        await akz.http.post("/auth/v1/mfa/totp/enroll", headers=akz.headers(token=token))
    ).json()
    confirmed = (
        await akz.http.post(
            "/auth/v1/mfa/totp/verify",
            json={"code": pyotp.TOTP(enrolled["secret"]).now()},
            headers=akz.headers(token=token),
        )
    ).json()
    device = {"x-device-id": "phone-device-id-00000001"}

    async def password_sign_in():
        return await akz.http.post(
            "/auth/v1/token",
            json={"email": "ada@example.com", "password": PASSWORD},
            headers={**akz.headers(), **device},
        )

    challenge = (await password_sign_in()).json()
    assert challenge["mfa_required"]
    done = await akz.http.post(
        "/auth/v1/token",
        json={
            "grant_type": "mfa",
            "mfa_token": challenge["mfa_token"],
            "code": confirmed["recovery_codes"][0],
            "remember_device": True,
        },
        headers={**akz.headers(), **device},
    )
    assert done.status_code == 200 and done.json()["device_trusted_days"] == 30
    skipped = (await password_sign_in()).json()
    assert "access_token" in skipped and "mfa_required" not in skipped
    # Another device still has to pass the second factor.
    other = await akz.http.post(
        "/auth/v1/token",
        json={"email": "ada@example.com", "password": PASSWORD},
        headers={**akz.headers(), "x-device-id": "other-device-id-000000001"},
    )
    assert other.json()["mfa_required"]


async def test_user_api_keys_act_as_their_owner_within_the_directory_rules(akz):
    akz.api.auth = {"directory": {"api_keys": {"enabled": True, "max_per_user": 2, "max_days": 30}}}
    token = (await akz.signup())["access_token"]
    headers = akz.headers(token=token)
    too_long = await akz.http.post(
        "/auth/v1/user/api-keys", json={"name": "ci", "expires_in_days": 90}, headers=headers
    )
    assert too_long.status_code == 422
    made = await akz.http.post("/auth/v1/user/api-keys", json={"name": "ci"}, headers=headers)
    assert made.status_code == 201, made.text
    secret = made.json()["key"]
    assert secret.startswith("pbu_") and made.json()["expires_at"]
    listed = (await akz.http.get("/auth/v1/user/api-keys", headers=headers)).json()["data"]
    assert len(listed) == 1 and "key" not in listed[0] and listed[0]["prefix"] == secret[:12]

    config = AuthConfig.from_api("development", {"auth": akz.api.auth})
    minted = await user_keys.mint(
        secret, env="development", config=config, secret=akz.settings.jwt_master_secret
    )
    claims = verify_user_token(minted, akz.settings.jwt_master_secret, env="development")
    assert claims["email"] == "ada@example.com" and claims["sid"].startswith("key:")

    # A key cannot make keys or change the password.
    as_key = akz.headers(token=minted)
    assert (
        await akz.http.post("/auth/v1/user/api-keys", json={"name": "x"}, headers=as_key)
    ).status_code == 403
    assert (await password_change(akz, minted, "another-pass-88")).status_code == 403

    revoked = await akz.http.delete(f"/auth/v1/user/api-keys/{made.json()['id']}", headers=headers)
    assert revoked.status_code == 204
    try:
        await user_keys.mint(
            secret, env="development", config=config, secret=akz.settings.jwt_master_secret
        )
    except user_keys.KeyRefused:
        pass
    else:
        raise AssertionError("a revoked key was accepted")


async def test_user_api_keys_are_off_until_the_directory_turns_them_on(akz):
    token = (await akz.signup())["access_token"]
    refused = await akz.http.post(
        "/auth/v1/user/api-keys", json={"name": "ci"}, headers=akz.headers(token=token)
    )
    assert refused.status_code == 422 and "not turned on" in refused.text


async def test_attributes_are_validated_locked_and_listed_in_tokens(akz):
    akz.api.auth = {
        "directory": {"claims": {"mode": "listed"}},
        "signup_form": {
            "attributes": {"company": "optional"},
            "custom": [
                {"key": "seats", "label": "Seats", "type": "number", "min": 1, "max": 50},
                {
                    "key": "plan",
                    "label": "Plan",
                    "type": "select",
                    "options": ["free", "pro"],
                    "ask": False,
                },
            ],
            "locked": ["plan"],
            "claims": ["plan"],
        },
    }
    page = (await akz.http.get("/auth/v1/hosted/development/signup")).text
    assert "Seats" in page and "Company" in page and ">Plan<" not in page  # not asked at sign-up
    body = await akz.signup(data={"company": "Acme", "plan": "free", "seats": 3})
    token = body["access_token"]
    claims = verify_user_token(token, akz.settings.jwt_master_secret, env="development")
    assert claims["meta"] == {"plan": "free"}  # only the listed attribute rides in the token

    headers = akz.headers(token=token)
    bad = await akz.http.patch("/auth/v1/user", json={"data": {"seats": 500}}, headers=headers)
    assert bad.status_code == 422 and "Seats must be at most 50" in bad.text
    locked = await akz.http.patch("/auth/v1/user", json={"data": {"plan": "pro"}}, headers=headers)
    assert locked.status_code == 422 and "administrator" in locked.text
    ok = await akz.http.patch("/auth/v1/user", json={"data": {"seats": 7}}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["user_metadata"]["seats"] == 7


async def test_an_oidc_provider_is_found_by_discovery_and_routed_by_email_domain(akz):
    from urllib.parse import urlsplit

    from pawabase_server.identity import federation

    document = {
        "issuer": "https://login.acme.test",
        "authorization_endpoint": "https://login.acme.test/authorize",
        "token_endpoint": "https://login.acme.test/token",
        "userinfo_endpoint": "https://login.acme.test/userinfo",
    }
    federation.TRANSPORT = httpx.MockTransport(lambda request: httpx.Response(200, json=document))
    federation._DISCOVERED.clear()
    try:
        akz.api.auth = {
            "site_url": "https://app.test",
            "providers": {
                "acme": {
                    "type": "oidc",
                    "issuer": "https://login.acme.test",
                    "client_id": "cid",
                    "client_secret": "s",
                    "domains": ["acme.com"],
                }
            },
        }
        start = await akz.http.get("/auth/v1/authorize/development/acme")
        assert start.status_code in (302, 307)
        assert urlsplit(start.headers["location"]).netloc == "login.acme.test"
        routed = (
            await akz.http.get(
                "/auth/v1/hosted/development/discover/provider", params={"email": "ada@Acme.com"}
            )
        ).json()
        assert routed["provider"] == "acme" and routed["redirect"].startswith("/auth/v1/authorize/")
        other = (
            await akz.http.get(
                "/auth/v1/hosted/development/discover/provider", params={"email": "ada@gmail.com"}
            )
        ).json()
        assert other["provider"] is None
    finally:
        federation.TRANSPORT = None
        federation._DISCOVERED.clear()


def _identity_provider():
    """A throwaway SAML identity provider: a key, a self-signed certificate, and a way to sign a Response."""
    import base64
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from lxml import etree
    from signxml import XMLSigner, methods

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.acme.test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    def response(*, request_id, acs, audience, email, issuer="https://idp.acme.test", sign=True):
        stamp = lambda delta: (now + timedelta(minutes=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        xml = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
 xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_resp1" Version="2.0"
 IssueInstant="{stamp(0)}" Destination="{acs}" InResponseTo="{request_id}">
<saml:Issuer>{issuer}</saml:Issuer>
<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>
<saml:Assertion ID="_assert1" Version="2.0" IssueInstant="{stamp(0)}">
<saml:Issuer>{issuer}</saml:Issuer>
<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID>
<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
<saml:SubjectConfirmationData Recipient="{acs}" InResponseTo="{request_id}" NotOnOrAfter="{stamp(5)}"/>
</saml:SubjectConfirmation></saml:Subject>
<saml:Conditions NotBefore="{stamp(-1)}" NotOnOrAfter="{stamp(5)}"><saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction></saml:Conditions>
<saml:AttributeStatement>
<saml:Attribute Name="displayName"><saml:AttributeValue>Ada Lovelace</saml:AttributeValue></saml:Attribute>
<saml:Attribute Name="dept"><saml:AttributeValue>Analytical Engines</saml:AttributeValue></saml:Attribute>
</saml:AttributeStatement>
</saml:Assertion></samlp:Response>"""
        root = etree.fromstring(xml.encode())
        if sign:
            signer = XMLSigner(
                method=methods.enveloped,
                signature_algorithm="rsa-sha256",
                digest_algorithm="sha256",
                c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
            )
            root = signer.sign(root, key=key_pem, cert=cert_pem, reference_uri="_resp1")
        return base64.b64encode(etree.tostring(root)).decode()

    return cert_pem.decode(), response


async def test_a_saml_identity_provider_signs_people_in(akz):
    import base64
    import re
    import zlib
    from urllib.parse import parse_qs, urlsplit

    pytest.importorskip("signxml")
    cert, make_response = _identity_provider()
    akz.api.auth = {
        "site_url": "https://app.test",
        "providers": {
            "acme": {
                "type": "saml",
                "idp_entity_id": "https://idp.acme.test",
                "idp_sso_url": "https://idp.acme.test/sso",
                "idp_cert": cert,
                "domains": ["acme.com"],
                "attribute_map": {"name": "displayName", "department": "dept"},
            }
        },
    }
    base = "http://gateway.test/auth/v1/saml/development/acme"
    meta = await akz.http.get("/auth/v1/saml/development/acme/metadata")
    assert (
        meta.status_code == 200 and f'entityID="{base}"' in meta.text and f"{base}/acs" in meta.text
    )

    async def start():
        redirect = await akz.http.get(
            "/auth/v1/authorize/development/acme", params={"redirect_to": "https://app.test/cb"}
        )
        assert redirect.status_code in (302, 307)
        query = parse_qs(urlsplit(redirect.headers["location"]).query)
        xml = zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), -15).decode()
        return re.search(r'ID="([^"]+)"', xml).group(1), query["RelayState"][0]

    request_id, relay = await start()
    reply = make_response(
        request_id=request_id, acs=f"{base}/acs", audience=base, email="ada@acme.com"
    )
    done = await akz.http.post(
        "/auth/v1/saml/development/acme/acs", data={"SAMLResponse": reply, "RelayState": relay}
    )
    assert done.status_code in (302, 307), done.text
    assert "access_token=" in done.headers["location"]
    users = await akz.admin.get("/admin/v1/envs/development/users")
    ada = next(u for u in users["data"] if u["email"] == "ada@acme.com")
    assert (
        ada["name"] == "Ada Lovelace" and ada["user_metadata"]["department"] == "Analytical Engines"
    )
    assert ada["email_verified"]

    # The same state cannot be used twice, and a tampered or foreign response is refused.
    replay = await akz.http.post(
        "/auth/v1/saml/development/acme/acs", data={"SAMLResponse": reply, "RelayState": relay}
    )
    assert "saml_state" in replay.headers["location"]
    request_id, relay = await start()
    forged = make_response(
        request_id=request_id, acs=f"{base}/acs", audience=base, email="ada@acme.com"
    )
    forged = base64.b64encode(
        base64.b64decode(forged).replace(b"ada@acme.com", b"ceo@acme.com")
    ).decode()
    refused = await akz.http.post(
        "/auth/v1/saml/development/acme/acs", data={"SAMLResponse": forged, "RelayState": relay}
    )
    assert "saml_signature" in refused.headers["location"]
    request_id, relay = await start()
    unsigned = make_response(
        request_id=request_id, acs=f"{base}/acs", audience=base, email="ada@acme.com", sign=False
    )
    refused = await akz.http.post(
        "/auth/v1/saml/development/acme/acs", data={"SAMLResponse": unsigned, "RelayState": relay}
    )
    assert "saml_unsigned" in refused.headers["location"]
    request_id, relay = await start()
    outsider = make_response(
        request_id=request_id, acs=f"{base}/acs", audience=base, email="mallory@evil.test"
    )
    refused = await akz.http.post(
        "/auth/v1/saml/development/acme/acs", data={"SAMLResponse": outsider, "RelayState": relay}
    )
    assert "email_domain_not_allowed" in refused.headers["location"]
