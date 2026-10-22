"""Types for an environment's resources, for the languages its clients are written in.

The data API's shapes are fully described by the resource definitions: which columns a read returns, which a create requires, which an
update accepts, and what can be null. This turns them into TypeScript (the exact ``Database`` shape ``createClient<Database>()`` takes,
as ``pawabase-types`` produces from the OpenAPI document) and Python ``TypedDict`` classes, directly from the definitions, so a project
does not have to switch on public docs or download a spec first.

Rules, matching what the API enforces (``core.schemas.compile_model``):

* A **row** has every readable column. A column may be null unless it is required or has a non-null default; the key and the
  timestamps never are. Write-only columns are not in it.
* An **insert** has every writable column, required ones without ``?``. Read-only columns, the key and the timestamps are not
  accepted on create.
* An **update** is a PATCH: every writable column, all optional.
* A column accepts null when it is declared ``nullable``, or when it is not ``required`` (the API's own default).
* A resource with a response ``transformer`` shapes its own responses, so its row is left open (``Record<string, unknown>``).
"""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pawabase_server.data.store import TIMESTAMP_FIELDS, ResourceSpec

LANGUAGES = ("typescript", "python")
STRINGLIKE = {"string", "text", "email", "url", "datetime", "date", "uuid", "ulid"}
MAX_DEPTH = 5


def pascal(name: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", name) if part)


def _nullable(spec: Mapping[str, Any]) -> bool:
    return bool(spec.get("nullable", not spec.get("required", False)))


@dataclass
class Column:
    name: str
    spec: Mapping[str, Any]
    #: ``key`` (the primary key), ``timestamp`` or ``field``.
    role: str = "field"

    @property
    def readable(self) -> bool:
        return not self.spec.get("write_only")

    @property
    def writable(self) -> bool:
        return self.role == "field" and not self.spec.get("read_only")

    @property
    def required_on_create(self) -> bool:
        return bool(self.spec.get("required")) and "default" not in self.spec

    @property
    def guaranteed_on_read(self) -> bool:
        if self.role != "field":
            return True
        default = self.spec.get("default", None)
        return bool(self.spec.get("required")) or default is not None


@dataclass
class Resource:
    name: str
    columns: list[Column]
    shaped: bool = False
    realtime: bool = False


def resources_of(state: Any) -> list[Resource]:
    """The environment's resources, in name order, as the type generators read them."""
    found = []
    for name in sorted(state.resources):
        definition = state.resources[name]
        spec: ResourceSpec = state.specs[name]
        columns = []
        key = spec.field_map[spec.primary_key]
        columns.append(Column(spec.primary_key, key, "key"))
        for column, field_spec in spec.field_map.items():
            if column == spec.primary_key:
                continue
            role = "timestamp" if spec.timestamps and column in TIMESTAMP_FIELDS else "field"
            columns.append(Column(column, field_spec, role))
        found.append(
            Resource(
                name=name,
                columns=columns,
                shaped=bool(definition.transformer),
                realtime=bool(definition.realtime),
            )
        )
    return found


# ── TypeScript ───────────────────────────────────────────────────────────────────────────────────


def _ts_key(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) else f'"{name}"'


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ts_type(spec: Mapping[str, Any], schemas: Mapping[str, Any], depth: int = 0) -> str:
    if spec.get("enum"):
        return " | ".join(_literal(v) for v in spec["enum"])
    kind = spec.get("type", "string")
    if kind in STRINGLIKE:
        return "string"
    if kind in ("integer", "number"):
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "json":
        return "unknown"
    if kind == "array":
        inner = _ts_type(spec.get("items") or {"type": "json"}, schemas, depth + 1)
        return f"Array<{inner}>" if " | " in inner else f"{inner}[]"
    if kind == "object":
        return _ts_object(spec.get("fields") or [], schemas, depth + 1)
    if kind == "ref":
        return _ts_object(schemas.get(spec.get("schema")) or [], schemas, depth + 1)
    return "unknown"


def _ts_object(fields: list[Mapping[str, Any]], schemas: Mapping[str, Any], depth: int) -> str:
    if depth > MAX_DEPTH or not fields:
        return "Record<string, unknown>"
    parts = []
    for f in fields:
        if f.get("write_only"):
            continue
        optional = "" if f.get("required") else "?"
        null = " | null" if _nullable(f) else ""
        parts.append(f"{_ts_key(f['name'])}{optional}: {_ts_type(f, schemas, depth)}{null}")
    return "{ " + "; ".join(parts) + " }" if parts else "Record<string, unknown>"


def typescript(state: Any, *, name: str = "Database", source: str | None = None) -> str:
    resources = resources_of(state)
    schemas = state.schemas
    out = [
        "/* eslint-disable */",
        f"// Generated by Pawabase from the environment's resources{f' ({source})' if source else ''}.",
        "// Do not edit by hand: regenerate when your resources change.",
        "",
    ]
    for r in resources:
        type_name = pascal(r.name)
        row = []
        for c in r.columns:
            if not c.readable:
                continue
            base = _ts_type(c.spec, schemas)
            row.append(
                f"  {_ts_key(c.name)}: {base}{'' if c.guaranteed_on_read or base == 'unknown' else ' | null'};"
            )
        out.append(f"export interface {type_name}Row {{")
        out.extend(["  [column: string]: unknown;"] if r.shaped else row)
        out += ["}", ""]
        insert, update = [], []
        for c in r.columns:
            if not c.writable:
                continue
            base = _ts_type(c.spec, schemas)
            null = " | null" if _nullable(c.spec) and base != "unknown" else ""
            insert.append(
                f"  {_ts_key(c.name)}{'' if c.required_on_create else '?'}: {base}{null};"
            )
            update.append(f"  {_ts_key(c.name)}?: {base}{null};")
        out += [f"export interface {type_name}Insert {{", *insert, "}", ""]
        out += [f"export interface {type_name}Update {{", *update, "}", ""]
    out += [f"export interface {name} {{", "  resources: {"]
    for r in resources:
        t = pascal(r.name)
        out.append(
            f"    {_ts_key(r.name)}: {{ Row: {t}Row; Insert: {t}Insert; Update: {t}Update }};"
        )
    out += ["  };", "}", ""]
    out += [
        '/** A resource\'s row: `Row<"posts">`. */',
        f'export type Row<K extends keyof {name}["resources"]> = {name}["resources"][K]["Row"];',
        '/** What a create accepts: `Insert<"posts">`. */',
        f'export type Insert<K extends keyof {name}["resources"]> = {name}["resources"][K]["Insert"];',
        '/** What an update accepts: `Update<"posts">`. */',
        f'export type Update<K extends keyof {name}["resources"]> = {name}["resources"][K]["Update"];',
        "/** A realtime message on `resource:<name>`. `source` is `database` for a change made outside the API (see `capture`). */",
        f'export interface Change<K extends keyof {name}["resources"]> {{',
        "  resource: K;",
        '  change: "created" | "updated" | "deleted";',
        "  record: Row<K>;",
        "  /** For an update: the columns whose values changed. */",
        "  changed?: string[];",
        '  source?: "database";',
        "}",
        "",
    ]
    return "\n".join(out)


# ── Python ───────────────────────────────────────────────────────────────────────────────────────


@dataclass
class _Py:
    schemas: Mapping[str, Any]
    #: Classes to emit before the one being built: nested objects and referenced schemas.
    classes: list[str] = field(default_factory=list)
    made: set[str] = field(default_factory=set)

    def annotation(self, spec: Mapping[str, Any], owner: str, depth: int = 0) -> str:
        if spec.get("enum"):
            return "Literal[" + ", ".join(_literal(v) for v in spec["enum"]) + "]"
        kind = spec.get("type", "string")
        if kind in STRINGLIKE:
            return "str"
        if kind == "integer":
            return "int"
        if kind == "number":
            return "float"
        if kind == "boolean":
            return "bool"
        if kind == "json":
            return "Any"
        if kind == "array":
            return f"list[{self.annotation(spec.get('items') or {'type': 'json'}, owner + 'Item', depth + 1)}]"
        if kind == "object":
            return self.nested(owner, spec.get("fields") or [], depth + 1)
        if kind == "ref":
            return self.nested(
                pascal(str(spec.get("schema") or "Schema")),
                self.schemas.get(spec.get("schema")) or [],
                depth + 1,
            )
        return "Any"

    def nested(self, name: str, fields: list[Mapping[str, Any]], depth: int) -> str:
        if depth > MAX_DEPTH or not fields:
            return "dict[str, Any]"
        if name not in self.made:
            self.made.add(name)
            entries = [
                (
                    f["name"],
                    self.annotation(f, name + pascal(f["name"]), depth)
                    + (" | None" if _nullable(f) else ""),
                    bool(f.get("required")),
                )
                for f in fields
                if not f.get("write_only")
            ]
            self.classes.append(_typed_dict(name, entries))
        return name


def _typed_dict(name: str, entries: list[tuple[str, str, bool]]) -> str:
    """A ``TypedDict`` in class syntax, or in functional syntax when a key is not a Python identifier."""
    if not entries:
        return f"class {name}(TypedDict):\n    pass\n"
    if all(k.isidentifier() and not keyword.iskeyword(k) for k, _, _ in entries):
        lines = [f"class {name}(TypedDict):"]
        for key, annotation, required in entries:
            lines.append(f"    {key}: {annotation if required else f'NotRequired[{annotation}]'}")
        return "\n".join(lines) + "\n"
    pairs = ", ".join(f'"{k}": {a if r else f"NotRequired[{a}]"}' for k, a, r in entries)
    return f'{name} = TypedDict("{name}", {{{pairs}}})\n'


def python(state: Any, *, source: str | None = None) -> str:
    resources = resources_of(state)
    builder = _Py(state.schemas)
    body: list[str] = []
    names = []
    for r in resources:
        type_name = pascal(r.name)
        names.append(r.name)
        if r.shaped:
            row_entries: list[tuple[str, str, bool]] = []
            row = f"{type_name}Row = dict[str, Any]\n"
        else:
            row_entries = []
            for c in r.columns:
                if not c.readable:
                    continue
                annotation = builder.annotation(c.spec, type_name + pascal(c.name))
                if not c.guaranteed_on_read and annotation != "Any":
                    annotation += " | None"
                row_entries.append((c.name, annotation, True))
            row = _typed_dict(f"{type_name}Row", row_entries)
        insert, update = [], []
        for c in r.columns:
            if not c.writable:
                continue
            annotation = builder.annotation(c.spec, type_name + pascal(c.name))
            if _nullable(c.spec) and annotation != "Any":
                annotation += " | None"
            insert.append((c.name, annotation, c.required_on_create))
            update.append((c.name, annotation, False))
        body += [
            row,
            _typed_dict(f"{type_name}Insert", insert),
            _typed_dict(f"{type_name}Update", update),
        ]
    literal = ", ".join(f'"{n}"' for n in names) or "str"
    head = [
        f'"""Types for the data API, generated by Pawabase from the environment\'s resources{f" ({source})" if source else ""}.',
        "",
        "Do not edit by hand: regenerate when your resources change. Dates and times are ISO 8601 strings, as the API sends them.",
        '"""',
        "",
        "from typing import Any, Literal, NotRequired, TypedDict",
        "",
        "",
    ]
    tail = [
        f"ResourceName = Literal[{literal}]",
        "",
        "",
        "class Change(TypedDict):",
        '    """A realtime message on ``resource:<name>``. ``source`` is ``database`` for a change made outside the API."""',
        "",
        "    resource: str",
        '    change: Literal["created", "updated", "deleted"]',
        "    record: dict[str, Any]",
        "    changed: NotRequired[list[str]]",
        '    source: NotRequired[Literal["database"]]',
        "",
    ]
    return (
        "\n".join(head)
        + "\n\n".join([*builder.classes, *body]).replace("\n\n\n", "\n\n")
        + "\n\n"
        + "\n".join(tail)
    )
