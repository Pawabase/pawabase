"""Connection-pool settings for Postgres URLs.

Every Pawabase process keeps its own connection pool, and Tortoise's default is one open
connection at least and five at most. A deployment of seven processes therefore holds
about seven idle connections even when nothing is happening, which adds up on a shared
Postgres server. ``PAWABASE_DB_POOL_MIN``, ``PAWABASE_DB_POOL_MAX`` and
``PAWABASE_DB_STATEMENT_CACHE_SIZE`` put those numbers in the URL::

    PAWABASE_DB_POOL_MIN=0      # open connections only while there is work
    PAWABASE_DB_POOL_MAX=3
    PAWABASE_DB_STATEMENT_CACHE_SIZE=0   # needed behind PgBouncer in transaction mode

A value already in the URL is never overridden, and SQLite and MySQL URLs are left alone.
Unset (the default) changes nothing.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

POSTGRES = ("postgres://", "postgresql://", "asyncpg://")


def pooled(
    url: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    statement_cache: int | None = None,
) -> str:
    """*url* with pool limits added as query parameters, for Postgres only."""
    if not url.startswith(POSTGRES):
        return url
    wanted = {
        "minsize": minimum,
        "maxsize": maximum,
        "statement_cache_size": statement_cache,
    }
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    present = {key for key, _ in query}
    added = [
        (key, str(value))
        for key, value in wanted.items()
        if value is not None and key not in present
    ]
    if not added:
        return url
    return urlunsplit(parts._replace(query=urlencode([*query, *added])))
