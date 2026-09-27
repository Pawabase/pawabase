"""Reading a developer database's structure, for Studio and for migrations."""

from __future__ import annotations

from typing import Any

from .source import DataSource
from .sql import check_identifier


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
    check_identifier(table)
    if source.dialect == "sqlite":
        rows = await source.fetch(f'PRAGMA index_list("{table}")')
        indexes = []
        for row in rows:
            columns = await source.fetch(f'PRAGMA index_info("{row["name"]}")')
            indexes.append(
                {
                    "name": row["name"],
                    "unique": bool(row["unique"]),
                    "columns": [c["name"] for c in columns],
                }
            )
        return indexes
    if source.dialect == "postgres":
        rows = await source.fetch(
            "SELECT indexname AS name, indexdef AS definition FROM pg_indexes WHERE schemaname = current_schema() AND tablename = $1",
            [table],
        )
        return [
            {
                "name": r["name"],
                "unique": "UNIQUE" in r["definition"],
                "definition": r["definition"],
            }
            for r in rows
        ]
    rows = await source.fetch(f"SHOW INDEX FROM `{table}`")
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(
            row["Key_name"],
            {"name": row["Key_name"], "unique": not row["Non_unique"], "columns": []},
        )
        entry["columns"].append(row["Column_name"])
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
