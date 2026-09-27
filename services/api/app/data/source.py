"""Connections to developers' databases, one per environment.

Sillo Record binds the platform's own models to the API's database. Resource
data lives elsewhere, in the database the developer configured for each
environment, so those connections are opened here with Tortoise's own client
classes (the engine under Sillo Record) and kept in a pool keyed by URL.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any

from tortoise.backends.base.config_generator import expand_db_url

from .sql import dialect_of


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
            client_class = module.get_client_class(info) if hasattr(module, "get_client_class") else module.client_class
            client = client_class(connection_name=self.alias, **credentials)
            try:
                await client.create_connection(with_db=True)
            except Exception as exc:
                raise DataSourceError(f"could not connect to the environment database: {type(exc).__name__}: {exc}") from exc
            self.client = client
            self.dialect = dialect_of(client)
            self.query_class = client.query_class
            return self

    async def fetch(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        await self.connect()
        return [dict(row) for row in await self.client.execute_query_dict(sql, params or [])]

    async def execute(self, sql: str, params: list[Any] | None = None) -> int:
        await self.connect()
        count, _ = await self.client.execute_query(sql, params or [])
        return count

    async def insert(self, sql: str, params: list[Any]) -> Any:
        """Run an INSERT; returns the new row id on SQLite and MySQL."""
        await self.connect()
        return await self.client.execute_insert(sql, params)

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
        return [{"alias": s.alias, "dialect": s.dialect, "connected": s.client is not None} for s in self._sources.values()]
