"""Console reads on MySQL run in a database-enforced read-only transaction.

Runs against a real MySQL: ``PAWABASE_TEST_MYSQL_URL=mysql://root:pw@127.0.0.1:33061/shop``.
"""

import os
import uuid

import pytest
from tortoise.context import TortoiseContext

from pawabase_server.core.errors import PawabaseError
from pawabase_server.data.console import fetch_read_only
from pawabase_server.data.source import DataSourcePool

URL = os.environ.get("PAWABASE_TEST_MYSQL_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="needs PAWABASE_TEST_MYSQL_URL")


@pytest.fixture
async def mysql():
    async with TortoiseContext():
        pool = DataSourcePool()
        source = await pool.get(URL, alias="mysql")
        table = f"t_{uuid.uuid4().hex[:8]}"
        await source.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY, name VARCHAR(20))")
        await source.execute(f"INSERT INTO {table} VALUES (1, 'a'), (2, 'b')")
        try:
            yield source, table
        finally:
            await source.execute(f"DROP TABLE {table}")
            await pool.close()


async def test_reads_return_rows(mysql):
    source, table = mysql
    rows = await fetch_read_only(source, f"SELECT name FROM {table} WHERE id = %s", [2])
    assert rows == [{"name": "b"}]
    assert await fetch_read_only(source, f"DESCRIBE {table}")
    assert await fetch_read_only(source, f"EXPLAIN SELECT * FROM {table}")
    assert await fetch_read_only(source, "SHOW TABLES")


async def test_a_write_hidden_in_a_read_is_refused_by_the_database(mysql):
    source, table = mysql
    with pytest.raises(PawabaseError):
        await fetch_read_only(source, f"DELETE FROM {table}")  # the parser refuses this one
    # A function call the parser sees as a read: MySQL's read-only transaction refuses the write inside it.
    await source.execute(
        f"CREATE FUNCTION wipe_{table}() RETURNS INT MODIFIES SQL DATA DETERMINISTIC "
        f"BEGIN DELETE FROM {table}; RETURN 1; END"
    )
    try:
        with pytest.raises(Exception, match="(?i)read.only|READ ONLY"):
            await fetch_read_only(source, f"SELECT wipe_{table}()")
    finally:
        await source.execute(f"DROP FUNCTION wipe_{table}")
    assert await source.fetch(f"SELECT COUNT(*) AS n FROM {table}") == [{"n": 2}]
    # The connection is not left read-only for the next user.
    await source.execute(f"INSERT INTO {table} VALUES (3, 'c')")
