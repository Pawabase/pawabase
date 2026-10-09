"""``python -m pawabase_core.ensure_database [url]``: create the database named in *url* (default PAWABASE_DATABASE_URL) when it does not exist yet.

The bundled Postgres starts with one database; Akountz keeps its own. The development and self-hosted compose files create it with an init script
mounted into Postgres. A host that cannot mount files (a deployment manager that only knows named volumes) sets ``PAWABASE_ENSURE_DATABASE=true`` on
the service instead, and the service makes its own database before it migrates.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def connect_args(url: str) -> tuple[str, dict[str, Any]]:
    """*url* as asyncpg wants it: ``ssl`` and the pool settings are Tortoise's, not libpq's, so they leave the URL (a server that is sent them as startup parameters, PgBouncer for one, refuses the connection)."""
    parts = urlsplit(url)
    kept, options = [], {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in ("ssl", "sslmode"):
            options["ssl"] = value
        elif key not in ("minsize", "maxsize", "statement_cache_size", "schema"):
            kept.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(kept))), options


async def ensure(url: str, *, attempts: int = 30) -> bool:
    """Create the database in *url* if missing. Returns whether it had to be created.

    The database is tried first, as the role in the URL: a role that owns its database (a project's share of a
    shared server) is not allowed into the server's maintenance database and has nothing to create. Only when the
    database is missing is the maintenance database used.
    """
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not NAME.match(name):
        raise SystemExit(f"cannot create a database called {name!r}")
    last: Exception | None = None
    for _ in range(attempts):
        try:
            target, options = connect_args(url)
            connection = await asyncpg.connect(target, **options)
            await connection.fetchval(
                "SELECT 1"
            )  # behind PgBouncer the server login happens on the first query
            await connection.close()
            return False
        except asyncpg.InvalidCatalogNameError:  # missing: create it below
            break
        except (OSError, asyncpg.PostgresError) as error:  # Postgres still starting
            last = error
            await asyncio.sleep(2)
    else:
        raise SystemExit(f"cannot reach Postgres: {last}")
    admin, options = connect_args(urlunsplit(parts._replace(path="/postgres")))
    connection = await asyncpg.connect(admin, **options)
    try:
        if await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            return False
        await connection.execute(f'CREATE DATABASE "{name}"')
        return True
    finally:
        await connection.close()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    url = args[0] if args else os.environ.get("PAWABASE_DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        return  # SQLite and the like create themselves
    created = asyncio.run(ensure(url))
    print(f"database {'created' if created else 'ready'}", file=sys.stderr)


if __name__ == "__main__":
    main()
