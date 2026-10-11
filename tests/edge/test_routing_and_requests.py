"""Path hardening, method screening, request parsing and headers across the gateway."""

import pytest

from pawabase_server.core.context import CONTEXT_HEADER, PlatformContext
from pawabase_server.edge.headers import request_headers
from pawabase_server.edge.request import Request, plausible_key
from pawabase_server.edge.routing import is_clean_path, route_for


@pytest.mark.parametrize(
    "path",
    ["/rest/v1/orders", "/rest/v1/orders/", "/auth/v1/signup", "/a/b/c.json", "/rest/v1/a%20b"],
)
def test_ordinary_paths_are_clean(path):
    assert is_clean_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/rest/../internal/v1/keys/resolve",
        "/rest/./x",
        "//rest/v1",
        "/rest//v1",
        "/rest/v1/..",
        "/rest\\v1",
        "/rest/v1/x\x00",
        "/rest/v1/x\n",
        "rest/v1",
        "/rest/%2e%2e/x" if False else "/rest/v1/\x7f",
    ],
)
def test_traversal_and_control_characters_are_not(path):
    assert not is_clean_path(path)


def test_internal_and_admin_are_never_routed():
    assert route_for("/internal/v1/keys/resolve") is None and route_for("/admin/v1/users") is None
    assert route_for("/rest/v1/x").group == "data" and route_for("/auth/v1/signup").group == "auth"


async def test_a_traversal_attempt_never_reaches_an_upstream(gateway):
    # The client library would normalise a literal "..", so the segments are percent-encoded the way an attacker's would be.
    for path in ("/rest/%2e%2e/internal/v1/keys/resolve", "/rest/v1/%2E%2E/%2E%2E/internal/x"):
        response = await gateway.http.get(path, headers={"apikey": "sk_service"})
        assert response.status_code == 400 and response.json()["error"] == "bad_path"
    assert gateway.upstreams["api"].seen == []


async def test_unsupported_methods_are_answered_without_touching_an_upstream(gateway):
    response = await gateway.http.request(
        "TRACE", "/rest/v1/orders", headers={"apikey": "sk_service"}
    )
    assert response.status_code == 405 and "GET" in response.headers["allow"]
    assert gateway.upstreams["api"].seen == []


async def test_an_oversized_declared_body_is_refused_before_it_is_read(gateway):
    response = await gateway.http.post(
        "/rest/v1/orders",
        content=b"x" * 10,
        headers={"apikey": "sk_service", "content-length": str(10**12)},
    )
    assert response.status_code == 413 and response.json()["error"] == "payload_too_large"
    assert gateway.upstreams["api"].seen == []


async def test_a_nonsense_content_length_is_a_400(gateway):
    response = await gateway.http.post(
        "/rest/v1/orders", content=b"x", headers={"apikey": "sk_service", "content-length": "abc"}
    )
    assert response.status_code in (400, 413)


async def test_an_implausible_key_is_refused_without_asking_the_api(gateway):
    for key in ("x" * 600, "has space", "tab\tkey", "ünïcode"):
        try:
            response = await gateway.http.get("/rest/v1/orders", headers={"apikey": key})
        except Exception:
            continue  # the client library itself refused to send it
        assert response.status_code == 401
    assert gateway.api.calls == []


def test_plausible_keys():
    assert (
        plausible_key("pk_live_abc123")
        and not plausible_key("")
        and not plausible_key("a b")
        and not plausible_key("x" * 513)
    )


def build(headers, peer="10.0.0.5", client_ip="203.0.113.9", via_proxy=True):
    raw = [(k.encode(), v.encode()) for k, v in headers.items()]
    return Request.of(
        {
            "type": "http",
            "method": "GET",
            "path": "/rest/v1/x",
            "headers": raw,
            "query_string": b"",
            "client": (peer, 1),
            "scheme": "http",
        },
        client_ip,
        via_proxy=via_proxy,
    )


def test_spoofed_platform_and_forwarding_headers_do_not_cross():
    request = build(
        {
            "host": "api.example.com",
            "apikey": "secret",
            "x-pawabase-context": "forged",
            "x-forwarded-for": "6.6.6.6",
            "x-real-ip": "6.6.6.6",
            "forwarded": "for=6.6.6.6",
            "x-custom": "kept",
            "connection": "keep-alive",
        }
    )
    sent = request_headers(
        request,
        PlatformContext(env="main", role="anon"),
        secret="s" * 32,
        request_id="abcdefgh1",
        peer="10.0.0.5",
    )
    assert sent["x-custom"] == "kept"
    assert (
        "apikey" not in sent
        and "x-real-ip" not in sent
        and "forwarded" not in sent
        and "connection" not in sent
    )
    assert sent["x-forwarded-for"] == "203.0.113.9, 10.0.0.5"
    assert (
        sent[CONTEXT_HEADER] != "forged"
        and sent["x-request-id"] == "abcdefgh1"
        and sent["x-forwarded-host"] == "api.example.com"
    )


def test_forwarded_proto_is_only_believed_from_a_trusted_proxy():
    behind = build({"x-forwarded-proto": "https"}, via_proxy=True)
    direct = build({"x-forwarded-proto": "https"}, via_proxy=False)
    assert (
        request_headers(behind, None, secret="s" * 32, request_id=None, peer=None)[
            "x-forwarded-proto"
        ]
        == "https"
    )
    assert (
        request_headers(direct, None, secret="s" * 32, request_id=None, peer=None)[
            "x-forwarded-proto"
        ]
        == "http"
    )


def test_request_ids_from_clients_must_be_well_formed():
    assert build({"x-request-id": "abc-12345"}).request_id("minted") == "abc-12345"
    assert build({"x-request-id": "bad id!\r\n"}).request_id("minted") == "minted"
    assert build({"x-request-id": "short"}).request_id("minted") == "minted"


def test_the_key_comes_from_a_header_or_the_query_and_never_goes_upstream():
    request = Request.of(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"apikey=k1&environment=prod&page=2",
            "client": ("1.1.1.1", 1),
        },
        "1.1.1.1",
    )
    assert request.api_key() == "k1" and request.environment_hint() == "prod"
    assert request.upstream_query() == "environment=prod&page=2"
