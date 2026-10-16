"""Developer SQL on a schema-shared Postgres reaches its own environment and nothing else.

Runs against a real Postgres: ``PAWABASE_TEST_POSTGRES_URL=postgres://pawabase:pawabase@127.0.0.1:5432/pawabase``.
"""

import os
import uuid
from types import SimpleNamespace

import pytest
from tortoise.context import TortoiseContext

from pawabase_server.core.errors import PawabaseError
from pawabase_server.data import isolation
from pawabase_server.data.console import fetch_read_only
from pawabase_server.data.isolation import developer_source, role_name, role_url
from pawabase_server.data.source import DataSourcePool

URL = os.environ.get("PAWABASE_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="needs PAWABASE_TEST_POSTGRES_URL")


def schema_url(schema):
    return URL + ("&" if "?" in URL else "?") + f"schema={schema}"


@pytest.fixture
async def world():
    # Transactions run inside a Tortoise context, as they do in the server.
    async with TortoiseContext():
        async for value in _world():
            yield value


async def _world():
    pool = DataSourcePool()
    tag = uuid.uuid4().hex[:8]
    mine, theirs = f"pbt_{tag}_a", f"pbt_{tag}_b"
    admin = await pool.get(URL, alias="admin")
    own = await pool.get(schema_url(mine), alias="a")
    other = await pool.get(schema_url(theirs), alias="b")
    await own.client.execute_script(
        "CREATE TABLE orders (id int primary key, total int); INSERT INTO orders VALUES (1, 10)"
    )
    await other.client.execute_script(
        "CREATE TABLE orders (id int primary key, total int); INSERT INTO orders VALUES (2, 99)"
    )
    await admin.client.execute_script(
        f"CREATE TABLE public.pbt_{tag}_platform (secret text); INSERT INTO public.pbt_{tag}_platform VALUES ('x')"
    )
    platform = SimpleNamespace(
        settings=SimpleNamespace(sql_isolation="role", master_key="test-master-key"), sources=pool
    )
    state = SimpleNamespace(env_name="a", source=lambda: pool.get(schema_url(mine), alias="a"))
    yield SimpleNamespace(
        platform=platform, state=state, own=own, admin=admin, mine=mine, theirs=theirs, tag=tag
    )
    await pool.close()
    cleanup = DataSourcePool()
    admin = await cleanup.get(URL, alias="cleanup")
    await admin.client.execute_script(
        f'DROP SCHEMA IF EXISTS "{mine}" CASCADE; DROP SCHEMA IF EXISTS "{theirs}" CASCADE'
    )
    role = role_name(mine)
    if await admin.fetch("SELECT 1 FROM pg_roles WHERE rolname = $1", [role]):
        await admin.client.execute_script(f'DROP OWNED BY "{role}"; DROP ROLE "{role}"')
    await admin.client.execute_script(f"DROP TABLE IF EXISTS public.pbt_{tag}_platform")
    await cleanup.close()
    isolation._ready.clear()


async def test_own_schema_is_open_and_everything_else_is_not(world):
    sql = await developer_source(world.platform, world.state)
    assert sql is not world.own
    assert await sql.fetch("SELECT total FROM orders") == [{"total": 10}]
    with pytest.raises(Exception, match="permission denied"):
        await sql.fetch(f'SELECT * FROM "{world.theirs}".orders')
    with pytest.raises(Exception, match="permission denied"):
        await sql.fetch(f"SELECT * FROM public.pbt_{world.tag}_platform")


async def test_sql_cannot_switch_back_to_the_platform_user(world):
    sql = await developer_source(world.platform, world.state)
    with pytest.raises(Exception, match="permission denied"):
        await sql.fetch("SELECT set_config('role', 'pawabase', false)")
    with pytest.raises(Exception, match="permission denied"):
        await sql.execute("SET ROLE pawabase")
    assert (await sql.fetch("SELECT current_user AS who, session_user AS s"))[0] == {
        "who": role_name(world.mine),
        "s": role_name(world.mine),
    }


async def test_console_ddl_works_and_the_platform_keeps_access(world):
    sql = await developer_source(world.platform, world.state)
    await sql.execute("ALTER TABLE orders ADD COLUMN note text")  # a table the platform made
    await sql.execute("CREATE TABLE notes (id int)")
    await sql.execute("INSERT INTO notes VALUES (5)")
    assert await world.own.fetch("SELECT id FROM notes") == [{"id": 5}]
    await world.own.execute(
        "ALTER TABLE notes ADD COLUMN body text"
    )  # the platform still owns what it needs


async def test_reads_are_read_only_in_the_database(world):
    sql = await developer_source(world.platform, world.state)
    assert await fetch_read_only(sql, "SELECT id FROM orders") == [{"id": 1}]
    with pytest.raises(PawabaseError):
        await fetch_read_only(
            sql, "WITH gone AS (DELETE FROM orders RETURNING id) SELECT * FROM gone"
        )
    # A function that writes, called from a SELECT: the parser cannot see it; the read-only transaction stops it.
    await sql.execute(
        "CREATE FUNCTION wipe() RETURNS int LANGUAGE sql AS $$ DELETE FROM orders RETURNING 1 $$"
    )
    with pytest.raises(Exception, match="read-only"):
        await fetch_read_only(sql, "SELECT wipe()")
    assert await sql.fetch("SELECT count(*) AS n FROM orders") == [{"n": 1}]
    # Describing statements read; EXPLAIN ANALYZE would execute its statement, and the transaction refuses that.
    assert await fetch_read_only(sql, "EXPLAIN SELECT * FROM orders")
    assert (await fetch_read_only(sql, "SHOW search_path"))[0]["search_path"].startswith(world.mine)
    with pytest.raises(Exception, match="read-only"):
        await fetch_read_only(sql, "EXPLAIN ANALYZE DELETE FROM orders")
    assert await sql.fetch("SELECT count(*) AS n FROM orders") == [{"n": 1}]


async def test_isolation_off_uses_the_platform_connection(world):
    world.platform.settings.sql_isolation = "off"
    assert await developer_source(world.platform, world.state) is world.own


async def test_a_role_that_cannot_be_made_refuses_sql(world, monkeypatch):
    async def denied(*args):
        raise RuntimeError("permission denied to create role")

    monkeypatch.setattr(isolation, "ensure_role", denied)
    with pytest.raises(PawabaseError) as info:
        await developer_source(world.platform, world.state)
    assert info.value.status == 503


def test_role_url_replaces_only_the_credentials():
    url = role_url("postgres://u:p@db:5432/x?schema=s&minsize=1", "r", "pw")
    assert url == "postgres://r:pw@db:5432/x?schema=s&minsize=1"
