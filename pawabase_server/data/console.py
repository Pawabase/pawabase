"""SQL console boundaries: parsed statements plus database-enforced reads.

A statement is parsed (sqlglot) and must be exactly one query or data/schema change; a read runs in a transaction the database
itself holds read-only. Which environment the SQL can reach is not decided here but by the connection it runs on — see
:mod:`pawabase_server.data.isolation`.
"""

import sqlite3

from sqlglot import exp

from pawabase_server.core.errors import PawabaseError
from pawabase_server.data.inspect import describes, is_read_only, parse_one_statement
from pawabase_server.data.source import timed

#: SQLite pragmas that only report, whatever their argument (``PRAGMA table_info(things)``).
SQLITE_INSPECTING_PRAGMAS = {
    "table_info",
    "table_xinfo",
    "table_list",
    "index_list",
    "index_info",
    "index_xinfo",
    "foreign_key_list",
    "foreign_key_check",
    "database_list",
    "collation_list",
    "function_list",
    "module_list",
    "pragma_list",
    "compile_options",
    "integrity_check",
    "quick_check",
}
#: SQLite pragmas that are settings: readable, never set from the read path (``PRAGMA user_version`` but not ``= 5``).
SQLITE_SETTING_PRAGMAS = {
    "user_version",
    "schema_version",
    "application_id",
    "page_count",
    "page_size",
    "freelist_count",
    "encoding",
    "journal_mode",
    "foreign_keys",
    "auto_vacuum",
    "cache_size",
    "busy_timeout",
    "synchronous",
}
SQLITE_DENIED_FUNCTIONS = {"load_extension", "writefile", "readfile", "eval", "fts3_tokenizer"}


def validate_statement(sql: str, dialect: str) -> None:
    node = parse_one_statement(sql, dialect)
    allowed = (exp.Query, exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Alter, exp.Drop)
    if node is None or not (isinstance(node, allowed) or describes(node)):
        raise PawabaseError(
            "Run one query, data or schema change, or EXPLAIN/SHOW/DESCRIBE/PRAGMA; connection and transaction commands are not allowed.",
            status=400,
            code="unsupported_sql",
        )


def _sqlite_authorizer(action, arg1, arg2, database, trigger):
    """What a read may do on SQLite: read the main database, call harmless functions, ask an inspecting pragma. Nothing else."""
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if database in ("main", None) else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return (
            sqlite3.SQLITE_DENY
            if str(arg2).lower() in SQLITE_DENIED_FUNCTIONS
            else sqlite3.SQLITE_OK
        )
    if action == sqlite3.SQLITE_PRAGMA:
        name = str(arg1).lower()
        if name in SQLITE_INSPECTING_PRAGMAS or (name in SQLITE_SETTING_PRAGMAS and arg2 is None):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return (
        sqlite3.SQLITE_OK
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE)
        else sqlite3.SQLITE_DENY
    )


async def fetch_read_only(source, sql, params=None):
    """Run a read the database itself holds read-only, on one pinned connection (no connection-level guard survives reuse).

    Postgres: a ``READ ONLY`` transaction. MySQL: ``START TRANSACTION READ ONLY``, rolled back. SQLite: an authorizer that refuses
    anything but reading.
    """
    if not is_read_only(sql, source.dialect):
        raise PawabaseError(
            "Only a single read query is allowed.", status=400, code="write_refused"
        )
    if source.dialect == "postgres":
        async with source.transaction() as transaction:
            await transaction.execute_query("SET TRANSACTION READ ONLY")
            return await timed(transaction, "execute_query_dict", sql, params)
    if source.dialect == "sqlite":
        # Hold Tortoise's connection lock while installing/removing the guard;
        # use the driver queue so SQLite's thread affinity is respected.
        async with source.client.acquire_connection() as connection:
            await connection._execute(connection._conn.set_authorizer, _sqlite_authorizer)
            try:
                return list(map(dict, await connection.execute_fetchall(sql, params or [])))
            finally:
                await connection._execute(connection._conn.set_authorizer, None)
    if source.dialect == "mysql":
        async with source.client.acquire_connection() as connection, connection.cursor() as cursor:
            await cursor.execute("START TRANSACTION READ ONLY")
            try:
                await cursor.execute(sql, params or None)
                rows = await cursor.fetchall()
                fields = [f[0] for f in cursor.description or []]
                return [dict(zip(fields, row, strict=False)) for row in rows]
            finally:
                await cursor.execute("ROLLBACK")
    raise PawabaseError(
        "Database-enforced read-only SQL is not configured for this driver.",
        status=503,
        code="read_only_unavailable",
    )
