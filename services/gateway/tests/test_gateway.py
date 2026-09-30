from pawabase_kit.context import CONTEXT_HEADER
from pawabase_kit.tokens import verify_context_token


async def test_proxies_with_signed_context(gateway):
    response = await gateway.http.get(
        "/rest/v1/orders?limit=2&apikey=pk_anon",
        headers={"x-pawabase-context": "forged", "x-pawabase-service": "forged"},
    )
    assert response.status_code == 200
    seen = response.json()
    assert seen["service"] == "api"
    assert seen["path"] == "/rest/v1/orders"
    assert seen["query"] == "limit=2"
    headers = seen["headers"]
    assert "apikey" not in headers and "x-pawabase-service" not in headers
    context = verify_context_token(headers[CONTEXT_HEADER], gateway.settings.internal_secret)
    assert (context.project, context.env, context.role, context.key_id) == (
        "shop",
        "main",
        "anon",
        "k1",
    )
    assert headers["x-request-id"]
    assert response.headers["x-upstream"] == "api"

    v2 = await gateway.http.get("/rest/v2/orders?apikey=pk_anon")
    assert v2.status_code == 200 and v2.json()["path"] == "/rest/v2/orders"


async def test_routes_by_prefix(gateway):
    for path, service in [
        ("/auth/v1/token", "akountz"),
        ("/realtime/v1/publish", "angula"),
        ("/storage/v1/object/b/x", "api"),
        ("/functions/v1/hello", "api"),
    ]:
        response = await gateway.http.post(path, json={"a": 1}, headers={"apikey": "sk_service"})
        assert response.status_code == 200, path
        assert response.json()["service"] == service
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
    for path in ("/internal/v1/keys/resolve", "/admin/v1/users/1"):
        response = await gateway.http.get(path, headers={"apikey": "sk_service"})
        assert response.status_code in (401, 404)
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


async def test_status_and_health(gateway):
    assert (await gateway.http.get("/health")).status_code == 200
    response = await gateway.http.get("/v1/status")
    assert response.status_code == 200
    assert set(response.json()["services"]) == {"api", "akountz", "angula"}
