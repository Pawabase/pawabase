"""Which address a request is believed to come from."""

import pytest

from pawabase_server.edge.clientip import TrustedProxies, chain_for_upstream, parse_address


def scope(peer):
    return {"client": (peer, 5000) if peer else None}


def test_a_direct_client_cannot_choose_its_address():
    trusted = TrustedProxies("private")
    # A public peer is not a proxy: whatever it writes in X-Forwarded-For is ignored.
    assert trusted.client_ip(scope("8.8.8.8"), {"x-forwarded-for": "1.2.3.4"}) == "8.8.8.8"


def test_a_trusted_proxy_vouches_for_the_client_it_saw():
    trusted = TrustedProxies("private")
    assert trusted.client_ip(scope("10.0.0.5"), {"x-forwarded-for": "203.0.113.9"}) == "203.0.113.9"


def test_the_chain_is_read_from_the_right_so_a_forged_prefix_does_not_win():
    trusted = TrustedProxies("10.0.0.0/8")
    # The client sent "6.6.6.6"; the edge proxy appended the address it actually saw (203.0.113.9); our own proxy hop is 10.1.1.1.
    headers = {"x-forwarded-for": "6.6.6.6, 203.0.113.9, 10.1.1.1"}
    assert trusted.client_ip(scope("10.0.0.5"), headers) == "203.0.113.9"


def test_a_garbled_hop_stops_the_walk_instead_of_skipping_to_a_forgery():
    trusted = TrustedProxies("private")
    headers = {"x-forwarded-for": "6.6.6.6, not-an-ip"}
    assert trusted.client_ip(scope("10.0.0.5"), headers) == "6.6.6.6"


def test_the_trust_setting_has_four_forms():
    assert TrustedProxies("*").trusts(parse_address("8.8.8.8"))
    assert not TrustedProxies("").trusts(parse_address("10.0.0.1"))
    only = TrustedProxies("203.0.113.0/24, 2001:db8::/32")
    assert (
        only.trusts(parse_address("203.0.113.7"))
        and only.trusts(parse_address("2001:db8::1"))
        and not only.trusts(parse_address("10.0.0.1"))
    )
    assert TrustedProxies("private").trusts(parse_address("::1"))


def test_no_peer_at_all_is_unknown_not_a_crash():
    assert TrustedProxies().client_ip(scope(None), {}) == "unknown"
    assert TrustedProxies().client_ip(scope("testclient"), {}) == "testclient"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.3.4", "1.2.3.4"),
        (" 1.2.3.4 ", "1.2.3.4"),
        ("1.2.3.4:8080", "1.2.3.4"),
        ("[::1]", "::1"),
        ("::1", "::1"),
        ("nope", None),
        ("", None),
        (None, None),
    ],
)
def test_addresses_are_parsed_leniently(text, expected):
    parsed = parse_address(text)
    assert (str(parsed) if parsed else None) == expected


def test_the_chain_sent_upstream_starts_with_the_verified_client():
    assert chain_for_upstream("1.2.3.4", "10.0.0.5") == "1.2.3.4, 10.0.0.5"
    assert chain_for_upstream("1.2.3.4", "1.2.3.4") == "1.2.3.4"
    assert chain_for_upstream("1.2.3.4", None) == "1.2.3.4"
