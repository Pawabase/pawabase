"""Keeping a resource's table in step with its definition, with a history that can be undone.

A definition says what a table should look like; the database says what it does look like. The difference is a *plan*: a list of
changes, each with the statements that make it and the statements that undo it. Nothing is applied until a person applies it.

Nothing is dropped on the way. A column a migration retires (a field removed, a type changed) and a column a rollback gives up are
*parked*: renamed to ``_pbm_<migration>_<name>`` and left in place, so a rollback restores the data it held. Parked columns are
hidden from the API and removed for good only by an explicit purge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pawabase_server.core.ids import new_ulid
from pawabase_server.data import inspect as db_inspect
from pawabase_server.data.source import DataSource
from pawabase_server.data.sql import (
    PARKED,
    TIMESTAMP_FIELDS,
    SqlError,
    add_column_sql,
    cast_type,
    column_type,
    create_index_sql,
    create_table_sql,
    drop_column_sql,
    drop_index_sql,
    drop_table_sql,
    index_name,
    quote,
    rename_column_sql,
    rename_table_sql,
)
from pawabase_server.data.store import ResourceSpec
from pawabase_server.db.models import SchemaMigration

#: Changes that only add: applied without a second thought.
SAFE_OPS = ("create_table", "add_column", "add_index")
#: Changes that retire or rewrite data (always reversible until purged, but a person should look first).
DESTRUCTIVE_OPS = ("retype_column", "drop_column")


class SchemaError(Exception):
    """A change that cannot be made, with the reason."""

    def __init__(self, message: str, migration: SchemaMigration | None = None) -> None:
        super().__init__(message)
        self.migration = migration


@dataclass
class Change:
    """One difference between the definition and the table."""

    op: str
    column: str | None
    detail: str
    #: ``(statement, how to undo it)`` pairs, in the order they run.
    steps: list[tuple[str, str | None]] = field(default_factory=list)
    #: Names a rollback restores from (parked columns, a parked table).
    needs: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.op}:{self.column or ''}"

    @property
    def safe(self) -> bool:
        return self.op in SAFE_OPS

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "column": self.column,
            "detail": self.detail,
            "safe": self.safe,
            "sql": [up for up, _ in self.steps],
        }


@dataclass
class Observed:
    """What the database has for one resource's table."""

    exists: bool
    columns: list[dict[str, Any]]
    index_names: dict[str, bool]
    parked: list[dict[str, Any]]


def family(declared: str) -> str:
    """A column type reduced to what matters for drift: ``VARCHAR(255)`` and ``text`` are both ``text``."""
    kind = re.sub(r"\(.*\)", "", str(declared or "").lower()).strip()
    if "bool" in kind or kind == "tinyint":
        return "bool"
    if "json" in kind:
        return "json"
    if "uuid" in kind:
        return "uuid"
    if "timestamp" in kind or "datetime" in kind:
        return "timestamp"
    if kind == "date":
        return "date"
    if any(word in kind for word in ("double", "float", "real", "numeric", "decimal")):
        return "real"
    if "int" in kind or kind == "serial":
        return "integer"
    return "text"


def parked_name(migration_id: str, original: str, *, given_up: bool = False) -> str:
    """Where a retired column or table is kept. ``given_up`` marks what a rollback parked, apart from what the migration did."""
    name = f"_pbm_{migration_id[-8:].lower()}{'x' if given_up else ''}_{original}"
    if len(name) > 63:
        raise SchemaError(
            f"{original!r} is too long to be kept for a rollback ({len(name)} characters, the limit is 63)"
        )
    return name


async def observe(source: DataSource, spec: ResourceSpec) -> Observed:
    tables = await db_inspect.list_tables(source)
    exists = spec.table in tables
    parked: list[dict[str, Any]] = []
    pattern = re.compile(rf"^_pbm_([0-9a-z]{{8}})(x?)_{re.escape(spec.table)}$")
    for name in tables:
        match = pattern.match(name)
        if match:
            parked.append(
                {"kind": "table", "name": name, "original": spec.table, "migration": match[1]}
            )
    columns: list[dict[str, Any]] = []
    index_names: dict[str, bool] = {}
    if exists:
        columns = await db_inspect.list_columns(source, spec.table)
        for index in await db_inspect.list_indexes(source, spec.table):
            index_names[index["name"]] = bool(index.get("unique"))
        for column in columns:
            match = PARKED.match(column["name"])
            if match:
                parked.append(
                    {
                        "kind": "column",
                        "name": column["name"],
                        "original": match[3],
                        "migration": match[1],
                    }
                )
    return Observed(exists, columns, index_names, parked)


def plan(dialect: str, spec: ResourceSpec, seen: Observed, migration_id: str) -> list[Change]:
    """The changes that bring the table in line with the definition."""
    table = spec.table
    changes: list[Change] = []
    owner = spec.owner_field and spec.owner_field not in {f["name"] for f in spec.fields}
    wanted_index = {
        column["name"]: bool(column.get("unique"))
        for column in spec.fields
        if column.get("indexed") or column.get("unique")
    }
    if spec.owner_field:
        wanted_index.setdefault(spec.owner_field, False)

    def index_change(name: str, unique: bool) -> Change:
        label = f"{'unique ' if unique else ''}index on {name}"
        return Change(
            "add_index",
            name,
            f"Add {label}",
            [
                (
                    create_index_sql(dialect, table, name, unique=unique),
                    drop_index_sql(dialect, table, index_name(table, name)),
                )
            ],
        )

    if not seen.exists:
        steps: list[tuple[str, str | None]] = [
            (
                create_table_sql(
                    dialect, table, spec.primary_key, spec.id_type, spec.fields, spec.timestamps
                ),
                rename_table_sql(dialect, table, parked_name(migration_id, table, given_up=True)),
            )
        ]
        if owner:
            steps.append((add_column_sql(dialect, table, spec.field_map[spec.owner_field]), None))
        changes.append(Change("create_table", None, f"Create table {table}", steps))
        changes += [index_change(name, unique) for name, unique in wanted_index.items()]
        return changes

    actual = {column["name"]: column for column in seen.columns}
    ignored = {spec.primary_key, *(TIMESTAMP_FIELDS if spec.timestamps else ())}
    for name, column in spec.field_map.items():
        if name == spec.primary_key:
            continue
        if name not in actual:
            changes.append(
                Change(
                    "add_column",
                    name,
                    f"Add column {name} ({column_type(dialect, column).lower()})",
                    [
                        (
                            add_column_sql(dialect, table, column),
                            rename_column_sql(
                                dialect, table, name, parked_name(migration_id, name, given_up=True)
                            ),
                        )
                    ],
                )
            )
            continue
        have = actual[name]["type"]
        want = column_type(dialect, column)
        if name in ignored or family(have) == family(want):
            continue
        parked = parked_name(migration_id, name)
        steps = []
        indexed = index_name(table, name)
        if indexed in seen.index_names:
            steps.append(
                (
                    drop_index_sql(dialect, table, indexed),
                    create_index_sql(dialect, table, name, unique=seen.index_names[indexed]),
                )
            )
        steps += [
            (
                rename_column_sql(dialect, table, name, parked),
                rename_column_sql(dialect, table, parked, name),
            ),
            (
                add_column_sql(dialect, table, column),
                rename_column_sql(
                    dialect, table, name, parked_name(migration_id, name, given_up=True)
                ),
            ),
            (
                f"UPDATE {quote(dialect, table)} SET {quote(dialect, name)} = "
                f"CAST({quote(dialect, parked)} AS {cast_type(dialect, column)})",
                None,
            ),
        ]
        if name in wanted_index:
            steps.append(
                (
                    create_index_sql(dialect, table, name, unique=wanted_index[name]),
                    drop_index_sql(dialect, table, indexed),
                )
            )
        changes.append(
            Change(
                "retype_column",
                name,
                f"Change {name} from {have.lower()} to {want.lower()}; the old values are kept until purged",
                steps,
                [parked],
            )
        )
    for name in actual:
        if name in spec.field_map or name in ignored or name.startswith("_pbm_"):
            continue
        parked = parked_name(migration_id, name)
        steps = []
        indexed = index_name(table, name)
        if indexed in seen.index_names:
            steps.append(
                (
                    drop_index_sql(dialect, table, indexed),
                    create_index_sql(dialect, table, name, unique=seen.index_names[indexed]),
                )
            )
        steps.append(
            (
                rename_column_sql(dialect, table, name, parked),
                rename_column_sql(dialect, table, parked, name),
            )
        )
        changes.append(
            Change(
                "drop_column",
                name,
                f"Remove column {name}, which the definition no longer has; its values are kept until purged",
                steps,
                [parked],
            )
        )
    retyped = {change.column for change in changes if change.op == "retype_column"}
    for name, unique in wanted_index.items():
        if name in retyped or index_name(table, name) in seen.index_names:
            continue
        if name not in actual and name not in spec.field_map:
            continue
        changes.append(index_change(name, unique))
    return changes


# ── reading ──────────────────────────────────────────────────────────────────


def _summary(changes: list[Change]) -> str:
    text = "; ".join(change.detail.split(";")[0] for change in changes)
    return text if len(text) <= 300 else text[:297] + "…"


async def status(source: DataSource, spec: ResourceSpec) -> dict[str, Any]:
    """Whether the table matches the definition, and what would change."""
    seen = await observe(source, spec)
    changes = plan(source.dialect, spec, seen, new_ulid())
    safe = [change for change in changes if change.safe]
    if not seen.exists:
        state = "no_table"
    elif safe:
        state = "pending"
    elif changes:
        state = "review"
    else:
        state = "in_sync"
    return {
        "resource": spec.name,
        "table": spec.table,
        "state": state,
        "exists": seen.exists,
        "changes": [change.view() for change in changes],
        "pending": len(changes),
        "destructive": len([change for change in changes if not change.safe]),
        "retained": seen.parked,
        "columns": [
            {"name": c["name"], "type": c["type"]}
            for c in seen.columns
            if not c["name"].startswith("_pbm_")
        ],
    }


async def history(
    env: str, resource: str, source: DataSource, spec: ResourceSpec
) -> list[dict[str, Any]]:
    """Every migration of the resource, newest first, saying which can be rolled back."""
    rows = await SchemaMigration.filter(env=env, resource=resource).order_by("-number").all()
    seen = await observe(source, spec)
    present = {item["name"] for item in seen.parked} | {c["name"] for c in seen.columns}
    present |= {spec.table} if seen.exists else set()
    latest = next((row for row in rows if row.kind == "apply" and row.status == "applied"), None)
    out = []
    for row in rows:
        reason = None
        if row.kind != "apply" or row.status != "applied":
            reason = {"rolled_back": "already rolled back", "failed": "did not apply"}.get(
                row.status, "a rollback cannot be rolled back; apply the change again instead"
            )
        elif row is not latest:
            reason = "a newer migration has to be rolled back first"
        else:
            missing = [name for name in row.needs if name not in present]
            if missing:
                reason = f"the data it kept ({', '.join(missing)}) was purged"
        out.append(
            {
                "id": row.id,
                "number": row.number,
                "kind": row.kind,
                "status": row.status,
                "source": row.source,
                "summary": row.summary,
                "changes": row.changes,
                "up": row.up,
                "down": row.down,
                "columns": row.columns,
                "error": row.error,
                "actor": row.actor,
                "related_id": row.related_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "can_roll_back": reason is None,
                "cannot_roll_back": reason,
            }
        )
    return out


# ── changing ─────────────────────────────────────────────────────────────────


async def _next_number(env: str, resource: str) -> int:
    last = await SchemaMigration.filter(env=env, resource=resource).order_by("-number").first()
    return (last.number if last else 0) + 1


async def _run(source: DataSource, statements: list[str]) -> tuple[int, Exception | None]:
    done = 0
    for statement in statements:
        try:
            await source.execute(statement)
        except Exception as exc:  # reported with the migration, after undoing what ran
            return done, exc
        done += 1
    return done, None


async def apply(
    env: str,
    source: DataSource,
    spec: ResourceSpec,
    *,
    select: list[str] | None = None,
    allow_destructive: bool = False,
    actor: str | None = None,
    origin: str = "studio",
) -> SchemaMigration | None:
    """Make the planned changes (the safe ones, or those named in ``select``) and record them.

    Returns ``None`` when there was nothing to do. A change that fails is undone and recorded as failed.
    """
    migration_id = new_ulid()
    seen = await observe(source, spec)
    changes = plan(source.dialect, spec, seen, migration_id)
    if select is None:
        chosen = [change for change in changes if change.safe]
    else:
        by_id = {change.id: change for change in changes}
        unknown = [item for item in select if item not in by_id]
        if unknown:
            raise SchemaError(
                f"nothing to do for {', '.join(unknown)}; the table has changed, reload and look again"
            )
        chosen = [change for change in changes if change.id in select]
    if not chosen:
        return None
    if any(not change.safe for change in chosen) and not allow_destructive:
        raise SchemaError(
            "these changes retire or rewrite existing values and need to be confirmed"
        )

    ups: list[str] = []
    downs: list[str | None] = []
    for change in chosen:
        for up, down in change.steps:
            ups.append(up)
            downs.append(down)
    number = await _next_number(env, spec.name)
    done, error = await _run(source, ups)
    if error is not None:
        undo = [down for down in reversed(downs[:done]) if down]
        await _run(source, undo)
        failed = await SchemaMigration.create(
            id=migration_id,
            env=env,
            resource=spec.name,
            table_name=spec.table,
            number=number,
            kind="apply",
            status="failed",
            source=origin,
            summary=_summary(chosen),
            changes=[change.view() for change in chosen],
            up=ups,
            down=[],
            error=str(error),
            actor=actor,
        )
        raise SchemaError(f"{type(error).__name__}: {error}", failed) from error

    after = await observe(source, spec)
    return await SchemaMigration.create(
        id=migration_id,
        env=env,
        resource=spec.name,
        table_name=spec.table,
        number=number,
        kind="apply",
        status="applied",
        source=origin,
        summary=_summary(chosen),
        changes=[change.view() for change in chosen],
        up=ups,
        down=[down for down in reversed(downs) if down],
        needs=[name for change in chosen for name in change.needs]
        + ([spec.table] if any(change.op == "create_table" for change in chosen) else []),
        columns=[
            {"name": c["name"], "type": c["type"]}
            for c in after.columns
            if not c["name"].startswith("_pbm_")
        ],
        actor=actor,
    )


async def roll_back(
    env: str,
    source: DataSource,
    spec: ResourceSpec,
    migration_id: str,
    *,
    actor: str | None = None,
) -> SchemaMigration:
    """Undo the resource's latest applied migration, restoring what it retired."""
    row = await SchemaMigration.get_or_none(id=migration_id, env=env, resource=spec.name)
    if row is None:
        raise SchemaError("no such migration")
    state = next(
        (item for item in await history(env, spec.name, source, spec) if item["id"] == row.id), None
    )
    if state is None or not state["can_roll_back"]:
        raise SchemaError(
            f"this migration cannot be rolled back: {(state or {}).get('cannot_roll_back')}"
        )
    done, error = await _run(source, list(row.down))
    if error is not None:
        raise SchemaError(
            f"the rollback stopped after {done} of {len(row.down)} statements: {error}. "
            "Check the table before trying again."
        )
    number = await _next_number(env, spec.name)
    entry = await SchemaMigration.create(
        env=env,
        resource=spec.name,
        table_name=spec.table,
        number=number,
        kind="rollback",
        status="applied",
        source="rollback",
        summary=f"Rolled back #{row.number}: {row.summary}"[:300],
        changes=row.changes,
        up=list(row.down),
        down=[],
        actor=actor,
        related_id=row.id,
    )
    row.status = "rolled_back"
    row.related_id = entry.id
    await row.save()
    return entry


async def purge(source: DataSource, spec: ResourceSpec) -> list[str]:
    """Remove for good what migrations and rollbacks kept. After this, those migrations cannot be rolled back."""
    seen = await observe(source, spec)
    removed: list[str] = []
    for item in seen.parked:
        if item["kind"] == "column":
            statement = drop_column_sql(source.dialect, spec.table, item["name"])
        else:
            statement = drop_table_sql(source.dialect, item["name"])
        try:
            await source.execute(statement)
        except Exception as exc:
            raise SchemaError(f"could not remove {item['name']}: {exc}") from exc
        removed.append(item["name"])
    return removed


async def apply_safe(
    env: str, source: DataSource, spec: ResourceSpec, *, actor: str | None, origin: str
) -> list[str]:
    """What ``migrate`` always did (create the table, add what is missing), now on the record. Returns the statements."""
    try:
        migration = await apply(env, source, spec, actor=actor, origin=origin)
    except SchemaError as exc:
        raise SqlError(str(exc)) from exc
    return list(migration.up) if migration else []
