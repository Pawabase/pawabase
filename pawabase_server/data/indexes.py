"""The indexes a resource declares, checked and turned into SQL.

A definition lists the indexes it wants beyond the single-column ones fields ask for (``indexed`` / ``unique``)::

    {"name": "recent_by_tenant", "columns": ["tenant_id", {"name": "created_at", "desc": true}], "unique": false,
     "where": [{"column": "deleted_at", "op": "is_null"}], "method": "btree"}

A partial index's condition is *structured*, never a SQL string: each part is a column, an operator and a literal, and the SQL is built
from those pieces, so a definition cannot smuggle a statement into a ``CREATE INDEX``. Declared indexes are created under a name of
Pawabase's own (``pbx_<table>_<name>``), which is how an index it manages is told from one somebody made by hand.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pawabase_server.data.sql import SqlError, check_identifier, quote

MAX_INDEXES = 16
MAX_COLUMNS = 8
MAX_CONDITIONS = 4
NAME = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
PREFIX = "pbx_"
OPERATORS = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
NULL_OPERATORS = {"is_null": "IS NULL", "not_null": "IS NOT NULL"}
#: What each database can build. MySQL has no partial indexes; Hash and BRIN are PostgreSQL's.
CAPABILITIES: dict[str, dict[str, Any]] = {
    "sqlite": {"partial": True, "methods": ["btree"], "descending": True, "concurrent": False},
    "postgres": {
        "partial": True,
        "methods": ["btree", "hash", "brin"],
        "descending": True,
        "concurrent": True,
    },
    "mysql": {"partial": False, "methods": ["btree"], "descending": True, "concurrent": False},
}


def managed_prefix(table: str) -> str:
    """What every index Pawabase declares for *table* starts with. A long table name is shortened, with a hash so two stay apart."""
    tag = (
        table
        if len(table) <= 20
        else f"{table[:14]}{hashlib.sha1(table.encode(), usedforsecurity=False).hexdigest()[:5]}"
    )  # noqa: S324
    return f"{PREFIX}{tag}_"


def logical_name(physical: str, table: str) -> str | None:
    """The declared name behind a managed index's physical name, or None when it is not one of this table's."""
    prefix = managed_prefix(table)
    match = re.match(rf"^{re.escape(prefix)}([a-z][a-z0-9_]*)_[0-9a-f]{{6}}$", physical)
    return match.group(1) if match else None


@dataclass
class Declared:
    """One declared index, normalised."""

    name: str
    columns: list[dict[str, Any]]
    unique: bool = False
    where: list[dict[str, Any]] = field(default_factory=list)
    method: str = "btree"
    concurrent: bool = False
    note: str = ""

    def fingerprint(self) -> str:
        """Six characters that change when anything about the index's shape does."""
        text = json.dumps(self.signature(), default=str, sort_keys=True)
        return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:6]  # noqa: S324

    def physical(self, table: str) -> str:
        """The name the index has in the database: ``pbx_<table>_<name>_<fingerprint>``.

        The fingerprint is what lets Pawabase see that a declared index was *edited*: the edited index has a new name, so the old one is
        found without a trace of its own definition, and the two are swapped.
        """
        return check_identifier(f"{managed_prefix(table)}{self.name}_{self.fingerprint()}")

    @property
    def column_names(self) -> list[str]:
        return [c["name"] for c in self.columns]

    def view(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "columns": [
                {"name": c["name"], **({"desc": True} if c.get("desc") else {})}
                for c in self.columns
            ],
            "unique": self.unique,
        }
        if self.where:
            out["where"] = self.where
        if self.method != "btree":
            out["method"] = self.method
        if self.concurrent:
            out["concurrent"] = True
        if self.note:
            out["note"] = self.note
        return out

    def signature(self) -> tuple:
        """What makes two indexes the same index: columns and order, uniqueness, condition and method."""
        return (
            tuple((c["name"], bool(c.get("desc"))) for c in self.columns),
            self.unique,
            tuple((w["column"], w["op"], repr(w.get("value"))) for w in self.where),
            self.method,
        )


def clean(
    raw: Any,
    columns: set[str],
    *,
    dialect: str | None = None,
    strict: bool = True,
) -> list[Declared]:
    """Declared indexes from a definition, validated against the resource's columns (and the database, when known).

    Raises :class:`~pawabase_server.data.sql.SqlError` listing every problem when ``strict``; otherwise bad entries are dropped.
    """
    if raw in (None, ""):
        return []
    problems: list[str] = []
    out: list[Declared] = []
    if not isinstance(raw, list):
        raise SqlError("indexes must be a list")
    if len(raw) > MAX_INDEXES:
        problems.append(f"a resource may declare at most {MAX_INDEXES} indexes")
    caps = CAPABILITIES.get(dialect or "") if dialect else None
    seen_names: set[str] = set()
    seen_shapes: dict[tuple, str] = {}
    for position, item in enumerate(raw[:MAX_INDEXES]):
        where = f"indexes[{position}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not NAME.match(name):
            problems.append(
                f"{where}.name must be lowercase letters, digits and underscores, starting with a letter"
            )
            continue
        where = f"index {name}"
        if name in seen_names:
            problems.append(f"{where} appears twice")
            continue
        entries = item.get("columns")
        if not isinstance(entries, list) or not entries:
            problems.append(f"{where} needs at least one column")
            continue
        if len(entries) > MAX_COLUMNS:
            problems.append(f"{where} may cover at most {MAX_COLUMNS} columns")
            continue
        cols: list[dict[str, Any]] = []
        ok = True
        for entry in entries:
            column = (
                entry
                if isinstance(entry, str)
                else (entry or {}).get("name")
                if isinstance(entry, dict)
                else None
            )
            if not isinstance(column, str) or column not in columns:
                problems.append(f"{where}: {column!r} is not a column of this resource")
                ok = False
                break
            if any(c["name"] == column for c in cols):
                problems.append(f"{where}: {column} is listed twice")
                ok = False
                break
            cols.append(
                {
                    "name": column,
                    **({"desc": True} if isinstance(entry, dict) and entry.get("desc") else {}),
                }
            )
        if not ok:
            continue
        conditions: list[dict[str, Any]] = []
        for condition in item.get("where") or []:
            if not isinstance(condition, dict):
                problems.append(f"{where}: each condition must be an object")
                ok = False
                break
            column, op = condition.get("column"), condition.get("op")
            if column not in columns:
                problems.append(f"{where}: condition on unknown column {column!r}")
                ok = False
                break
            if op in NULL_OPERATORS:
                conditions.append({"column": column, "op": op})
                continue
            value = condition.get("value")
            if op not in OPERATORS or isinstance(value, (dict, list)) or value is None:
                problems.append(
                    f"{where}: a condition is a column with one of {', '.join([*OPERATORS, *NULL_OPERATORS])} and a plain value"
                )
                ok = False
                break
            if isinstance(value, str) and len(value) > 200:
                problems.append(f"{where}: a condition value is too long")
                ok = False
                break
            conditions.append({"column": column, "op": op, "value": value})
        if not ok:
            continue
        if len(conditions) > MAX_CONDITIONS:
            problems.append(f"{where} may have at most {MAX_CONDITIONS} conditions")
            continue
        method = item.get("method", "btree")
        if method not in ("btree", "hash", "brin"):
            problems.append(f"{where}: method must be btree, hash or brin")
            continue
        unique = bool(item.get("unique"))
        if method != "btree" and unique:
            problems.append(f"{where}: only a btree index can be unique")
            continue
        if method == "hash" and len(cols) > 1:
            problems.append(f"{where}: a hash index covers one column")
            continue
        if caps is not None:
            if conditions and not caps["partial"]:
                problems.append(
                    f"{where}: this database does not support partial indexes (conditions)"
                )
                continue
            if method not in caps["methods"]:
                problems.append(f"{where}: {method} indexes are not available on this database")
                continue
        declared = Declared(
            name=name,
            columns=cols,
            unique=unique,
            where=conditions,
            method=method,
            concurrent=bool(item.get("concurrent")) and bool(caps and caps["concurrent"]),
            note=str(item.get("note") or "")[:200],
        )
        shape = declared.signature()
        if shape in seen_shapes:
            problems.append(f"{where} duplicates index {seen_shapes[shape]}")
            continue
        seen_names.add(name)
        seen_shapes[shape] = name
        out.append(declared)
    if problems and strict:
        raise SqlError("; ".join(problems))
    return out


def literal(value: Any) -> str:
    """A SQL literal for a condition's value. Strings are quoted with their quotes doubled; nothing else can reach the statement."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("\x00", "").replace("'", "''")
    return f"'{text}'"


def where_sql(dialect: str, conditions: list[dict[str, Any]]) -> str:
    parts = []
    for condition in conditions:
        column = quote(dialect, check_identifier(condition["column"]))
        if condition["op"] in NULL_OPERATORS:
            parts.append(f"{column} {NULL_OPERATORS[condition['op']]}")
        else:
            parts.append(f"{column} {OPERATORS[condition['op']]} {literal(condition['value'])}")
    return " AND ".join(parts)


def create_sql(dialect: str, table: str, index: Declared) -> str:
    """The statement that builds one declared index."""
    kind = "UNIQUE INDEX" if index.unique else "INDEX"
    concurrently = "CONCURRENTLY " if index.concurrent and dialect == "postgres" else ""
    exists = "" if dialect == "mysql" else "IF NOT EXISTS "
    using = f" USING {index.method}" if dialect == "postgres" and index.method != "btree" else ""
    columns = ", ".join(
        quote(dialect, c["name"]) + (" DESC" if c.get("desc") and index.method == "btree" else "")
        for c in index.columns
    )
    statement = (
        f"CREATE {kind} {concurrently}{exists}{quote(dialect, index.physical(table))} "
        f"ON {quote(dialect, table)}{using} ({columns})"
    )
    if index.where:
        statement += f" WHERE {where_sql(dialect, index.where)}"
    return statement


def duplicates_sql(dialect: str, table: str, index: Declared, limit: int = 3) -> str:
    """A query that lists values that would stop a unique index being built (no rows: it can be built)."""
    columns = ", ".join(quote(dialect, name) for name in index.column_names)
    not_null = " AND ".join(f"{quote(dialect, name)} IS NOT NULL" for name in index.column_names)
    extra = f" AND {where_sql(dialect, index.where)}" if index.where else ""
    return (
        f"SELECT {columns}, COUNT(*) AS n FROM {quote(dialect, table)} WHERE {not_null}{extra} "
        f"GROUP BY {columns} HAVING COUNT(*) > 1 LIMIT {int(limit)}"
    )
