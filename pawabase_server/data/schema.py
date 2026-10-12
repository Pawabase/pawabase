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
from pawabase_server.data import indexes as declared_indexes
from pawabase_server.data import inspect as db_inspect
from pawabase_server.data.source import DataSource
from pawabase_server.data.sql import (
    PARKED,
    TIMESTAMP_FIELDS,
    SqlError,
    add_column_sql,
    cast_type,
    check_identifier,
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
#: Index changes that are not a plain addition: dropping or rebuilding an index loosens a constraint or slows queries until it is back.
INDEX_REVIEW_OPS = ("drop_index", "replace_index")


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
    #: The declared index this change is about, when it is about one.
    index: str | None = None
    #: A query that must return no rows before the change runs, and what to say when it does (a unique index over duplicates).
    precheck: str | None = None
    precheck_message: str = ""

    @property
    def id(self) -> str:
        if self.index:
            return f"{self.op}:index:{self.index}"
        return f"{self.op}:{self.column or ''}"

    @property
    def safe(self) -> bool:
        return self.op in SAFE_OPS

    def view(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "op": self.op,
            "column": self.column,
            "detail": self.detail,
            "safe": self.safe,
            "sql": [up for up, _ in self.steps],
        }
        if self.index:
            out["index"] = self.index
        return out


@dataclass
class Observed:
    """What the database has for one resource's table."""

    exists: bool
    columns: list[dict[str, Any]]
    index_names: dict[str, bool]
    parked: list[dict[str, Any]]
    #: Every index as the database describes it (see ``inspect.list_indexes``).
    indexes: list[dict[str, Any]] = field(default_factory=list)


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
    indexes: list[dict[str, Any]] = []
    if exists:
        columns = await db_inspect.list_columns(source, spec.table)
        indexes = await db_inspect.list_indexes(source, spec.table)
        for index in indexes:
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
    return Observed(exists, columns, index_names, parked, indexes)


def recreate_sql(dialect: str, table: str, actual: dict[str, Any]) -> str | None:
    """A statement that rebuilds an index the database has, from what the database says about it. None when that is not enough to go on."""
    if actual.get("definition"):
        return str(actual["definition"])
    columns = actual.get("columns") or []
    if not columns or actual.get("partial"):
        return None
    desc = set(actual.get("desc") or [])
    listed = ", ".join(quote(dialect, c) + (" DESC" if c in desc else "") for c in columns)
    kind = "UNIQUE INDEX" if actual.get("unique") else "INDEX"
    exists = "" if dialect == "mysql" else "IF NOT EXISTS "
    return f"CREATE {kind} {exists}{quote(dialect, actual['name'])} ON {quote(dialect, table)} ({listed})"


def _describe_declared(item: declared_indexes.Declared) -> str:
    cols = ", ".join(c["name"] + (" desc" if c.get("desc") else "") for c in item.columns)
    extra = (
        " where "
        + " and ".join(
            f"{w['column']} {w['op'].replace('_', ' ')}"
            + (f" {w['value']!r}" if "value" in w else "")
            for w in item.where
        )
        if item.where
        else ""
    )
    return f"{'unique ' if item.unique else ''}index {item.name} on {cols}{extra}"


def declared_changes(dialect: str, spec: ResourceSpec, seen: Observed) -> list[Change]:
    """Declared indexes against the managed ones the table has: missing ones are added, edited ones swapped, removed ones dropped."""
    table = spec.table
    wanted = declared_indexes.clean(
        spec.indexes, set(spec.field_map), dialect=dialect, strict=False
    )
    by_logical = {item.name: item for item in wanted}
    present = {index["name"]: index for index in seen.indexes}
    stale: dict[str, dict[str, Any]] = {}
    for name, actual in present.items():
        logical = declared_indexes.logical_name(name, table)
        if logical is not None and not actual.get("auto"):
            stale[name] = actual
    wanted_names = {item.physical(table) for item in wanted}
    changes: list[Change] = []

    def duplicates(item: declared_indexes.Declared) -> tuple[str | None, str]:
        if not item.unique or not seen.exists:
            return None, ""
        return (
            declared_indexes.duplicates_sql(dialect, table, item),
            f"Cannot make {item.name} unique: some values already appear more than once",
        )

    for item in wanted:
        physical = item.physical(table)
        if physical in present:
            continue
        previous = next(
            (
                n
                for n in stale
                if declared_indexes.logical_name(n, table) == item.name and n not in wanted_names
            ),
            None,
        )
        create = declared_indexes.create_sql(dialect, table, item)
        drop = drop_index_sql(dialect, table, physical)
        check, message = duplicates(item)
        if previous is not None:
            old = stale.pop(previous)
            changes.append(
                Change(
                    "replace_index",
                    None,
                    f"Rebuild {_describe_declared(item)}; the old index is dropped first",
                    [
                        (
                            drop_index_sql(dialect, table, previous),
                            recreate_sql(dialect, table, old),
                        ),
                        (create, drop),
                    ],
                    index=item.name,
                    precheck=check,
                    precheck_message=message,
                )
            )
        else:
            changes.append(
                Change(
                    "add_index",
                    None,
                    f"Add {_describe_declared(item)}",
                    [(create, drop)],
                    index=item.name,
                    precheck=check,
                    precheck_message=message,
                )
            )
    for name, actual in stale.items():
        if name in wanted_names:
            continue
        logical = declared_indexes.logical_name(name, table) or name
        if logical in by_logical:
            continue
        changes.append(
            Change(
                "drop_index",
                None,
                f"Drop index {logical}, which the definition no longer declares",
                [(drop_index_sql(dialect, table, name), recreate_sql(dialect, table, actual))],
                index=logical,
            )
        )
    return changes


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
        changes += declared_changes(dialect, spec, seen)
        return changes

    actual = {column["name"]: column for column in seen.columns}
    ignored = {spec.primary_key, *(TIMESTAMP_FIELDS if spec.timestamps else ())}

    def riding(column: str) -> list[dict[str, Any]]:
        """Indexes that cover *column* besides its own: a rename would leave them indexing the parked copy, so they step aside."""
        return [
            index
            for index in seen.indexes
            if column in (index.get("columns") or [])
            and not index.get("auto")
            and index["name"] != index_name(table, column)
        ]

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
        stepped_aside = [(i, recreate_sql(dialect, table, i)) for i in riding(name)]
        for index, rebuild in stepped_aside:
            steps.append((drop_index_sql(dialect, table, index["name"]), rebuild))
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
        for index, rebuild in stepped_aside:
            if rebuild:
                steps.append((rebuild, drop_index_sql(dialect, table, index["name"])))
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
        for index in riding(name):
            steps.append(
                (drop_index_sql(dialect, table, index["name"]), recreate_sql(dialect, table, index))
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
    changes += declared_changes(dialect, spec, seen)
    return changes


# ── changes made outside Pawabase ────────────────────────────────────────────

_FIELD_TYPES = {
    "bool": "boolean",
    "json": "json",
    "uuid": "uuid",
    "timestamp": "datetime",
    "date": "date",
    "real": "number",
    "integer": "integer",
}

#: Tables Pawabase or its framework own; they are never offered as resources.
INTERNAL_TABLE_PREFIXES = ("pb_", "akz_", "perm_", "_pbm_", "sqlite_", "aerich", "tortoise")


def field_type_for(declared: str, dialect: str = "sqlite", existing: str | None = None) -> str:
    """The definition's field type for a database column type. Keeps ``existing`` when it already fits."""
    kind = family(declared)
    if existing and family(column_type(dialect, {"type": existing})) == kind:
        return existing
    if kind == "text":
        return "text" if "text" in str(declared).lower() and dialect != "sqlite" else "string"
    return _FIELD_TYPES.get(kind, "string")


def snapshot_of(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": c["name"], "type": c["type"]} for c in columns if not c["name"].startswith("_pbm_")
    ]


async def last_snapshot(env: str, resource: str) -> SchemaMigration | None:
    """The newest record of what the table looked like when Pawabase last saw it."""
    rows = await SchemaMigration.filter(env=env, resource=resource).order_by("-number").all()
    return next((row for row in rows if row.columns and row.status != "failed"), None)


def external_changes(
    before: list[dict[str, Any]], live: list[dict[str, Any]], spec: ResourceSpec, dialect: str
) -> list[dict[str, Any]]:
    """What changed in the table since ``before``, which Pawabase did not do."""
    old = {c["name"]: c["type"] for c in before if c["name"] != spec.primary_key}
    new = {c["name"]: c["type"] for c in snapshot_of(live) if c["name"] != spec.primary_key}
    added = {n: t for n, t in new.items() if n not in old}
    removed = {n: t for n, t in old.items() if n not in new}
    out: list[dict[str, Any]] = []
    order_old = [c["name"] for c in before if c["name"] != spec.primary_key]
    order_new = [c["name"] for c in snapshot_of(live) if c["name"] != spec.primary_key]
    for name in list(removed):
        same = [a for a, t in added.items() if family(t) == family(removed[name])]
        peers = [r for r, t in removed.items() if family(t) == family(removed[name])]
        # A rename keeps its place among the columns; failing that, it is the only candidate of its kind.
        place = order_old.index(name)
        mates = [a for a in same if order_new.index(a) == place] or (same if len(same) == 1 else [])
        if len(mates) == 1 and len(peers) == 1:
            out.append(
                {
                    "kind": "renamed",
                    "column": mates[0],
                    "from": name,
                    "type": added[mates[0]],
                    "field_type": field_type_for(added[mates[0]], dialect),
                    "detail": f"{name} was renamed to {mates[0]}",
                }
            )
            del added[mates[0]]
            del removed[name]
    for name, kind in added.items():
        out.append(
            {
                "kind": "added",
                "column": name,
                "type": kind,
                "field_type": field_type_for(kind, dialect),
                "in_definition": name in spec.field_map,
                "detail": f"Column {name} ({kind.lower()}) was added",
            }
        )
    for name in removed:
        out.append(
            {
                "kind": "removed",
                "column": name,
                "type": removed[name],
                "in_definition": name in spec.field_map,
                "detail": f"Column {name} was removed",
            }
        )
    for name, kind in new.items():
        if name in old and family(old[name]) != family(kind):
            out.append(
                {
                    "kind": "retyped",
                    "column": name,
                    "from_type": old[name],
                    "type": kind,
                    "field_type": field_type_for(
                        kind, dialect, (spec.field_map.get(name) or {}).get("type")
                    ),
                    "detail": f"Column {name} changed from {old[name].lower()} to {kind.lower()}",
                }
            )
    return out


def propose_fields(spec: ResourceSpec, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The definition's fields once they follow the table."""
    fields = [dict(f) for f in spec.fields]
    reserved = {spec.primary_key, *TIMESTAMP_FIELDS}
    for change in changes:
        name = change["column"]
        if name in reserved:
            continue
        index = next((i for i, f in enumerate(fields) if f["name"] == name), None)
        if change["kind"] == "added" and index is None:
            fields.append({"name": name, "type": change["field_type"]})
        elif change["kind"] == "removed" and index is not None:
            del fields[index]
        elif change["kind"] == "retyped" and index is not None:
            fields[index]["type"] = change["field_type"]
        elif change["kind"] == "renamed":
            old = next((i for i, f in enumerate(fields) if f["name"] == change["from"]), None)
            if old is not None and index is None:
                fields[old]["name"] = name
            elif old is None and index is None:
                fields.append({"name": name, "type": change["field_type"]})
    return fields


async def record_external(
    env: str,
    source: DataSource,
    spec: ResourceSpec,
    changes: list[dict[str, Any]],
    *,
    actor: str | None,
    origin: str = "external",
    summary: str | None = None,
) -> SchemaMigration:
    """Put what Pawabase now sees on the record, so the next look starts from here."""
    seen = await observe(source, spec)
    return await SchemaMigration.create(
        env=env,
        resource=spec.name,
        table_name=spec.table,
        number=await _next_number(env, spec.name),
        kind="external",
        status="applied",
        source=origin,
        summary=(summary or "Changed outside Pawabase: " + "; ".join(c["detail"] for c in changes))[
            :300
        ],
        changes=changes,
        columns=snapshot_of(seen.columns),
        actor=actor,
    )


async def unclaimed_tables(source: DataSource, specs: list[ResourceSpec]) -> list[dict[str, Any]]:
    """Tables in the database no resource is defined for, with the fields they would become."""
    claimed = {spec.table for spec in specs}
    out = []
    for table in await db_inspect.list_tables(source):
        if table in claimed or table.startswith(INTERNAL_TABLE_PREFIXES):
            continue
        try:
            check_identifier(table)
        except SqlError:
            continue
        columns = await db_inspect.list_columns(source, table)
        out.append(
            {
                "table": table,
                "primary_key": next((c["name"] for c in columns if c["primary_key"]), None),
                "columns": [
                    {
                        "name": c["name"],
                        "type": c["type"],
                        "field_type": field_type_for(c["type"], source.dialect),
                        "primary_key": c["primary_key"],
                        "nullable": c["nullable"],
                    }
                    for c in columns
                ],
            }
        )
    return out


def definition_from_table(table: dict[str, Any], dialect: str) -> tuple[dict[str, Any], list[str]]:
    """A resource's table, primary key, id type, timestamps and fields, read from an existing table."""
    key = table["primary_key"]
    if not key:
        raise SchemaError(
            f"{table['table']} has no primary key, so records could not be told apart"
        )
    pk_column = next(c for c in table["columns"] if c["name"] == key)
    kind = family(pk_column["type"])
    if kind == "uuid":
        id_type = "uuid"
    elif kind == "text" and "26" in str(pk_column["type"]):
        id_type = "ulid"
    else:
        raise SchemaError(
            f"{table['table']} is keyed by {str(pk_column['type']).lower()}; resources are keyed by a ULID or a UUID"
        )
    names = {c["name"] for c in table["columns"]}
    timestamps = set(TIMESTAMP_FIELDS) <= names
    fields: list[dict[str, Any]] = []
    skipped: list[str] = []
    for column in table["columns"]:
        name = column["name"]
        if name == key or (timestamps and name in TIMESTAMP_FIELDS):
            continue
        try:
            check_identifier(name)
        except SqlError:
            skipped.append(name)
            continue
        fields.append({"name": name, "type": field_type_for(column["type"], dialect)})
    return {
        "table": table["table"],
        "primary_key": key,
        "id_type": id_type,
        "timestamps": timestamps,
        "fields_": fields,
    }, skipped


# ── reading ──────────────────────────────────────────────────────────────────


def describe_indexes(dialect: str, spec: ResourceSpec, seen: Observed) -> list[dict[str, Any]]:
    """Every index of the table and every one the definition wants, with who owns it and whether it is as declared.

    ``origin``: ``system`` (a key or a UNIQUE constraint), ``field`` (a field marked indexed or unique), ``declared`` (an index the
    definition lists) or ``external`` (made by hand: Pawabase leaves it alone). ``state``: ``ok``, ``missing`` (declared, not built),
    ``stale`` (an older version of a declared index, or one the definition dropped: applying replaces or removes it).
    """
    table = spec.table
    wanted = declared_indexes.clean(
        spec.indexes, set(spec.field_map), dialect=dialect, strict=False
    )
    declared = {item.physical(table): item for item in wanted}
    by_logical = {item.name: item for item in wanted}
    field_level = {
        index_name(table, f["name"]) for f in spec.fields if f.get("indexed") or f.get("unique")
    }
    if spec.owner_field:
        field_level.add(index_name(table, spec.owner_field))
    out: list[dict[str, Any]] = []
    for actual in seen.indexes:
        name = actual["name"]
        logical = declared_indexes.logical_name(name, table)
        entry: dict[str, Any] = {
            "name": name,
            "physical": name,
            "columns": actual.get("columns") or [],
            "desc": actual.get("desc") or [],
            "unique": bool(actual.get("unique")),
            "partial": bool(actual.get("partial")),
            "predicate": actual.get("predicate"),
            "method": actual.get("method") or "btree",
            "size": actual.get("size"),
            "definition": actual.get("definition"),
            "state": "ok",
            "droppable": False,
        }
        if actual.get("auto"):
            entry["origin"] = "system"
        elif name in declared:
            entry["origin"], entry["name"] = "declared", declared[name].name
        elif logical is not None:
            entry["origin"], entry["name"] = "declared", logical
            entry["state"] = "stale"
            entry["note"] = (
                "an older version of this index; applying replaces it"
                if logical in by_logical
                else "the definition no longer declares it; applying drops it"
            )
        elif name in field_level:
            entry["origin"] = "field"
        else:
            entry["origin"] = "external"
            entry["droppable"] = True
        out.append(entry)
    have = {item["physical"] for item in out}
    for physical, item in declared.items():
        if physical not in have:
            out.append(
                {
                    "name": item.name,
                    "physical": physical,
                    "columns": item.column_names,
                    "desc": [c["name"] for c in item.columns if c.get("desc")],
                    "unique": item.unique,
                    "partial": bool(item.where),
                    "predicate": None,
                    "method": item.method,
                    "size": None,
                    "definition": None,
                    "origin": "declared",
                    "state": "missing",
                    "droppable": False,
                    "note": "declared, not built yet",
                }
            )
    for item in wanted:
        entry = next((e for e in out if e["origin"] == "declared" and e["name"] == item.name), None)
        if entry is not None:
            entry["declared"] = item.view()
    order = {"declared": 0, "field": 1, "external": 2, "system": 3}
    return sorted(out, key=lambda e: (order.get(e["origin"], 9), e["name"]))


def _summary(changes: list[Change]) -> str:
    text = "; ".join(change.detail.split(";")[0] for change in changes)
    return text if len(text) <= 300 else text[:297] + "…"


async def status(env: str, source: DataSource, spec: ResourceSpec) -> dict[str, Any]:
    """Whether the table matches the definition, and what would change."""
    seen = await observe(source, spec)
    changes = plan(source.dialect, spec, seen, new_ulid())
    safe = [change for change in changes if change.safe]
    external: list[dict[str, Any]] = []
    since = None
    if seen.exists:
        snapshot = await last_snapshot(env, spec.name)
        if snapshot is not None:
            external = external_changes(snapshot.columns, seen.columns, spec, source.dialect)
            since = {
                "number": snapshot.number,
                "at": snapshot.created_at.isoformat() if snapshot.created_at else None,
            }
    if not seen.exists:
        state = "no_table"
    elif external:
        state = "external"
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
        "external": external,
        "since": since,
        "proposal": propose_fields(spec, external) if external else None,
        "pending": len(changes),
        "destructive": len([change for change in changes if not change.safe]),
        "retained": seen.parked,
        "indexes": describe_indexes(source.dialect, spec, seen),
        "index_capabilities": declared_indexes.CAPABILITIES.get(source.dialect, {}),
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
        if row.kind == "external":
            reason = "it was made outside Pawabase, so there is nothing to undo it with"
        elif row.kind != "apply" or row.status != "applied":
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
        only_indexes = all(change.op in INDEX_REVIEW_OPS for change in chosen if not change.safe)
        raise SchemaError(
            "these changes drop or rebuild indexes and need to be confirmed"
            if only_indexes
            else "these changes retire or rewrite existing values and need to be confirmed"
        )
    for change in chosen:
        if change.precheck:
            found = await source.fetch(change.precheck)
            if found:
                sample = ", ".join(
                    "(" + ", ".join(str(v) for k, v in row.items() if k != "n") + f") x{row['n']}"
                    for row in found
                )
                raise SchemaError(
                    f"{change.precheck_message}, for example {sample}. Remove or merge them first."
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
        columns=snapshot_of(after.columns),
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
    after = await observe(source, spec)
    entry = await SchemaMigration.create(
        env=env,
        resource=spec.name,
        table_name=spec.table,
        number=number,
        columns=snapshot_of(after.columns),
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


async def drop_external(
    env: str,
    source: DataSource,
    spec: ResourceSpec,
    name: str,
    *,
    actor: str | None = None,
    irreversible: bool = False,
) -> SchemaMigration:
    """Drop an index somebody made by hand, on the record, with the statement that rebuilds it so a rollback can.

    Only an index Pawabase does not manage is dropped here: a key, a constraint, a field's index and a declared index are changed by
    editing the definition, so that the definition and the table cannot disagree.
    """
    seen = await observe(source, spec)
    entry = next(
        (e for e in describe_indexes(source.dialect, spec, seen) if e["physical"] == name), None
    )
    if entry is None:
        raise SchemaError(f"the table has no index {name!r}")
    if entry["origin"] != "external":
        raise SchemaError(
            {
                "system": "this index backs a key or a unique constraint and cannot be dropped on its own",
                "field": "this index follows a field marked indexed or unique: change the field instead",
                "declared": "this index is declared by the definition: remove it from the definition and apply",
            }.get(entry["origin"], "this index cannot be dropped here")
        )
    actual = next(i for i in seen.indexes if i["name"] == name)
    rebuild = recreate_sql(source.dialect, spec.table, actual)
    if rebuild is None and not irreversible:
        raise SchemaError(
            "this index cannot be rebuilt from what the database reports (a partial or expression index), "
            "so dropping it could not be rolled back; confirm to drop it anyway"
        )
    up = drop_index_sql(source.dialect, spec.table, name)
    try:
        await source.execute(up)
    except Exception as exc:
        raise SchemaError(f"could not drop {name}: {exc}") from exc
    after = await observe(source, spec)
    return await SchemaMigration.create(
        env=env,
        resource=spec.name,
        table_name=spec.table,
        number=await _next_number(env, spec.name),
        kind="apply",
        status="applied",
        source="studio",
        summary=f"Drop index {name}",
        changes=[
            {
                "id": f"drop_index:{name}",
                "op": "drop_index",
                "column": None,
                "detail": f"Drop index {name}, made outside Pawabase",
                "safe": False,
                "sql": [up],
                "index": name,
            }
        ],
        up=[up],
        down=[rebuild] if rebuild else [],
        columns=snapshot_of(after.columns),
        actor=actor,
    )


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
