"""Telling a person what to do next when SQL they ran changed the shape of the database.

Someone running ``ALTER TABLE`` in Studio's SQL console, or a migration tool pointed at the same database, changes tables that resources
are defined over. Nothing breaks at once, but the definition and the table no longer agree. :func:`advise` reads the statement and, for
each table it touched, says what it means and the steps that put things right.
"""

from __future__ import annotations

import re
from typing import Any

from pawabase_server.data import schema
from pawabase_server.data.source import DataSource
from pawabase_server.data.store import ResourceSpec

_NAME = r"""(?:[`"\[]?[\w$]+[`"\]]?\.)?[`"\[]?([\w$]+)[`"\]]?"""
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "create_table",
        re.compile(
            rf"^\s*create\s+(?:temp(?:orary)?\s+)?table\s+(?:if\s+not\s+exists\s+)?{_NAME}", re.I
        ),
    ),
    ("drop_table", re.compile(rf"^\s*drop\s+table\s+(?:if\s+exists\s+)?{_NAME}", re.I)),
    (
        "rename_table",
        re.compile(
            rf"^\s*alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_NAME}\s+rename\s+to\s+{_NAME}",
            re.I,
        ),
    ),
    (
        "alter_table",
        re.compile(rf"^\s*alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?{_NAME}", re.I),
    ),
    (
        "create_index",
        re.compile(
            rf"^\s*create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?[\w`\"\[\].$]+\s+on\s+(?:only\s+)?{_NAME}",
            re.I,
        ),
    ),
    ("drop_index", re.compile(r"^\s*drop\s+index\b", re.I)),
    ("truncate", re.compile(rf"^\s*truncate\s+(?:table\s+)?{_NAME}", re.I)),
]


def parse(sql: str) -> list[dict[str, Any]]:
    """What a statement does to the database's structure: ``[{"action", "table", "to"?}]``. Reads and row writes give ``[]``."""
    found = []
    for statement in _COMMENTS.sub("", sql).split(";"):
        for action, pattern in _PATTERNS:
            match = pattern.match(statement)
            if match:
                item: dict[str, Any] = {
                    "action": action,
                    "table": match.group(1) if match.groups() else None,
                }
                if action == "rename_table":
                    item["to"] = match.group(2)
                found.append(item)
                break
    return found


def _warning(
    level: str, title: str, message: str, steps: list[str], **extra: Any
) -> dict[str, Any]:
    return {"level": level, "title": title, "message": message, "steps": steps, **extra}


async def advise(
    env: str, source: DataSource, specs: dict[str, ResourceSpec], sql: str
) -> list[dict[str, Any]]:
    """Warnings, each with the steps to fix it, for the structural changes ``sql`` made."""
    by_table = {spec.table: spec for spec in specs.values()}
    out: list[dict[str, Any]] = []
    unclaimed: dict[str, dict[str, Any]] | None = None

    async def import_advice(table: str) -> dict[str, Any] | None:
        nonlocal unclaimed
        if unclaimed is None:
            unclaimed = {
                t["table"]: t for t in await schema.unclaimed_tables(source, list(specs.values()))
            }
        found = unclaimed.get(table)
        if found is None:
            return None
        try:
            schema.definition_from_table(found, source.dialect)
        except schema.SchemaError as exc:
            return _warning(
                "info",
                f"Table {table} is not a resource",
                f"It exists in the database but Pawabase cannot manage it as it is: {exc}.",
                [
                    "Leave it as it is if only your own SQL or other apps use it.",
                    "To expose it through Pawabase, create it with a CHAR(26) primary key (ULIDs) or a UUID key, "
                    "then use Data, Resources, Import table.",
                ],
            )
        return _warning(
            "warn",
            f"New table {table} is not a resource yet",
            "It exists in the database, but Pawabase is not serving or tracking it.",
            [
                "Open Data, then Resources, and choose Import table.",
                f"Import {table}. The resource starts closed to the API.",
                "Open the resource's policies and decide who may read and write it before using it.",
            ],
            action={"type": "import", "label": "Import table"},
        )

    for change in parse(sql):
        action, table = change["action"], change["table"]
        spec = by_table.get(table or "")
        if action == "create_table":
            if spec is not None:
                report = await schema.status(env, source, spec)
                if report["state"] != "in_sync":
                    out.append(
                        _warning(
                            "warn",
                            f"Table {table} now exists for resource {spec.name}",
                            "Check that it matches the definition.",
                            [
                                f"Open resource {spec.name}, then its Schema tab, and review the changes listed there."
                            ],
                            resource=spec.name,
                            action={
                                "type": "schema",
                                "label": "Open Schema",
                                "resource": spec.name,
                            },
                        )
                    )
            else:
                advice = await import_advice(table or "")
                if advice:
                    out.append(advice)
        elif action == "drop_table":
            if spec is not None:
                out.append(
                    _warning(
                        "error",
                        f"Resource {spec.name} lost its table",
                        f"{spec.name} still points at {table}, which no longer exists. Its API will return errors until this is fixed.",
                        [
                            f"To bring the table back empty: open {spec.name}, its Schema tab, and apply the Create table change.",
                            "To get the data back: restore the environment from a backup (Backups).",
                            f"If the data is gone for good: delete resource {spec.name} so the API stops serving it.",
                        ],
                        resource=spec.name,
                        action={"type": "schema", "label": "Open Schema", "resource": spec.name},
                    )
                )
        elif action == "rename_table":
            new = change.get("to")
            if spec is not None:
                out.append(
                    _warning(
                        "error",
                        f"Resource {spec.name} lost its table by a rename",
                        f"{table} is now called {new}, but {spec.name} still points at {table}.",
                        [
                            f"Rename it back: ALTER TABLE {new} RENAME TO {table}",
                            f"Or open resource {spec.name}, its Configure tab, set Table to {new} and save.",
                            "Then reopen the Schema tab to confirm it says In sync.",
                        ],
                        resource=spec.name,
                        action={"type": "schema", "label": "Open Schema", "resource": spec.name},
                    )
                )
            else:
                advice = await import_advice(new or "")
                if advice:
                    out.append(advice)
        elif action == "alter_table" and spec is not None:
            report = await schema.status(env, source, spec)
            if report["external"]:
                out.append(
                    _warning(
                        "warn",
                        f"Table {table} changed outside Pawabase",
                        "; ".join(c["detail"] for c in report["external"]) + ".",
                        [
                            f"Open resource {spec.name} and its Schema tab.",
                            "Choose Update the definition to match if the table is right and the definition is out of date.",
                            "Choose Keep the definition if the change was a mistake; then undo it in SQL, or apply the definition.",
                        ],
                        resource=spec.name,
                        changes=report["external"],
                        action={"type": "schema", "label": "Open Schema", "resource": spec.name},
                    )
                )
            elif report["state"] != "in_sync":
                out.append(
                    _warning(
                        "warn",
                        f"Table {table} no longer matches resource {spec.name}",
                        f"{report['pending']} difference(s) from the definition.",
                        [
                            f"Open resource {spec.name}, its Schema tab, and review the changes listed."
                        ],
                        resource=spec.name,
                        action={"type": "schema", "label": "Open Schema", "resource": spec.name},
                    )
                )
        elif action == "truncate" and spec is not None:
            out.append(
                _warning(
                    "info",
                    f"All records of {spec.name} were removed",
                    "The table structure is unchanged. Cached reads were cleared.",
                    [
                        "Nothing to fix unless this was not intended; restore from a backup if it was a mistake."
                    ],
                    resource=spec.name,
                )
            )
        elif action in ("create_index", "drop_index"):
            out.append(
                _warning(
                    "info",
                    "An index was changed",
                    "Pawabase keeps the indexes for fields marked indexed or unique. Other indexes are kept but not tracked.",
                    [
                        "If you removed an index Pawabase manages, its Schema tab will list it to add back."
                    ],
                )
            )
    return out
