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
from urllib.parse import urlsplit, urlunsplit

import asyncpg

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


async def ensure(url: str, *, attempts: int = 30) -> bool:
    """Create the database in *url* if missing. Returns whether it had to be created."""
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not NAME.match(name):
        raise SystemExit(f"cannot create a database called {name!r}")
    admin = urlunsplit(parts._replace(path="/postgres"))
    last: Exception | None = None
    for _ in range(attempts):
        try:
            connection = await asyncpg.connect(admin)
            break
        except (OSError, asyncpg.PostgresError) as error:  # Postgres still starting
            last = error
            await asyncio.sleep(2)
    else:
        raise SystemExit(f"cannot reach Postgres: {last}")
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
