from pawabase_server.core.context import CONTEXT_HEADER
from pawabase_server.core.tokens import verify_context_token
from pawabase_server.edge import policy


async def test_proxies_with_signed_context(gateway):
    response = await gateway.http.get(
        "/rest/v1/orders?limit=2&apikey=pk_anon",
        headers={"x-pawabase-context": "forged", "x-pawabase-service": "forged"},
    )
    assert response.status_code == 200
    seen = response.json()
    assert seen["path"] == "/rest/v1/orders"
    assert seen["query"] == "limit=2"
    headers = seen["headers"]
    assert "apikey" not in headers and "x-pawabase-service" not in headers
    context = verify_context_token(headers[CONTEXT_HEADER], gateway.settings.internal_secret)
    assert (context.env, context.role, context.key_id) == (
        "main",
        "anon",
        "k1",
    )
    assert headers["x-request-id"]

    v2 = await gateway.http.get("/rest/v2/orders?apikey=pk_anon")
    assert v2.status_code == 200 and v2.json()["path"] == "/rest/v2/orders"


async def test_every_public_prefix_reaches_the_application(gateway):
    for path in (
        "/auth/v1/token",
        "/realtime/v1/publish",
        "/storage/v1/object/b/x",
        "/functions/v1/hello",
    ):
        response = await gateway.http.post(path, json={"a": 1}, headers={"apikey": "sk_service"})
        assert response.status_code == 200, path
        assert response.json()["path"] == path
        assert response.json()["body"] == '{"a":1}' or response.json()["body"] == '{"a": 1}'


async def test_keys_are_required_and_cached(gateway):
    assert (await gateway.http.get("/rest/v1/orders")).status_code == 401
    assert (
        await gateway.http.get("/rest/v1/orders", headers={"apikey": "nope"})
    ).status_code == 401
    assert (
        await gateway.http.get("/rest/v1/orders", headers={"apikey": "nope"})
    ).status_code == 401
    for _ in range(3):
        assert (
            await gateway.http.get("/rest/v1/orders", headers={"x-api-key": "sk_service"})
        ).status_code == 200
    assert gateway.api.calls.count("nope") == 1
    assert gateway.api.calls.count("sk_service") == 1


async def test_keyless_routes_and_blocked_paths(gateway):
    response = await gateway.http.get("/auth/v1/authorize/shop/main/github")
    assert response.status_code == 200
    assert CONTEXT_HEADER not in response.json()["headers"]
    assert (await gateway.http.get("/hooks/v1/shop/main/stripe")).status_code == 200
    for path in ("/internal/v1/telemetry/requests", "/admin/v1/users/1", "/envs/main"):
        response = await gateway.http.get(path, headers={"apikey": "sk_service"})
        assert response.status_code == 404
    assert not any(
        s["path"].startswith(("/internal", "/admin"))
        for up in gateway.upstreams.values()
        for s in up.seen
    )


async def test_origin_check_for_publishable_keys(gateway):
    ok = await gateway.http.get(
        "/rest/v1/orders", headers={"apikey": "pk_anon", "origin": "https://shop.example"}
    )
    assert ok.status_code == 200
    denied = await gateway.http.get(
        "/rest/v1/orders", headers={"apikey": "pk_anon", "origin": "https://evil.example"}
    )
    assert denied.status_code == 403


async def test_key_ip_and_route_restrictions(gateway):
    assert (
        await gateway.http.get("/rest/v1/orders", headers={"apikey": "sk_restricted"})
    ).status_code == 200
    denied = await gateway.http.post("/rest/v1/orders", headers={"apikey": "sk_restricted"})
    assert denied.status_code == 403
    assert denied.json()["error"] == "route_not_allowed"

    assert (
        policy.check_key("GET", "/rest/v1/orders", "10.2.3.4", {"allowed_ips": ["10.0.0.0/8"]})
        is None
    )
    assert (
        policy.check_key("GET", "/rest/v1/orders", "192.168.1.1", {"allowed_ips": ["10.0.0.0/8"]})
        == "ip_not_allowed"
    )


async def test_rate_limit_per_key(gateway):
    statuses = [
        (await gateway.http.get("/rest/v1/orders", headers={"apikey": "sk_service"})).status_code
        for _ in range(7)
    ]
    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429
    # A different key has its own budget.
    assert (
        await gateway.http.get("/rest/v1/orders", headers={"apikey": "pk_anon"})
    ).status_code == 200


async def test_body_limit(gateway):
    response = await gateway.http.post(
        "/rest/v1/orders",
        content=b"x" * 4096,
        headers={"apikey": "sk_service", "content-type": "application/octet-stream"},
    )
    assert response.status_code == 413


def test_environment_gate():
    from pawabase_server.core.context import PlatformContext

    def gate(address, context, info):
        return policy.environment_gate(address, context, info)

    anon = PlatformContext(env="main", role="anon")
    service = PlatformContext(env="main", role="service")
    scope = "10.2.3.4"

    assert gate(scope, anon, {}) is None

    down = {"maintenance": {"enabled": True, "message": "Back soon", "retry_after": 90}}
    assert gate(scope, anon, down) == ("maintenance", "Back soon", 90)
    assert gate(scope, service, down) is None
    closed = {"maintenance": {"enabled": True, "allow_secret_keys": False}}
    assert gate(scope, service, closed)[0] == "maintenance"
    assert gate(scope, anon, {"maintenance": {"enabled": False}}) is None

    allow = {"ip_allowlist": ["10.0.0.0/8"]}
    assert gate(scope, service, allow) is None
    assert gate("192.168.1.1", service, allow)[0] == "ip_not_allowed"
    assert gate("unknown", service, allow)[0] == "ip_not_allowed"
    # Browsers on publishable keys are not held to the secret-key allowlist.
    assert gate("192.168.1.1", anon, allow) is None


async def test_a_users_api_key_becomes_their_access_token(gateway):
    from pawabase_server.identity import user_keys

    asked = []

    async def token_for(raw, *, env, ip):
        asked.append((raw, env))
        if raw.endswith("revoked-key-000000"):
            raise user_keys.KeyRefused("invalid API key")
        return "minted.user.token"

    gateway.edge.user_keys.token_for = token_for
    good = await gateway.http.get(
        "/rest/v1/orders",
        headers={"apikey": "pk_anon", "authorization": "Bearer pbu_0123456789abcdefghij"},
    )
    assert good.status_code == 200
    assert good.json()["headers"]["authorization"] == "Bearer minted.user.token"
    assert asked == [("pbu_0123456789abcdefghij", "main")]

    refused = await gateway.http.get(
        "/rest/v1/orders",
        headers={"apikey": "pk_anon", "authorization": "Bearer pbu_revoked-key-000000"},
    )
    assert refused.status_code == 401 and refused.json()["error"] == "invalid_api_key"

    # An ordinary bearer token is left alone.
    jwt = await gateway.http.get(
        "/rest/v1/orders", headers={"apikey": "pk_anon", "authorization": "Bearer abc.def.ghi"}
    )
    assert jwt.json()["headers"]["authorization"] == "Bearer abc.def.ghi"
