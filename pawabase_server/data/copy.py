"""Moving an environment's resource data from one database to another, for example from SQLite files to Postgres.

The target's tables are made from the resource definitions (so they have the target's own column types), then rows are copied across in
batches. Nothing is changed in the source. Running it again is safe: rows already in the target are left as they are.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pawabase_server.data import inspect as db_inspect
from pawabase_server.data.source import DataSource
from pawabase_server.data.sql import (
    decode_value,
    encode_value,
    is_parked,
    quote,
)
from pawabase_server.data.store import ResourceSpec, ResourceStore

BATCH = 500


def _placeholder(dialect: str, index: int) -> str:
    return {"postgres": f"${index}", "mysql": "%s"}.get(dialect, "?")


async def copy_resource(
    source: DataSource,
    target: DataSource,
    spec: ResourceSpec,
    *,
    dry_run: bool = False,
    log: Callable[[str], None] = lambda message: None,
) -> dict[str, Any]:
    """Copy one resource's table. Returns what happened: ``{"resource", "status", "rows", "copied", "skipped_columns"}``."""
    report: dict[str, Any] = {"resource": spec.name, "table": spec.table, "rows": 0, "copied": 0}
    if spec.table not in await db_inspect.list_tables(source):
        return {**report, "status": "no table in the source"}
    have = [
        column["name"]
        for column in await db_inspect.list_columns(source, spec.table)
        if not is_parked(column["name"])
    ]
    columns = [name for name in have if name in spec.field_map]
    report["skipped_columns"] = [name for name in have if name not in spec.field_map]
    if spec.primary_key not in columns:
        return {**report, "status": f"the table has no {spec.primary_key} column"}
    quoted_source = quote(source.dialect, spec.table)
    report["rows"] = int((await source.fetch(f"SELECT COUNT(*) AS n FROM {quoted_source}"))[0]["n"])
    if dry_run:
        return {**report, "status": "would copy"}

    await ResourceStore(target, spec).migrate()
    if target.dialect not in ("postgres", "sqlite"):
        raise ValueError(f"copying into {target.dialect} is not supported")
    names = ", ".join(quote(target.dialect, name) for name in columns)
    marks = ", ".join(_placeholder(target.dialect, i + 1) for i in range(len(columns)))
    insert = (
        f"INSERT INTO {quote(target.dialect, spec.table)} ({names}) VALUES ({marks}) "
        f"ON CONFLICT ({quote(target.dialect, spec.primary_key)}) DO NOTHING"
    )
    select = ", ".join(quote(source.dialect, name) for name in columns)
    order = quote(source.dialect, spec.primary_key)
    offset = 0
    while True:
        rows = await source.fetch(
            f"SELECT {select} FROM {quoted_source} ORDER BY {order} LIMIT {BATCH} OFFSET {offset}"
        )
        if not rows:
            break
        for row in rows:
            values = [
                encode_value(
                    target.dialect,
                    spec.field_map[name],
                    decode_value(spec.field_map[name], row[name]),
                )
                for name in columns
            ]
            report["copied"] += await target.execute(insert, values) or 0
        offset += BATCH
        log(f"  {spec.name}: {min(offset, report['rows'])} of {report['rows']}")
    if target.dialect == "postgres" and spec.id_type == "integer":
        # Explicit ids were written, so the sequence has to be moved past them.
        key = quote(target.dialect, spec.primary_key)
        table = quote(target.dialect, spec.table)
        await target.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{spec.primary_key}'), "
            f"COALESCE((SELECT MAX({key}) FROM {table}), 1))"
        )
    after = int(
        (await target.fetch(f"SELECT COUNT(*) AS n FROM {quote(target.dialect, spec.table)}"))[0][
            "n"
        ]
    )
    return {
        **report,
        "status": "copied" if after >= report["rows"] else "incomplete",
        "in_target": after,
    }
