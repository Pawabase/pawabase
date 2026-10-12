"""Enterprise sign-in: OpenID Connect with discovery, and SAML 2.0.

Both arrive at the same place as a social sign-in: a :class:`FederatedProfile` that the callback turns into a person, a linked
identity and a session. A provider is an entry in the environment's ``providers``::

    "okta":  {"type": "oidc", "issuer": "https://acme.okta.com", "client_id": "...", "client_secret": "secret://OKTA",
              "domains": ["acme.com"]}
    "adfs":  {"type": "saml", "idp_entity_id": "https://adfs.acme.com/adfs/services/trust",
              "idp_sso_url": "https://adfs.acme.com/adfs/ls/", "idp_cert": "-----BEGIN CERTIFICATE-----…",
              "domains": ["acme.com"], "attribute_map": {"email": "mail", "name": "displayName", "department": "dept"}}

``domains`` routes a person by the domain of the email they type on the hosted sign-in page, and refuses an assertion for an address
outside them. SAML needs the optional ``signxml`` package (``pip install pawabase-server[saml]``).
"""

from __future__ import annotations

import base64
import re
import secrets
import time
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit
from xml.sax.saxutils import escape, quoteattr

import httpx

try:  # optional: SAML needs XML signature verification
    from lxml import etree
    from signxml import XMLVerifier
    from signxml.exceptions import InvalidSignature
except ImportError:  # pragma: no cover - exercised only without the extra
    etree = None
    XMLVerifier = None
    InvalidSignature = Exception

from pawabase_server.identity.environment import AuthConfig

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
CLOCK_SKEW = timedelta(seconds=120)

#: Tests substitute an ``httpx`` transport for discovery here.
TRANSPORT: Any = None
_DISCOVERED: dict[str, tuple[float, dict[str, Any]]] = {}
DISCOVERY_TTL = 3600

EMAIL_ATTRIBUTES = (
    "email",
    "mail",
    "emailaddress",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
)
NAME_ATTRIBUTES = (
    "name",
    "displayname",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "urn:oid:2.16.840.1.113730.3.1.241",
)


class FederationError(Exception):
    """A sign-in through an identity provider failed. ``code`` is safe to show, the message is for logs."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclass
class FederatedProfile:
    """What an identity provider said about a person, in the shape the callback understands."""

    subject: str
    email: str | None = None
    email_verified: bool = False
    name: str | None = None
    username: str | None = None
    avatar_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    return_to: str | None = None
    #: Attributes mapped onto the person's ``user_metadata`` (``attribute_map`` entries that are not email, name or username).
    metadata: dict[str, Any] = field(default_factory=dict)


def kind_of(settings: dict[str, Any]) -> str:
    return settings.get("type") or "oauth"


def domain_provider(config: AuthConfig, email: str) -> str | None:
    """The provider that serves this email's domain, if one does: the person is sent straight to their company's sign-in."""
    domain = email.rsplit("@", 1)[-1].strip().lower()
    if not domain or "@" not in email:
        return None
    for name, settings in sorted(config.providers.items()):
        if not settings.get("enabled", True) or name not in config.enabled_providers():
            continue
        if domain in {str(d).lower().lstrip("@") for d in settings.get("domains") or []}:
            return name
    return None


def allowed_domain(settings: dict[str, Any], email: str | None) -> bool:
    domains = {str(d).lower().lstrip("@") for d in settings.get("domains") or []}
    if not domains:
        return True
    return bool(email) and email.rsplit("@", 1)[-1].lower() in domains


# ── OpenID Connect discovery ─────────────────────────────────────────────


def _https(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" or (
        parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")
    )


async def discover(issuer: str) -> dict[str, Any]:
    """The provider's endpoints from ``/.well-known/openid-configuration`` (kept for an hour)."""
    now = time.monotonic()
    hit = _DISCOVERED.get(issuer)
    if hit and hit[0] > now:
        return hit[1]
    if not _https(issuer):
        raise FederationError("provider_misconfigured", "the issuer must be an https address")
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=5, transport=TRANSPORT) as client:
            reply = await client.get(url)
            reply.raise_for_status()
            document = reply.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FederationError("provider_unreachable", str(exc)) from exc
    if str(document.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
        raise FederationError(
            "provider_misconfigured", "the discovery document names another issuer"
        )
    found = {
        key: document.get(name)
        for key, name in (
            ("authorize_endpoint", "authorization_endpoint"),
            ("token_endpoint", "token_endpoint"),
            ("userinfo_endpoint", "userinfo_endpoint"),
        )
    }
    if not all(found.values()) or not all(_https(v) for v in found.values()):
        raise FederationError("provider_misconfigured", "the discovery document is incomplete")
    _DISCOVERED[issuer] = (now + DISCOVERY_TTL, found)
    return found


async def prepare(config: AuthConfig, name: str) -> None:
    """Fill in an OIDC provider's endpoints from its issuer, on this request's copy of the configuration."""
    settings = config.providers.get(name)
    if not settings or kind_of(settings) != "oidc" or not settings.get("issuer"):
        return
    if all(settings.get(k) for k in ("authorize_endpoint", "token_endpoint", "userinfo_endpoint")):
        return
    settings.update(await discover(settings["issuer"]))
    settings.setdefault("scopes", ["openid", "email", "profile"])


# ── SAML 2.0 ─────────────────────────────────────────────────────────────


def saml_available() -> bool:
    return etree is not None and XMLVerifier is not None


def _need_saml() -> None:
    if not saml_available():
        raise FederationError(
            "saml_unavailable", "SAML needs the optional signxml package: pip install signxml"
        )


def sp_entity_id(public_url: str, env: str, name: str) -> str:
    return f"{public_url.rstrip('/')}/auth/v1/saml/{env}/{name}"


def acs_url(public_url: str, env: str, name: str) -> str:
    return f"{sp_entity_id(public_url, env, name)}/acs"


def metadata_xml(public_url: str, env: str, name: str) -> str:
    entity = quoteattr(sp_entity_id(public_url, env, name))
    acs = quoteattr(acs_url(public_url, env, name))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<md:EntityDescriptor xmlns:md="{NS["md"]}" entityID={entity}>'
        '<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        "<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>"
        '<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location={acs} index="0" isDefault="true"/>'
        "</md:SPSSODescriptor></md:EntityDescriptor>"
    )


def authn_request(settings: dict[str, Any], *, entity: str, acs: str) -> tuple[str, str]:
    """An AuthnRequest and its id."""
    request_id = "_" + secrets.token_hex(16)
    instant = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" '
        f'ID="{request_id}" Version="2.0" IssueInstant="{instant}" '
        f"Destination={quoteattr(settings['idp_sso_url'])} "
        'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f"AssertionConsumerServiceURL={quoteattr(acs)}>"
        f"<saml:Issuer>{escape(entity)}</saml:Issuer>"
        '<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/>'
        "</samlp:AuthnRequest>"
    )
    return xml, request_id


def redirect_url(sso_url: str, xml: str, relay_state: str) -> str:
    """The HTTP-Redirect binding: the request deflated, base64 encoded, in the query."""
    deflater = zlib.compressobj(wbits=-15)
    packed = deflater.compress(xml.encode()) + deflater.flush()
    query = urlencode({"SAMLRequest": base64.b64encode(packed).decode(), "RelayState": relay_state})
    joiner = "&" if "?" in sso_url else "?"
    return f"{sso_url}{joiner}{query}"


def _parse(data: bytes) -> Any:
    if re.search(rb"<!DOCTYPE|<!ENTITY", data[:4096], re.I):
        raise FederationError("saml_invalid", "documents with a DTD are refused")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, dtd_validation=False, huge_tree=False
    )
    try:
        return etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        raise FederationError("saml_invalid", "the response is not valid XML") from exc


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _verified(root: Any, cert: str) -> Any:
    """The Response or Assertion element a valid signature covers; nothing outside it may be read."""
    verifier = XMLVerifier()
    pem = (
        cert
        if "BEGIN CERTIFICATE" in cert
        else f"-----BEGIN CERTIFICATE-----\n{cert}\n-----END CERTIFICATE-----"
    )
    for candidate in (root, *root.findall("saml:Assertion", NS)):
        if candidate.find("ds:Signature", NS) is None:
            continue
        try:
            return verifier.verify(candidate, x509_cert=pem, expect_references=1).signed_xml
        except InvalidSignature as exc:
            raise FederationError("saml_signature", str(exc)) from exc
    raise FederationError("saml_unsigned", "the response is not signed")


def parse_response(
    encoded: str,
    settings: dict[str, Any],
    *,
    entity: str,
    acs: str,
    request_id: str,
    now: datetime | None = None,
) -> FederatedProfile:
    """Verify a SAMLResponse and read the person from it. Raises :class:`FederationError`."""
    _need_saml()
    now = now or datetime.now(UTC)
    try:
        data = base64.b64decode(encoded, validate=False)
    except ValueError as exc:
        raise FederationError("saml_invalid", "the response is not base64") from exc
    if len(data) > 1_000_000:
        raise FederationError("saml_invalid", "the response is too large")
    root = _parse(data)
    if root.tag != f"{{{NS['samlp']}}}Response":
        raise FederationError("saml_invalid", "not a SAML response")
    status = root.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or status.get("Value") != SUCCESS:
        raise FederationError("saml_denied", "the identity provider did not sign the person in")
    signed = _verified(root, settings.get("idp_cert", ""))
    # Everything below reads only the element the signature covers.
    if signed.tag == f"{{{NS['samlp']}}}Response":
        assertions = signed.findall("saml:Assertion", NS)
        response = signed
    else:
        assertions = [signed]
        response = root
    if len(assertions) != 1:
        raise FederationError("saml_invalid", "expected exactly one assertion")
    assertion = assertions[0]
    if response.get("Destination") and response.get("Destination") != acs:
        raise FederationError("saml_destination", "the response was meant for another address")
    if response.get("InResponseTo") and response.get("InResponseTo") != request_id:
        raise FederationError("saml_replay", "the response answers another request")
    issuer = assertion.findtext("saml:Issuer", namespaces=NS) or response.findtext(
        "saml:Issuer", namespaces=NS
    )
    if settings.get("idp_entity_id") and issuer != settings["idp_entity_id"]:
        raise FederationError("saml_issuer", f"unexpected issuer {issuer!r}")
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is not None:
        before, after = _time(conditions.get("NotBefore")), _time(conditions.get("NotOnOrAfter"))
        if before and now + CLOCK_SKEW < before:
            raise FederationError("saml_expired", "the assertion is not valid yet")
        if after and now - CLOCK_SKEW >= after:
            raise FederationError("saml_expired", "the assertion has expired")
        audiences = [
            a.text for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS)
        ]
        if audiences and entity not in audiences:
            raise FederationError("saml_audience", "the assertion is for another service provider")
    confirmations = assertion.findall(
        "saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS
    )
    if not confirmations:
        raise FederationError("saml_invalid", "the assertion has no subject confirmation")
    ok = False
    for data_ in confirmations:
        if data_.get("Recipient") and data_.get("Recipient") != acs:
            continue
        if data_.get("InResponseTo") and data_.get("InResponseTo") != request_id:
            continue
        after = _time(data_.get("NotOnOrAfter"))
        if after and now - CLOCK_SKEW >= after:
            continue
        ok = True
    if not ok:
        raise FederationError(
            "saml_invalid", "the subject confirmation does not match this request"
        )
    name_id = assertion.findtext("saml:Subject/saml:NameID", namespaces=NS)
    if not name_id:
        raise FederationError("saml_invalid", "the assertion names no subject")

    attributes: dict[str, str] = {}
    for attribute in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        values = [v.text or "" for v in attribute.findall("saml:AttributeValue", NS)]
        if values:
            for key in (attribute.get("Name"), attribute.get("FriendlyName")):
                if key:
                    attributes[key.lower()] = values[0].strip()

    mapping = {str(k): str(v).lower() for k, v in (settings.get("attribute_map") or {}).items()}

    def pick(target: str, defaults: tuple[str, ...]) -> str | None:
        for key in ([mapping[target]] if target in mapping else []) + list(defaults):
            if attributes.get(key):
                return attributes[key]
        return None

    email = pick("email", EMAIL_ATTRIBUTES)
    if not email and "@" in name_id:
        email = name_id
    metadata = {
        key: attributes[source]
        for key, source in mapping.items()
        if key not in ("email", "name", "username") and attributes.get(source)
    }
    trusted = settings.get("email_verified", True)
    return FederatedProfile(
        subject=name_id,
        email=email.lower() if email else None,
        email_verified=bool(trusted and email),
        name=pick("name", NAME_ATTRIBUTES),
        username=pick("username", ()),
        raw={"issuer": issuer, "attributes": attributes},
        metadata=metadata,
    )
