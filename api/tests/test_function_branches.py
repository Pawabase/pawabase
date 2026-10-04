"""Function deployments: per environment, per branch, rollback, and the runtime over HTTP."""

import base64
import datetime as dt
import io
import tarfile
import textwrap

import pytest

from app.deployments import Deployments, unpack
from pawabase import codec
from pawabase_core.tokens import issue_user_token


def env(name="development"):
    return f"/platform/v1/envs/{name}"


def bundle(**files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        for name, text in files.items():
            data = textwrap.dedent(text).encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return base64.b64encode(raw.getvalue()).decode()


def fn(name, returns, extra=""):
    return f"""
        from pawabase.functions import function

        {extra}
        @function("{name}", policy="public")
        async def f(ctx):
            return {returns!r}
        """


@pytest.fixture
async def shop(api):
    await api.studio.post("/platform/v1/envs", json={"name": "production"})
    return api


async def call(api, name, branch=None, env_name="development"):
    suffix = f"?branch={branch}" if branch else ""
    response = await api.http.post(f"/functions/v1/{name}{suffix}", json={}, headers=api.context_headers(env_name))
    return response.status_code, response.json()


async def test_a_deployment_is_served_and_replaced_atomically(shop):
    first = await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "v1")})})
    assert first["deployment"]["status"] == "active" and [f["name"] for f in first["functions"]] == ["a"]
    assert await call(shop, "a") == (200, {"data": "v1"})
    second = await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "v2"), "functions/b.py": fn("b", "bee")})})
    assert await call(shop, "a") == (200, {"data": "v2"}) and await call(shop, "b") == (200, {"data": "bee"})
    rows = (await shop.studio.get(f"{env()}/function-deployments"))["data"]
    assert {r["id"]: r["status"] for r in rows} == {first["deployment"]["id"]: "superseded", second["deployment"]["id"]: "active"}


async def test_environments_do_not_share_functions(shop):
    await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "dev")})})
    assert await call(shop, "a") == (200, {"data": "dev"})
    status, body = await call(shop, "a", env_name="production")
    assert status == 404 and "no function" in body["detail"] if "detail" in body else status == 404
    await shop.studio.post(f"{env('production')}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "prod")})})
    assert await call(shop, "a", env_name="production") == (200, {"data": "prod"}) and await call(shop, "a") == (200, {"data": "dev"})


async def test_a_branch_has_its_own_functions_and_falls_back_to_the_environments(shop):
    await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "main-a"), "functions/b.py": fn("b", "main-b")})})
    await shop.studio.post(f"{env()}/function-deployments", json={"branch": "feature-x", "archive": bundle(**{"functions/a.py": fn("a", "branch-a"), "functions/c.py": fn("c", "branch-c")})})
    assert await call(shop, "a") == (200, {"data": "main-a"})  # main is untouched by the branch
    assert await call(shop, "a", "feature-x") == (200, {"data": "branch-a"})
    assert await call(shop, "b", "feature-x") == (200, {"data": "main-b"})  # what the branch did not redefine
    assert (await call(shop, "c"))[0] == 404 and await call(shop, "c", "feature-x") == (200, {"data": "branch-c"})
    listing = await shop.studio.get(f"{env()}/functions?branch=feature-x")
    assert {f["name"]: f["source"] for f in listing["data"]} == {"a": "branch", "b": "deployment", "c": "branch"}
    branches = (await shop.studio.get(f"{env()}/function-branches"))["data"]
    assert {b["branch"] for b in branches} == {"main", "feature-x"}
    await shop.studio.delete(f"{env()}/function-branches/feature-x")
    assert (await call(shop, "c", "feature-x"))[0] == 404 and await call(shop, "a", "feature-x") == (200, {"data": "main-a"})


async def test_code_that_does_not_load_is_refused_and_the_old_code_keeps_serving(shop):
    await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "good")})})
    for broken in ({"functions/a.py": "def (:\n"}, {"functions/a.py": "raise RuntimeError('boom')\n"}, {"functions/a.py": "import module_that_is_not_there\n"}):
        with pytest.raises(Exception) as refused:
            await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**broken)})
        assert "422" in str(refused.value) or "does not load" in str(refused.value) or "refused" in str(refused.value)
        assert await call(shop, "a") == (200, {"data": "good"})
    rows = (await shop.studio.get(f"{env()}/function-deployments"))["data"]
    assert [r["status"] for r in rows].count("failed") == 3 and [r["status"] for r in rows].count("active") == 1


async def test_what_cannot_be_deployed_is_named(shop):
    headers = shop.context_headers("development", role="service")
    for files, expected in (
        ({"functions/a.py": fn("a", 1), "routes.py": "router = None\n"}, "routes.py cannot be deployed"),
        ({"other/a.py": "X = 1\n"}, "functions/*.py"),
        ({"functions/a.py": "def (:\n"}, "functions/a.py:1"),
        ({"functions/a.py": "import module_that_is_not_there\n"}, "module_that_is_not_there"),
    ):
        response = await shop.http.post(f"{env()}/function-deployments", json={"archive": bundle(**files)}, headers=headers)
        assert response.status_code == 422, response.text
        assert expected in response.text, response.text


async def test_rollback_reinstalls_the_exact_earlier_bytes(shop):
    one = await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "one")})})
    two = await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "two")})})
    assert await call(shop, "a") == (200, {"data": "two"})
    back = await shop.studio.post(f"{env()}/function-deployments/{one['deployment']['id']}/activate", json={})
    assert back["deployment"]["status"] == "active" and await call(shop, "a") == (200, {"data": "one"})
    rows = {r["id"]: r["status"] for r in (await shop.studio.get(f"{env()}/function-deployments"))["data"]}
    assert rows == {one["deployment"]["id"]: "active", two["deployment"]["id"]: "superseded"}


async def test_another_process_notices_a_deployment_from_the_stamp_alone(shop):
    await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "v1")})})
    # A second process: its own registry, nothing told to it, only the shared folder to look at.
    other = Deployments(shop.platform.deployments.root)
    from pawabase_core.functions import clear_functions, get_exact

    clear_functions("runtime/development")
    other.ensure_loaded("development")
    assert get_exact("runtime/development", "a") is not None
    await shop.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/a.py": fn("a", "v2"), "functions/z.py": fn("z", "new")})})
    import app.deployments as module

    module.RECHECK_SECONDS = 0
    try:
        other.ensure_loaded("development")
    finally:
        module.RECHECK_SECONDS = 1.0
    assert get_exact("runtime/development", "z") is not None


async def test_helper_packages_ship_with_the_functions(shop):
    archive = bundle(**{"functions/use.py": """
        from pawabase.functions import function
        from shared_helpers import greeting

        @function("use", policy="public")
        async def use(ctx):
            return greeting()
        """, "shared_helpers/__init__.py": "def greeting():\n    return 'from the helper'\n"})
    await shop.studio.post(f"{env()}/function-deployments", json={"archive": archive})
    assert await call(shop, "use") == (200, {"data": "from the helper"})


async def test_a_function_chooses_its_status_and_a_run_is_recorded_per_branch(shop):
    code = """
        from pawabase.functions import FunctionError, function

        @function("refuse", policy="public")
        async def refuse(ctx):
            ctx.log("about to refuse", who=ctx.auth.get("user_id"))
            raise FunctionError("Already paid.", status=409, code="already_paid", details={"order": 7})
        """
    await shop.studio.post(f"{env()}/function-deployments", json={"branch": "fx", "archive": bundle(**{"functions/r.py": code})})
    status, body = await call(shop, "refuse", "fx")
    assert status == 409 and body["error"] == "already_paid" and body["message"] == "Already paid."
    runs = (await shop.studio.get(f"{env()}/function-runs?function=refuse&branch=fx"))["data"]
    assert runs[0]["status"] == "failed" and runs[0]["branch"] == "fx" and runs[0]["deployment_id"]
    detail = await shop.studio.get(f"{env()}/function-runs/{runs[0]['id']}")
    assert detail["logs"][0]["message"] == "about to refuse"


async def test_a_key_manages_only_its_own_environment(shop):
    headers = shop.context_headers("development", role="service")
    own = await shop.http.get(f"{env()}/function-deployments", headers=headers)
    assert own.status_code == 200
    assert (await shop.http.get(f"{env('production')}/function-deployments", headers=headers)).status_code == 403
    scoped = shop.context_headers("development", role="service", scopes=("functions:invoke",))
    assert (await shop.http.get(f"{env()}/function-deployments", headers=scoped)).status_code == 403


# ── the runtime over HTTP ────────────────────────────────────────────────

ORDERS = {"name": "orders", "fields": [{"name": "total", "type": "number"}, {"name": "placed_at", "type": "datetime"}, {"name": "meta", "type": "json"}], "operations": {}}


@pytest.fixture
async def rpc(shop):
    await shop.studio.post(f"{env()}/resources", json=ORDERS)
    await shop.studio.post(f"{env()}/resources/orders/migrate")
    headers = shop.context_headers("development", role="service")

    async def call_(method, *args, as_user=None, expect=200, **kwargs):
        response = await shop.http.post(f"{env()}/runtime/call", json={"method": method, "args": codec.encode(list(args)), "kwargs": codec.encode(kwargs), "as_user": as_user}, headers=headers)
        assert response.status_code == expect, response.text
        return codec.decode(response.json().get("result")) if expect == 200 else response.json()

    async def db(op, expect=200, **fields):
        response = await shop.http.post(f"{env()}/runtime/db", json={"op": op, **{k: codec.encode(v) for k, v in fields.items()}}, headers=headers)
        assert response.status_code == expect, response.text
        body = response.json()
        return body.get("tx") if op == "begin" else (codec.decode(body.get("result")) if expect == 200 else body)

    shop.rpc, shop.db = call_, db
    return shop


async def test_runtime_calls_reach_resources_and_keep_types(rpc):
    when = dt.datetime(2026, 10, 2, 9, 30, tzinfo=dt.UTC)
    created = await rpc.rpc("resource_create", "orders", {"total": 12.5, "placed_at": when, "meta": {"a": [1]}})
    fetched = await rpc.rpc("resource_get", "orders", created["id"])
    assert fetched["total"] == 12.5 and fetched["meta"] == {"a": [1]}
    listed = await rpc.rpc("resource_list", "orders", filters={"total": 12.5})
    assert listed["total"] == 1
    assert await rpc.rpc("emit", "order.placed", {"id": created["id"]})  # an event id: it was published
    assert await rpc.rpc("secret", "NOT_SET") is None


async def test_runtime_errors_keep_their_status_and_code(rpc):
    body = await rpc.rpc("call_flow", "no-such-flow", {}, expect=500)
    assert body["error"]["code"] == "unknown_flow" and "no-such-flow" in body["error"]["message"]
    assert await rpc.rpc("not_a_method", expect=404)
    assert (await rpc.rpc("db_query", "DELETE FROM orders", expect=400))["error"]["code"] == "write_refused"


async def test_remote_sql_sessions_and_transactions(rpc):
    await rpc.db("execute", sql="CREATE TABLE ledger (id INTEGER PRIMARY KEY, amount INTEGER)")
    assert await rpc.db("execute", sql="INSERT INTO ledger (amount) VALUES (?)", params=[10]) == 1
    assert await rpc.db("scalar", sql="SELECT COUNT(*) FROM ledger") == 1
    # A transaction that is rolled back leaves nothing; one that commits keeps its writes.
    tx = await rpc.db("begin")
    await rpc.db("execute", tx=tx, sql="INSERT INTO ledger (amount) VALUES (?)", params=[20])
    assert await rpc.db("scalar", tx=tx, sql="SELECT COUNT(*) FROM ledger") == 2
    await rpc.db("rollback", tx=tx)
    assert await rpc.db("scalar", sql="SELECT COUNT(*) FROM ledger") == 1
    tx = await rpc.db("begin")
    await rpc.db("execute", tx=tx, sql="INSERT INTO ledger (amount) VALUES (?)", params=[30])
    await rpc.db("commit", tx=tx)
    assert await rpc.db("scalar", sql="SELECT SUM(amount) FROM ledger") == 40
    assert (await rpc.db("scalar", tx="not-open", sql="SELECT 1", expect=409))["error"]["code"] == "no_transaction"
    created = await rpc.rpc("resource_create", "orders", {"total": 1})
    assert await rpc.db("update", table="orders", id=created["id"], data={"total": 2}) == 1
    assert await rpc.db("scalar", sql="SELECT total FROM orders WHERE id = ?", params=[created["id"]]) == 2
    assert await rpc.db("delete", table="orders", id=created["id"]) == 1


async def test_the_deployment_vouches_for_a_token_and_nobody_else(rpc):
    token = issue_user_token(rpc.settings.jwt_master_secret, env="development", user_id="42", jti="j", session_id="s", roles=["staff"], permissions=["orders.read"], email="ada@example.com")
    headers = rpc.context_headers("development", role="service")
    good = (await rpc.http.post(f"{env()}/runtime/identify", json={"token": token}, headers=headers)).json()["auth"]
    assert good["authenticated"] and good["user_id"] == "42" and good["roles"] == ["staff"] and good["email"] == "ada@example.com"
    for bad in (token + "x", "garbage", None):
        answer = (await rpc.http.post(f"{env()}/runtime/identify", json={"token": bad}, headers=headers)).json()["auth"]
        assert answer["authenticated"] is False
    other_env = issue_user_token(rpc.settings.jwt_master_secret, env="production", user_id="1", jti="j", session_id="s")
    assert (await rpc.http.post(f"{env()}/runtime/identify", json={"token": other_env}, headers=headers)).json()["auth"]["authenticated"] is False


async def test_the_runtime_needs_its_scope_and_the_right_environment(rpc):
    body = {"method": "emit", "args": ["x", {}]}
    no_scope = rpc.context_headers("development", role="service", scopes=("resource:read",))
    assert (await rpc.http.post(f"{env()}/runtime/call", json=body, headers=no_scope)).status_code == 403
    wrong = rpc.context_headers("production", role="service")  # a production key against development
    assert (await rpc.http.post(f"{env()}/runtime/call", json=body, headers=wrong)).status_code == 403
    anonymous = rpc.context_headers("development")
    assert (await rpc.http.post(f"{env()}/runtime/call", json=body, headers=anonymous)).status_code in (401, 403)


def test_the_unpacker_refuses_unsafe_archives(tmp_path):
    from app.deployments import DeploymentError

    for name in ("../escape.py", "/abs.py"):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(DeploymentError):
            unpack(raw.getvalue(), tmp_path / "out")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        link = tarfile.TarInfo("functions/link.py")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        archive.addfile(link)
    with pytest.raises(DeploymentError):
        unpack(raw.getvalue(), tmp_path / "out2")
    with pytest.raises(DeploymentError):
        unpack(b"not an archive", tmp_path / "out3")


async def test_reading_a_secret_needs_its_own_scope_and_runs_can_be_followed(rpc):
    body = {"method": "secret", "args": ["ANY"]}
    only_runtime = rpc.context_headers("development", role="service", scopes=("runtime:use",))
    assert (await rpc.http.post(f"{env()}/runtime/call", json=body, headers=only_runtime)).status_code == 403
    both = rpc.context_headers("development", role="service", scopes=("runtime:use", "secrets:read"))
    assert (await rpc.http.post(f"{env()}/runtime/call", json=body, headers=both)).status_code == 200
    # `after` returns only runs newer than the last one a follower saw.
    await rpc.studio.post(f"{env()}/function-deployments", json={"archive": bundle(**{"functions/t.py": fn("t", 1)})})
    await call(rpc, "t")
    first = (await rpc.studio.get(f"{env()}/function-runs?function=t"))["data"]
    assert len(first) == 1
    await call(rpc, "t")
    newer = (await rpc.studio.get(f"{env()}/function-runs", params={"function": "t", "after": first[0]["created_at"]}))["data"]
    assert len(newer) == 1 and newer[0]["id"] != first[0]["id"]
