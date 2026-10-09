import pytest

from app.hosts import normalise, parse


def test_pairs_map_hosts_to_environments():
    assert parse("production=api.example.com, staging=a.example.com|B.Example.com:8443") == {
        "api.example.com": "production",
        "a.example.com": "staging",
        "b.example.com": "staging",
    }
    assert parse("") == {} and parse(" , ") == {}


@pytest.mark.parametrize(
    "bad",
    ["production", "=api.example.com", "production=", "production=a||b", "a=x.com,b=x.com"],
)
def test_malformed_mappings_are_refused_with_the_setting_named(bad):
    with pytest.raises(ValueError, match="PAWABASE_HOSTS"):
        parse(bad)


def test_hosts_compare_without_case_or_port():
    assert normalise("API.Example.com:8080") == "api.example.com"
    assert normalise("[::1]:8080") == "[::1]"


# ── through the proxy ────────────────────────────────────────────────────


async def test_a_key_on_its_own_environments_host_works(gateway):
    gateway.app.state["proxy"].hosts = {"api.shop.example": "main"}
    response = await gateway.http.get(
        "/rest/v1/orders", headers={"apikey": "pk_anon", "host": "api.shop.example:443"}
    )
    assert response.status_code == 200


async def test_a_key_on_another_environments_host_is_refused(gateway):
    gateway.app.state["proxy"].hosts = {"staging.shop.example": "staging"}
    response = await gateway.http.get(
        "/rest/v1/orders", headers={"apikey": "pk_anon", "host": "staging.shop.example"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == "host_environment_mismatch"


async def test_an_unlisted_host_and_an_empty_mapping_change_nothing(gateway):
    for hosts in ({}, {"api.shop.example": "main"}):
        gateway.app.state["proxy"].hosts = hosts
        response = await gateway.http.get(
            "/rest/v1/orders", headers={"apikey": "pk_anon", "host": "somewhere.else.example"}
        )
        assert response.status_code == 200


async def test_the_host_tells_key_resolution_which_environment_to_expect(gateway):
    gateway.app.state["proxy"].hosts = {"api.shop.example": "main"}
    seen = []
    original = gateway.app.state["proxy"].resolver.resolve

    async def spy(raw, *, env=None):
        seen.append(env)
        return await original(raw, env=env)

    gateway.app.state["proxy"].resolver.resolve = spy
    await gateway.http.get(
        "/rest/v1/orders", headers={"apikey": "sk_service", "host": "api.shop.example"}
    )
    assert seen == ["main"]


async def test_an_explicit_environment_still_wins_the_lookup(gateway):
    gateway.app.state["proxy"].hosts = {"api.shop.example": "main"}
    seen = []
    original = gateway.app.state["proxy"].resolver.resolve

    async def spy(raw, *, env=None):
        seen.append(env)
        return await original(raw, env=env)

    gateway.app.state["proxy"].resolver.resolve = spy
    await gateway.http.get(
        "/rest/v1/orders",
        headers={"apikey": "sk_service", "host": "api.shop.example", "x-environment": "main"},
    )
    assert seen == ["main"]
