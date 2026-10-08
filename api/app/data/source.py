"""Connections to developers' databases, one per environment.

Sillo Record binds the platform's own models to the API's database. Resource
data lives elsewhere, in the database the developer configured for each
environment, so those connections are opened here with Tortoise's own client
classes (the engine under Sillo Record) and kept in a pool keyed by URL.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from typing import Any

from tortoise.backends.base.config_generator import expand_db_url

from pawabase_core.telemetry import span

from .sql import dialect_of

_OPS = {"execute_query_dict": "fetch", "execute_query": "execute", "execute_insert": "insert"}


async def timed(client: Any, method: str, sql: str, params: list[Any] | None = None) -> Any:
    """Run one statement on *client* (a connection or a transaction) as a ``db`` span.

    The span records the statement, how many parameters it bound (never their
    values) and, for reads, how many rows came back.
    """
    params = params or []
    with span("db", sql, op=_OPS.get(method, method), params=len(params)) as step:
        result = await getattr(client, method)(sql, params)
        if method == "execute_query_dict":
            step.set(rows=len(result))
        return result


class DataSourceError(RuntimeError):
    """The environment's database is unreachable or misconfigured."""


class DataSource:
    """One open connection to a developer database.

    Attributes:
        url: The connection URL (credentials included, never logged).
        dialect: ``sqlite``, ``postgres`` or ``mysql``.
        query_class: The PyPika query class for this dialect.
    """

    def __init__(self, url: str, alias: str) -> None:
        self.url = url
        self.alias = alias
        self.client: Any = None
        #: What the URL said about where the data sits: a Postgres schema, or a SQLite file.
        self.schema: str | None = None
        self.file_path: str | None = None
        self.dialect = "sqlite"
        self.query_class: Any = None
        self._lock = asyncio.Lock()

    async def connect(self) -> DataSource:
        async with self._lock:
            if self.client is not None:
                return self
            try:
                info = expand_db_url(self.url)
            except Exception as exc:
                raise DataSourceError(f"invalid database URL: {exc}") from exc
            credentials = dict(info["credentials"])
            file_path = credentials.get("file_path")
            if file_path and file_path != ":memory:":
                Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            module = importlib.import_module(info["engine"])
            client_class = (
                module.get_client_class(info)
                if hasattr(module, "get_client_class")
                else module.client_class
            )
            client = client_class(connection_name=self.alias, **credentials)
            try:
                await client.create_connection(with_db=True)
            except Exception as exc:
                raise DataSourceError(
                    f"could not connect to the environment database: {type(exc).__name__}: {exc}"
                ) from exc
            schema = credentials.get("schema")
            if schema:
                # The environment's own schema on a shared Postgres database: made before anything asks for a table in it.
                await client.execute_script(
                    f'CREATE SCHEMA IF NOT EXISTS "{str(schema).replace(chr(34), "")}"'
                )
            self.client = client
            self.schema = str(schema) if schema else None
            self.file_path = str(file_path) if file_path and file_path != ":memory:" else None
            self.dialect = dialect_of(client)
            self.query_class = client.query_class
            return self

    async def fetch(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        await self.connect()
        return [dict(row) for row in await timed(self.client, "execute_query_dict", sql, params)]

    async def execute(self, sql: str, params: list[Any] | None = None) -> int:
        await self.connect()
        count, _ = await timed(self.client, "execute_query", sql, params)
        return count

    async def insert(self, sql: str, params: list[Any]) -> Any:
        """Run an INSERT; returns the new row id on SQLite and MySQL."""
        await self.connect()
        return await timed(self.client, "execute_insert", sql, params)

    async def size_bytes(self) -> int | None:
        """How much space the data takes: the schema (Postgres), the file (SQLite) or the database (MySQL).

        ``None`` when it cannot be told, which is not the same as empty.
        """
        await self.connect()
        try:
            if self.dialect == "sqlite":
                return os.path.getsize(self.file_path) if self.file_path else None
            if self.dialect == "postgres":
                if self.schema:
                    rows = await self.fetch(
                        "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint AS size "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = $1 AND c.relkind IN ('r', 'm')",
                        [self.schema],
                    )
                else:
                    rows = await self.fetch("SELECT pg_database_size(current_database()) AS size")
                return int(rows[0]["size"])
            if self.dialect == "mysql":
                rows = await self.fetch(
                    "SELECT COALESCE(SUM(data_length + index_length), 0) AS size "
                    "FROM information_schema.tables WHERE table_schema = DATABASE()"
                )
                return int(rows[0]["size"])
        except Exception:  # a size is a courtesy, never a failure
            return None
        return None

    async def script(self, sql: str) -> None:
        await self.connect()
        await self.client.execute_script(sql)

    def transaction(self) -> Any:
        """An async context manager yielding a transactional client."""
        return self.client._in_transaction()

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None


class DataSourcePool:
    """Every open developer connection, by URL."""

    def __init__(self) -> None:
        self._sources: dict[str, DataSource] = {}

    async def get(self, url: str, alias: str) -> DataSource:
        source = self._sources.get(url)
        if source is None:
            source = self._sources[url] = DataSource(url, alias)
        return await source.connect()

    async def close(self) -> None:
        for source in list(self._sources.values()):
            await source.close()
        self._sources.clear()

    def stats(self) -> list[dict[str, Any]]:
        return [
            {"alias": s.alias, "dialect": s.dialect, "connected": s.client is not None}
            for s in self._sources.values()
        ]
