"""Reading a developer database's structure, for Studio and for migrations."""

from __future__ import annotations

import re
from typing import Any

from pawabase_server.data.source import DataSource
from pawabase_server.data.sql import check_identifier


async def list_tables(source: DataSource) -> list[str]:
    if source.dialect == "sqlite":
        rows = await source.fetch(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    elif source.dialect == "postgres":
        rows = await source.fetch(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
    else:
        rows = await source.fetch(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name"
        )
    return [row["name"] for row in rows]


async def list_columns(source: DataSource, table: str) -> list[dict[str, Any]]:
    check_identifier(table)
    if source.dialect == "sqlite":
        rows = await source.fetch(f'PRAGMA table_info("{table}")')
        return [
            {
                "name": r["name"],
                "type": r["type"],
                "nullable": not r["notnull"],
                "default": r["dflt_value"],
                "primary_key": bool(r["pk"]),
            }
            for r in rows
        ]
    schema = "current_schema()" if source.dialect == "postgres" else "DATABASE()"
    placeholder = "$1" if source.dialect == "postgres" else "%s"
    rows = await source.fetch(
        "SELECT column_name AS name, data_type AS type, is_nullable AS nullable, column_default AS dflt "
        f"FROM information_schema.columns WHERE table_schema = {schema} AND table_name = {placeholder} ORDER BY ordinal_position",
        [table],
    )
    keys = await _primary_keys(source, table)
    return [
        {
            "name": r["name"],
            "type": r["type"],
            "nullable": r["nullable"] == "YES",
            "default": r["dflt"],
            "primary_key": r["name"] in keys,
        }
        for r in rows
    ]


async def _primary_keys(source: DataSource, table: str) -> set[str]:
    placeholder = "$1" if source.dialect == "postgres" else "%s"
    schema = "current_schema()" if source.dialect == "postgres" else "DATABASE()"
    rows = await source.fetch(
        "SELECT kcu.column_name AS name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_name = kcu.table_name "
        f"WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = {schema} AND tc.table_name = {placeholder}",
        [table],
    )
    return {row["name"] for row in rows}


async def list_indexes(source: DataSource, table: str) -> list[dict[str, Any]]:
    """Every index on *table*: ``name``, ``unique``, ``columns`` and, where the database says, ``partial``, ``method``, ``size``, ``auto``.

    ``auto`` marks what the database made for a primary key or a UNIQUE constraint: not something to drop as an index.
    """
    check_identifier(table)
    if source.dialect == "sqlite":
        rows = await source.fetch(f'PRAGMA index_list("{table}")')
        sql = {
            r["name"]: r["sql"]
            for r in await source.fetch(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ?", [table]
            )
        }
        indexes = []
        for row in rows:
            columns = await source.fetch(f'PRAGMA index_xinfo("{row["name"]}")')
            keyed = [c for c in columns if c.get("key")]
            indexes.append(
                {
                    "name": row["name"],
                    "unique": bool(row["unique"]),
                    "columns": [c["name"] for c in keyed if c["name"] is not None],
                    "desc": [c["name"] for c in keyed if c.get("desc") and c["name"] is not None],
                    "partial": bool(row.get("partial")),
                    "method": "btree",
                    "definition": sql.get(row["name"]),
                    "auto": row.get("origin") in ("pk", "u"),
                }
            )
        return indexes
    if source.dialect == "postgres":
        rows = await source.fetch(
            "SELECT i.relname AS name, ix.indisunique AS is_unique, ix.indisprimary AS is_primary, am.amname AS method, "
            "pg_get_expr(ix.indpred, ix.indrelid) AS predicate, pg_relation_size(i.oid) AS size, "
            "pg_get_indexdef(i.oid) AS definition, "
            "ARRAY(SELECT a.attname FROM unnest(ix.indkey) WITH ORDINALITY k(attnum, ord) "
            "JOIN pg_attribute a ON a.attrelid = ix.indrelid AND a.attnum = k.attnum ORDER BY k.ord) AS columns, "
            "EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.oid) AS from_constraint "
            "FROM pg_index ix JOIN pg_class i ON i.oid = ix.indexrelid JOIN pg_class t ON t.oid = ix.indrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace JOIN pg_am am ON am.oid = i.relam "
            "WHERE t.relname = $1 AND n.nspname = current_schema()",
            [table],
        )
        return [
            {
                "name": r["name"],
                "unique": bool(r["is_unique"]),
                "columns": list(r["columns"] or []),
                "desc": [
                    c
                    for c in (r["columns"] or [])
                    if re.search(rf"\b{re.escape(c)}\s+DESC\b", r["definition"] or "")
                ],
                "partial": r["predicate"] is not None,
                "predicate": r["predicate"],
                "method": r["method"],
                "size": int(r["size"] or 0),
                "definition": r["definition"],
                "auto": bool(r["is_primary"] or r["from_constraint"]),
            }
            for r in rows
        ]
    rows = await source.fetch(f"SHOW INDEX FROM `{table}`")
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["Key_name"],
            {
                "name": row["Key_name"],
                "unique": not row["Non_unique"],
                "columns": [],
                "desc": [],
                "partial": False,
                "method": str(row.get("Index_type") or "BTREE").lower(),
                "auto": row["Key_name"] == "PRIMARY",
            },
        )
        entry["columns"].append(row["Column_name"])
        if row.get("Collation") == "D":
            entry["desc"].append(row["Column_name"])
    return list(grouped.values())


async def list_foreign_keys(source: DataSource, table: str) -> list[dict[str, Any]]:
    check_identifier(table)
    if source.dialect == "sqlite":
        rows = await source.fetch(f'PRAGMA foreign_key_list("{table}")')
        return [
            {"column": r["from"], "references_table": r["table"], "references_column": r["to"]}
            for r in rows
        ]
    placeholder = "$1" if source.dialect == "postgres" else "%s"
    rows = await source.fetch(
        "SELECT kcu.column_name AS col, ccu.table_name AS ref_table, ccu.column_name AS ref_col "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name "
        f"WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = {placeholder}",
        [table],
    )
    return [
        {"column": r["col"], "references_table": r["ref_table"], "references_column": r["ref_col"]}
        for r in rows
    ]


async def describe_table(source: DataSource, table: str) -> dict[str, Any]:
    return {
        "name": table,
        "columns": await list_columns(source, table),
        "indexes": await list_indexes(source, table),
        "foreign_keys": await list_foreign_keys(source, table),
    }


READ_ONLY_PREFIXES = ("select", "with", "explain", "pragma", "show", "describe")


def is_read_only(sql: str) -> bool:
    """A conservative check that a statement only reads.

    Used to keep ``db.query`` blocks and Studio's read mode honest. Multiple
    statements are refused outright.
    """
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        return False
    lowered = stripped.lower()
    if not lowered.startswith(READ_ONLY_PREFIXES):
        return False
    forbidden = (
        " insert ",
        " update ",
        " delete ",
        " drop ",
        " alter ",
        " create ",
        " truncate ",
        " grant ",
        " attach ",
    )
    padded = f" {lowered} "
    return not any(word in padded for word in forbidden)
