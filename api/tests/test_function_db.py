"""SQL for functions: ``ctx.runtime.db()`` / ``.transaction()``, and the request details a custom route hands its function."""

import textwrap

import pytest

ENV = "/platform/v1/envs/development"

STOCK = {
    "name": "stock",
    "fields": [
        {"name": "sku", "type": "string", "unique": True},
        {"name": "on_hand", "type": "integer", "default": 0},
        {"name": "meta", "type": "json"},
        {"name": "active", "type": "boolean", "default": True},
        {"name": "seen_at", "type": "datetime"},
    ],
    "operations": {},
}

CODE = textwrap.dedent(
    '''
    from pawabase_core.functions import function

    class OutOfStock(Exception):
        pass

    @function("stock.add", policy="public")
    async def add(ctx):
        db = await ctx.runtime.db()
        row = await db.insert("stock", {"sku": ctx.input["sku"], "on_hand": ctx.input.get("on_hand", 5), "meta": {"tags": ["a", "b"]}})
        return {"row": row}

    @function("stock.take", policy="public")
    async def take(ctx):
        """Decrement only if enough is left: one conditional UPDATE is the whole concurrency story."""
        db = await ctx.runtime.db()
        moved = await db.execute("UPDATE stock SET on_hand = on_hand - ? WHERE sku = ? AND on_hand >= ?", [ctx.input["n"], ctx.input["sku"], ctx.input["n"]])
        return {"moved": moved, "left": await db.scalar("SELECT on_hand FROM stock WHERE sku = ?", [ctx.input["sku"]])}

    @function("stock.move", policy="public")
    async def move(ctx):
        """Two writes in one commit; the second one fails, so the first must not survive."""
        try:
            async with ctx.runtime.transaction() as db:
                await db.execute("UPDATE stock SET on_hand = on_hand - 1 WHERE sku = ?", [ctx.input["sku"]])
                if ctx.input.get("fail"):
                    raise OutOfStock()
                await db.insert("stock", {"sku": ctx.input["sku"] + "-copy", "on_hand": 1})
        except OutOfStock:
            return {"ok": False, "left": await (await ctx.runtime.db()).scalar("SELECT on_hand FROM stock WHERE sku = ?", [ctx.input["sku"]])}
        return {"ok": True}

    @function("stock.read", policy="public")
    async def read(ctx):
        db = await ctx.runtime.db()
        return {"rows": await db.fetch("SELECT sku, on_hand, meta, active FROM stock ORDER BY sku")}

    @function("echo.request", policy="public")
    async def echo(ctx):
        request = ctx.request
        return {"headers": request.get("headers"), "client_ip": request.get("client_ip"), "query": request.get("query"), "body": request.get("body"), "input": ctx.input}
    '''
)


@pytest.fixture
async def shop(api, tmp_path):
    code = tmp_path / "code" / "functions"
    code.mkdir(parents=True)
    (code / "stock.py").write_text(CODE)
    await api.studio.post(f"{ENV}/resources", json=STOCK)
    await api.studio.post(f"{ENV}/resources/stock/migrate")
    for name, path, method in (("stock.add", "/stock-add", "POST"), ("stock.take", "/stock-take", "POST"), ("stock.move", "/stock-move", "POST"),
                               ("stock.read", "/stock-read", "GET"), ("echo.request", "/echo/{thing}", "POST")):
        await api.studio.post(f"{ENV}/routes", json={"method": method, "path": path, "handler_type": "function", "handler": name, "policy": "public"})
    return api


async def call(api, path, body=None, method="POST", headers=None):
    response = await api.http.request(method, f"/rest/v1{path}", json=body, headers={**api.context_headers("development"), **(headers or {})})
    assert response.status_code == 200, response.text
    return response.json()


async def test_insert_applies_defaults_and_decodes_by_the_resource_fields(shop):
    added = await call(shop, "/stock-add", {"sku": "A"})
    row = added["row"]
    assert row["on_hand"] == 5 and row["meta"] == {"tags": ["a", "b"]} and row["active"] is True and row["created_at"]
    bare = await call(shop, "/stock-add", {"sku": "B", "on_hand": 0})
    assert bare["row"]["on_hand"] == 0 and bare["row"]["active"] is True  # a declared default applies when the value is not given
    rows = (await call(shop, "/stock-read", method="GET"))["rows"]
    assert [r["sku"] for r in rows] == ["A", "B"] and rows[0]["meta"] == {"tags": ["a", "b"]} and rows[0]["active"] is True


async def test_a_conditional_update_is_the_concurrency_guard(shop):
    await call(shop, "/stock-add", {"sku": "C", "on_hand": 3})
    assert (await call(shop, "/stock-take", {"sku": "C", "n": 2})) == {"moved": 1, "left": 1}
    assert (await call(shop, "/stock-take", {"sku": "C", "n": 2})) == {"moved": 0, "left": 1}  # not enough left: nothing changes


async def test_a_transaction_rolls_back_every_write_when_one_fails(shop):
    await call(shop, "/stock-add", {"sku": "D", "on_hand": 4})
    failed = await call(shop, "/stock-move", {"sku": "D", "fail": True})
    assert failed == {"ok": False, "left": 4}  # the decrement did not survive the failure
    assert (await call(shop, "/stock-move", {"sku": "D"})) == {"ok": True}
    rows = {r["sku"]: r["on_hand"] for r in (await call(shop, "/stock-read", method="GET"))["rows"]}
    assert rows == {"D": 3, "D-copy": 1}


async def test_identifiers_are_checked_before_they_reach_sql(shop):
    response = await shop.http.post("/rest/v1/stock-add", json={"sku": "X"}, headers=shop.context_headers("development"))
    assert response.status_code == 200
    from app.data.sql import check_identifier

    with pytest.raises(Exception):
        check_identifier("stock; DROP TABLE stock")


async def test_a_custom_route_hands_its_function_headers_and_the_client_address(shop):
    out = await call(shop, "/echo/widget?x=1", {"a": 1}, headers={"X-Thing": "yes", "Apikey": "pb_pk_secret", "Cookie": "s=1", "X-Pawabase-Context": "forged", "X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert out["headers"]["x-thing"] == "yes"
    # The platform's own credentials and browser session state are never handed to application code.
    assert not {"apikey", "cookie", "x-pawabase-context"} & set(out["headers"])
    # "6.6.6.6" is whatever the caller claimed; the gateway appended the address it actually saw, which is the one a function gets.
    assert out["client_ip"] == "203.0.113.9" and out["query"] == {"x": "1"}
    assert out["body"]["a"] == 1 and out["input"] == {"a": 1, "thing": "widget"}
