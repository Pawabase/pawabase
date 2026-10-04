"""Backups: everything that makes up one environment, as a file you keep.

A backup holds:

* ``definitions``: every resource, policy, schema, transformer, flow, route,
  bucket, mail template, subscription, webhook, inbound hook and schedule;
* ``settings``: the environment's auth configuration and settings;
* ``users``: roles, users (with password hashes), their roles and permissions,
  linked identities, MFA factors, organizations and teams, from Akountz;
* ``data``: every row of every resource's table.

It never holds API keys (only their hashes exist), secret values (only
ciphertext exists, and ciphertext is useless without the installation's key),
stored files, sessions, logs or jobs. Infrastructure comes from the
installation's environment file, so it is not part of a backup either.

Unlike a blueprint, which can only create new environments from a shared design,
a backup is for restoring the *same* system: ids are preserved so rows keep
pointing at the users and records they belonged to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.data.sql import SqlError, quote
from app.data.store import MAX_PAGE_SIZE, ResourceStore
from app.releases import apply_snapshot, snapshot_checksum, snapshot_environment
from app.state import bump
from database.models import Environment, Secret
from pawabase_core.records import upsert
from routes.platform.promote import KINDS, SKIP

if TYPE_CHECKING:
    from app.platform import Platform

FORMAT = "pawabase.backup"
VERSION = 1
PARTS = ("definitions", "settings", "users", "data")

#: A backup is one JSON document held in memory. Past this many rows, dump the
#: database with its own tools instead (see the docs).
MAX_ROWS = 250_000


class BackupError(Exception):
    """A backup that can't be made or applied, and why."""


def _checksum(document: dict[str, Any]) -> str:
    body = {key: value for key, value in document.items() if key != "checksum"}
    return snapshot_checksum(body)


def _users_path(env: str) -> str:
    return f"/admin/v1/envs/{env}"


def parse_parts(value: str | list[str] | None) -> list[str]:
    if not value:
        return list(PARTS)
    items = value.split(",") if isinstance(value, str) else value
    wanted = [item.strip() for item in items if item.strip()]
    unknown = [item for item in wanted if item not in PARTS]
    if unknown:
        raise BackupError(f"unknown part {unknown[0]!r}; choose from {', '.join(PARTS)}")
    return [part for part in PARTS if part in wanted]


async def create_backup(
    platform: Platform, environment: Environment, parts: list[str]
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "source": {
            "env": environment.name,
            "definitions_version": environment.version,
        },
        "parts": parts,
    }
    snapshot = await snapshot_environment(environment)
    if "definitions" in parts:
        document["definitions"] = snapshot["definitions"]
    if "settings" in parts:
        document["settings"] = {
            "auth": environment.auth or {},
            "settings": environment.settings or {},
        }
    if "users" in parts:
        document["users"] = await platform.akountz.get(
            f"{_users_path(environment.name)}/backup"
        )
    if "data" in parts:
        document["data"] = await _export_data(platform, environment, snapshot["definitions"])
    document["not_included"] = {
        "secrets": sorted(await Secret.filter(environment=environment).values_list("name", flat=True)),
        "note": (
            "API keys, secret values, stored files, sessions, logs and jobs are not part of a "
            "backup. Recreate secrets and keys after restoring into a new installation."
        ),
    }
    document["checksum"] = _checksum(document)
    return document


async def _export_data(
    platform: Platform, environment: Environment, definitions: dict[str, Any]
) -> dict[str, Any]:
    state = await platform.state(environment.name)
    tables: dict[str, Any] = {}
    total = 0
    for resource in definitions.get("resources", []):
        name = resource["name"]
        store = await state.store(name)
        rows: list[dict[str, Any]] = []
        offset = 0
        try:
            while True:
                page, _ = await store.list(limit=MAX_PAGE_SIZE, offset=offset, count=False)
                rows.extend(page)
                if len(page) < MAX_PAGE_SIZE:
                    break
                offset += MAX_PAGE_SIZE
                if total + len(rows) > MAX_ROWS:
                    raise BackupError(
                        f"more than {MAX_ROWS} rows; back up the database with its own tools"
                    )
        except SqlError:
            continue  # the table has not been created yet: nothing to back up
        total += len(rows)
        tables[name] = rows
    return tables


def verify(document: dict[str, Any]) -> list[str]:
    """Check the envelope. Returns the parts the document contains."""
    if document.get("format") != FORMAT:
        raise BackupError("this is not a Pawabase backup (format is not pawabase.backup)")
    if not isinstance(document.get("version"), int) or document["version"] > VERSION:
        raise BackupError(f"unsupported backup version {document.get('version')!r}")
    stored = document.get("checksum")
    if stored and stored != _checksum(document):
        raise BackupError("the checksum does not match: the file was changed or is damaged")
    return [part for part in PARTS if part in document]


def summarise(document: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if "definitions" in document:
        summary["definitions"] = {kind: len(items) for kind, items in document["definitions"].items()}
    if "settings" in document:
        summary["settings"] = sorted(document["settings"])
    if "users" in document:
        users = document["users"]
        summary["users"] = {
            "roles": len(users.get("roles", [])),
            "users": len(users.get("users", [])),
            "organizations": len(users.get("orgs", [])),
        }
    if "data" in document:
        summary["data"] = {name: len(rows) for name, rows in document["data"].items()}
    return summary


async def restore_backup(
    platform: Platform,
    environment: Environment,
    document: dict[str, Any],
    parts: list[str],
    *,
    replace: bool,
) -> dict[str, Any]:
    report: dict[str, Any] = {"restored": parts, "strategy": "replace" if replace else "merge", "warnings": []}

    if "definitions" in parts:
        report["definitions"] = await _restore_definitions(environment, document["definitions"], replace)
    if "settings" in parts:
        incoming = document["settings"]
        auth = incoming.get("auth", {})
        settings = incoming.get("settings", {})
        environment.auth = auth if replace else {**(environment.auth or {}), **auth}
        environment.settings = settings if replace else {**(environment.settings or {}), **settings}
        await environment.save(update_fields=["auth", "settings"])
        report["settings"] = sorted(incoming)
    if "definitions" in parts or "settings" in parts:
        await bump(environment.id)

    if "users" in parts:
        result = await platform.akountz.post(
            f"{_users_path(environment.name)}/restore",
            json={"identities": document["users"], "replace": replace},
        )
        report["users"] = result
        report["warnings"].extend(result.get("warnings", []))

    if "data" in parts:
        report["data"] = await _restore_data(platform, environment, document["data"], replace, report["warnings"])
    return report


async def _restore_definitions(
    environment: Environment, definitions: dict[str, list[dict[str, Any]]], replace: bool
) -> dict[str, int]:
    if replace:
        await apply_snapshot(environment, {"definitions": definitions})
        return {kind: len(items) for kind, items in definitions.items()}
    counts: dict[str, int] = {}
    for kind, (model, natural) in KINDS.items():
        items = definitions.get(kind, [])
        for source in items:
            values = {
                key: value
                for key, value in source.items()
                if key not in SKIP and key in model._meta.fields_map
            }
            lookup = {name: values.pop(name) for name in natural}
            await upsert(model, values, environment_id=environment.id, **lookup)
        counts[kind] = len(items)
    return counts


async def _restore_data(
    platform: Platform,
    environment: Environment,
    data: dict[str, list[dict[str, Any]]],
    replace: bool,
    warnings: list[str],
) -> dict[str, int]:
    state = await platform.state(environment.name)
    source = await state.source()
    counts: dict[str, int] = {}
    for name, rows in data.items():
        if name not in state.specs:
            warnings.append(f"data for {name!r} was skipped: the environment has no such resource")
            continue
        store: ResourceStore = await state.store(name)
        await store.migrate()
        spec = store.spec
        if replace:
            await source.execute(f"DELETE FROM {quote(source.dialect, spec.table)}")
        known = set(spec.field_map)
        dropped: set[str] = set()
        written = 0
        for row in rows:
            row = dict(row)
            dropped |= set(row) - known
            row = {key: value for key, value in row.items() if key in known}
            key = row.get(spec.primary_key)
            if not replace and key is not None and await store.get(key) is not None:
                await store.update(key, row)
            else:
                await store.create(row)
            written += 1
        if dropped:
            warnings.append(
                f"{name}: columns {', '.join(sorted(dropped))} are not fields of the resource and were skipped"
            )
        if source.dialect == "postgres" and spec.id_type == "integer":
            table, pk = quote("postgres", spec.table), quote("postgres", spec.primary_key)
            await source.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{spec.primary_key}'), "
                f"COALESCE((SELECT MAX({pk}) FROM {table}), 1))"
            )
        counts[name] = written
    await platform.cache_invalidate(state, [f"resource:{name}" for name in counts])
    return counts


__all__ = ["FORMAT", "PARTS", "BackupError", "create_backup", "parse_parts", "restore_backup", "summarise", "verify"]
