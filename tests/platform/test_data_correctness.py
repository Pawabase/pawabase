"""Data API correctness: strict booleans, PATCH null, constraint conflicts, write-only fields, console statements."""

import pytest

from pawabase_server.core.clients import ServiceError
from pawabase_server.core.errors import PawabaseError
from pawabase_server.data.console import validate_statement
from pawabase_server.data.inspect import is_read_only

ENV = "/platform/v1/envs/development"

ACCOUNTS = {
    "name": "accounts",
    "fields": [
        {"name": "email", "type": "string", "required": True, "unique": True},
        {"name": "nickname", "type": "string"},
        {"name": "active", "type": "boolean", "default": True},
        {"name": "pin", "type": "string", "write_only": True},
    ],
    "operations": {
        op: {"enabled": True, "policy": "public"}
        for op in ("list", "get", "create", "update", "delete")
    },
}


@pytest.fixture
async def accounts(api):
    await api.studio.post(f"{ENV}/resources", json=ACCOUNTS)
    await api.studio.post(f"{ENV}/resources/accounts/migrate")
    api.anon = api.context_headers("development")
    return api


async def create(api, **data):
    response = await api.http.post("/rest/v1/accounts", json=data, headers=api.anon)
    return response


async def test_boolean_filters_are_strict(accounts):
    api = accounts
    await create(api, email="a@x.io", active=True)
    await create(api, email="b@x.io", active=False)

    async def emails(query):
        response = await api.http.get(f"/rest/v1/accounts?{query}", headers=api.anon)
        return response.status_code, sorted(r["email"] for r in response.json().get("data", []))

    assert await emails("filter[active]=false") == (200, ["b@x.io"])
    assert await emails("filter[active]=true") == (200, ["a@x.io"])
    assert await emails("filter[active]=0") == (200, ["b@x.io"])
    status, _ = await emails("filter[active]=maybe")
    assert status == 400


async def test_patch_null_on_a_required_field_is_refused(accounts):
    api = accounts
    record = (await create(api, email="a@x.io", nickname="ada")).json()
    refused = await api.http.patch(
        f"/rest/v1/accounts/{record['id']}", json={"email": None}, headers=api.anon
    )
    assert refused.status_code == 422
    cleared = await api.http.patch(
        f"/rest/v1/accounts/{record['id']}", json={"nickname": None}, headers=api.anon
    )
    assert cleared.status_code == 200 and cleared.json()["nickname"] is None


async def test_a_duplicate_unique_value_is_a_conflict_not_a_crash(accounts):
    api = accounts
    assert (await create(api, email="a@x.io")).status_code == 201
    duplicate = await create(api, email="a@x.io")
    assert duplicate.status_code == 409, duplicate.text
    assert "a@x.io" not in duplicate.text  # the value is not echoed back


async def test_write_only_fields_are_never_returned(accounts):
    api = accounts
    created = await create(api, email="a@x.io", pin="1234")
    assert created.status_code == 201 and "pin" not in created.json()
    fetched = await api.http.get(f"/rest/v1/accounts/{created.json()['id']}", headers=api.anon)
    assert "pin" not in fetched.json()
    listed = await api.http.get("/rest/v1/accounts", headers=api.anon)
    assert all("pin" not in row for row in listed.json()["data"])
    selected = await api.http.get("/rest/v1/accounts?select=email,pin", headers=api.anon)
    assert all("pin" not in row for row in selected.json()["data"])


@pytest.mark.parametrize(
    ("sql", "read"),
    [
        ("SELECT 1", True),
        ("  select * from t -- comment", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", True),
        ("WITH gone AS (DELETE FROM t RETURNING id) SELECT * FROM gone", False),
        ("SELECT 1; DELETE FROM t", False),
        ("DELETE FROM t", False),
        ("/* hi */ UPDATE t SET a = 1", False),
        ("SELECT * INTO t2 FROM t", False),
        ("not sql at all (", False),
    ],
)
def test_read_only_classification(sql, read):
    assert is_read_only(sql, "postgres") is read


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SET ROLE postgres",
        "BEGIN",
        "COMMIT",
        "COPY t TO '/tmp/x'",
    ],
)
def test_console_refuses_session_and_multi_statements(sql):
    with pytest.raises(PawabaseError):
        validate_statement(sql, "postgres")


async def test_console_reads_cannot_write(accounts):
    api = accounts
    await create(api, email="a@x.io")
    rows = await api.studio.post(
        f"{ENV}/database/query", json={"sql": "SELECT email FROM accounts"}
    )
    assert [r["email"] for r in rows["rows"]] == ["a@x.io"]
    with pytest.raises(ServiceError):
        await api.studio.post(f"{ENV}/database/query", json={"sql": "DELETE FROM accounts"})
    with pytest.raises(ServiceError):
        await api.studio.post(
            f"{ENV}/database/query",
            json={"sql": "SELECT 1; DELETE FROM accounts", "allow_write": True},
        )
    rows = await api.studio.post(
        f"{ENV}/database/query", json={"sql": "SELECT count(*) AS n FROM accounts"}
    )
    assert rows["rows"] == [{"n": 1}]


async def test_console_describing_statements_take_the_read_path(accounts):
    api = accounts

    async def query(sql, allow_write=False):
        return await api.studio.post(
            f"{ENV}/database/query", json={"sql": sql, "allow_write": allow_write}
        )

    columns = await query("PRAGMA table_info(accounts)")
    assert "email" in {row["name"] for row in columns["rows"]}
    plan = await query("EXPLAIN QUERY PLAN SELECT * FROM accounts WHERE email = 'a'")
    assert plan["rows"]
    assert "user_version" in (await query("PRAGMA user_version"))["rows"][0]
    # A pragma that sets something is refused by the database guard, even with writes allowed.
    with pytest.raises(ServiceError):
        await query("PRAGMA user_version = 5", allow_write=True)
    with pytest.raises(ServiceError):
        await query("PRAGMA writable_schema = 1", allow_write=True)
    # A semicolon inside a string and a leading comment no longer confuse the classifier.
    await create(api, email="a;b@x.io")
    found = await query("-- who\nSELECT email FROM accounts WHERE email = 'a;b@x.io'")
    assert found["rows"] == [{"email": "a;b@x.io"}]
