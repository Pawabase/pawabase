"""Developer SQL on a shared Postgres database, kept inside one environment.

Environments on the platform's default Postgres share one database, one schema each. A ``search_path`` only says where unqualified
names resolve; ``SELECT * FROM pb_other.orders`` or ``FROM public.pb_users`` would still reach another environment or the platform's
own tables if the SQL ran as the platform's user. So SQL a developer writes runs as a *login role of its own* for each environment:

- it may use and create in its environment's schema and owns that schema's tables, so the console can still alter and drop them;
- it is granted nothing elsewhere (other environments' schemas and the platform's tables are not open to ``PUBLIC``);
- it logs in as itself rather than switching role on the platform's connection, so no statement (``SET ROLE``,
  ``set_config('role', …)``, a function body doing either) can climb back to the platform's user;
- its password is derived from ``PAWABASE_MASTER_KEY``, so every process computes it and nothing is stored.

The platform's user is made a member of the role, so it keeps full access to tables the console creates. Making the role takes
``CREATEROLE`` (or a superuser); without it developer SQL is refused rather than run unconfined — set ``PAWABASE_SQL_ISOLATION=off``
on a single-tenant install to accept that. The catalogue (``pg_catalog``) stays readable, as on any Postgres: names of other
schemas can be listed, their contents cannot.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from pawabase_server.core.errors import PawabaseError
from pawabase_server.data.source import DataSource

#: Schemas whose role is known to be in place in this process, with its grants.
_ready: set[tuple[str, str]] = set()
_lock = asyncio.Lock()

TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER"


def role_name(schema: str) -> str:
    """The role for one schema: stable, short and free of characters needing quotes."""
    return "pb_sql_" + hashlib.sha256(schema.encode()).hexdigest()[:20]


def role_password(master_key: str, schema: str) -> str:
    return hmac.new(
        master_key.encode(), f"pawabase:sql-role:{schema}".encode(), hashlib.sha256
    ).hexdigest()


def role_url(url: str, role: str, password: str) -> str:
    """*url* with its credentials replaced by the role's."""
    parts = urlsplit(url)
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit(
        parts._replace(netloc=f"{quote(role, safe='')}:{quote(password, safe='')}@{host}")
    )


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def ensure_role(source: DataSource, role: str, password: str) -> None:
    """Make or refresh the role for *source*'s schema; idempotent. Raises when the platform's user may not."""
    schema = _ident(source.schema or "")
    quoted = _ident(role)
    # ``role`` and ``password`` are hex and a fixed prefix: safe inside the literal.
    await source.client.execute_script(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            CREATE ROLE {quoted} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{password}';
          ELSE
            ALTER ROLE {quoted} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{password}';
          END IF;
        END
        $$;
        GRANT {quoted} TO CURRENT_USER;
        GRANT USAGE, CREATE ON SCHEMA {schema} TO {quoted};
        GRANT {TABLE_PRIVILEGES} ON ALL TABLES IN SCHEMA {schema} TO {quoted};
        GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {schema} TO {quoted};
        GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {quoted};
        ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT {TABLE_PRIVILEGES} ON TABLES TO {quoted};
        ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {quoted};
        """
    )
    await adopt_tables(source, role)


async def adopt_tables(source: DataSource, role: str) -> None:
    """Give the role ownership of the schema's tables, so console DDL (``ALTER TABLE``, ``DROP``) works on tables the platform made.

    The platform's user, a member of the role, keeps every right an owner has.
    """
    rows = await source.client.execute_query_dict(
        "SELECT c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = $1 AND c.relkind IN ('r', 'p', 'v', 'm', 'f') AND pg_get_userbyid(c.relowner) <> $2",
        [source.schema, role],
    )
    schema = _ident(source.schema or "")
    for row in rows:
        await source.client.execute_script(
            f"ALTER TABLE {schema}.{_ident(row['name'])} OWNER TO {_ident(role)}"
        )


async def developer_source(platform: Any, state: Any) -> DataSource:
    """The connection developer SQL runs on for *state*'s environment.

    The environment's own connection when it does not share a Postgres database by schema (its own database or file), or when
    isolation is off; otherwise the schema's own role (see the module docstring).
    """
    source = await state.source()
    if (
        source.dialect != "postgres"
        or not source.schema
        or platform.settings.sql_isolation == "off"
    ):
        return source
    role = role_name(source.schema)
    password = role_password(platform.settings.master_key, source.schema)
    key = (source.url, role)
    if key not in _ready:
        async with _lock:
            if key not in _ready:
                try:
                    await ensure_role(source, role, password)
                except Exception as exc:
                    raise PawabaseError(
                        "SQL cannot be confined to this environment: the platform's database user could not set up the "
                        f"environment's SQL role ({type(exc).__name__}: {exc}). Grant it CREATEROLE, or set "
                        "PAWABASE_SQL_ISOLATION=off on a single-tenant install.",
                        status=503,
                        code="sql_isolation_unavailable",
                    ) from exc
                _ready.add(key)
    else:
        # Tables made since (a new resource): hand them over too. One catalogue query.
        await adopt_tables(source, role)
    return await platform.sources.get(
        role_url(source.url, role, password), alias=f"{state.env_name}:sql"
    )
