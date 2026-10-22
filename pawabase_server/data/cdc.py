"""Change capture: seeing every change to a table, including the ones that did not come through the API.

The data API announces the changes it makes itself (``after_write``). Anything else that touches the database (another
service, a migration tool, a person in the SQL editor) is invisible to it. Turning on a resource's ``capture`` closes that gap:

* **Triggers** on the table write one row per insert, update or delete to an **outbox** table (``pb_changes``) in the same
  transaction as the change, so a rolled-back change is never announced and a committed one never missed.
* A **poller** (:class:`ChangeCapture`) claims outbox rows in batches and delivers each one as the same events and realtime
  messages the API produces: ``<resource>.created|updated|deleted`` events (flows, webhooks, subscriptions) and the
  ``resource:<name>`` channel, whose per-subscriber read policy still decides who sees what. Cached reads are invalidated too.

Delivery is at least once: a claim that is not finished within :data:`CLAIM_SECONDS` (a process died mid-batch) is taken again,
up to :data:`MAX_ATTEMPTS` times. Several servers can poll at once; claims are atomic, so each change is delivered once.

Works on PostgreSQL and SQLite. A capture trigger can never fail the write it watches on PostgreSQL (its errors are reported as
warnings); on SQLite it can only fail if the outbox table is dropped by hand.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.core.ids import new_ulid
from pawabase_server.data import inspect as db_inspect
from pawabase_server.data.source import DataSource
from pawabase_server.data.sql import decode_value, quote

logger = logging.getLogger("pawabase.cdc")

OUTBOX = "pb_changes"
FUNCTION = "pb_cdc_record"
SUPPORTED = ("postgres", "sqlite")
#: A claim older than this, still not delivered, is taken again (its process presumably died).
CLAIM_SECONDS = 60
MAX_ATTEMPTS = 5
#: Delivered rows are kept this long, then pruned.
RETENTION = timedelta(hours=1)
CHANGES = {"insert": "created", "update": "updated", "delete": "deleted"}


def supported(dialect: str) -> bool:
    return dialect in SUPPORTED


def trigger_name(table: str, suffix: str = "") -> str:
    """Short and unique per table (Postgres names are limited to 63 characters)."""
    return f"pb_cdc_{hashlib.sha256(table.encode()).hexdigest()[:16]}{suffix}"


def _now(dialect: str) -> Any:
    now = datetime.now(UTC)
    return now.isoformat() if dialect == "sqlite" else now


def _ph(dialect: str, n: int) -> str:
    return f"${n}" if dialect == "postgres" else "?"


# ── the outbox and the triggers ──────────────────────────────────────────────────────────────────


async def _schema(source: DataSource) -> str | None:
    if source.dialect != "postgres":
        return None
    rows = await source.fetch("SELECT current_schema() AS schema")
    return rows[0]["schema"] if rows else None


def _outbox_ref(dialect: str, schema: str | None) -> str:
    return (
        f"{quote(dialect, schema)}.{quote(dialect, OUTBOX)}" if schema else quote(dialect, OUTBOX)
    )


async def ensure_outbox(source: DataSource) -> None:
    """The outbox table, and on PostgreSQL the function the triggers call. Safe to run again."""
    if source.dialect == "postgres":
        schema = await _schema(source)
        outbox = _outbox_ref("postgres", schema)
        await source.execute(
            f"CREATE TABLE IF NOT EXISTS {outbox} ("
            "id BIGSERIAL PRIMARY KEY, tbl TEXT NOT NULL, op TEXT NOT NULL, pk TEXT, new_row JSONB, old_row JSONB, "
            "at TIMESTAMPTZ NOT NULL DEFAULT now(), claimed_by TEXT, claimed_at TIMESTAMPTZ, done_at TIMESTAMPTZ, "
            "attempts INTEGER NOT NULL DEFAULT 0)"
        )
        await source.execute(
            f'CREATE INDEX IF NOT EXISTS "{OUTBOX}_pending" ON {outbox} (id) WHERE done_at IS NULL'
        )
        # The outbox is named in full inside the function: a writer whose search_path is not this schema's must still land here.
        function = f"{quote('postgres', schema)}.{quote('postgres', FUNCTION)}"
        await source.execute(
            f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $pb$ "
            "DECLARE key text := TG_ARGV[0]; "
            "BEGIN "
            "BEGIN "
            "IF TG_OP = 'DELETE' THEN "
            f"INSERT INTO {outbox} (tbl, op, pk, old_row) VALUES (TG_TABLE_NAME, 'delete', to_jsonb(OLD) ->> key, to_jsonb(OLD)); "
            "ELSIF TG_OP = 'UPDATE' THEN "
            f"INSERT INTO {outbox} (tbl, op, pk, new_row, old_row) VALUES (TG_TABLE_NAME, 'update', to_jsonb(NEW) ->> key, to_jsonb(NEW), to_jsonb(OLD)); "
            "ELSE "
            f"INSERT INTO {outbox} (tbl, op, pk, new_row) VALUES (TG_TABLE_NAME, 'insert', to_jsonb(NEW) ->> key, to_jsonb(NEW)); "
            "END IF; "
            "EXCEPTION WHEN OTHERS THEN RAISE WARNING 'pawabase change capture: %', SQLERRM; "
            "END; "
            "IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; "
            "RETURN NEW; "
            "END $pb$"
        )
        return
    await source.execute(
        f"CREATE TABLE IF NOT EXISTS {quote('sqlite', OUTBOX)} ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, op TEXT NOT NULL, pk TEXT, new_row TEXT, old_row TEXT, "
        "at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), claimed_by TEXT, claimed_at TEXT, done_at TEXT, "
        "attempts INTEGER NOT NULL DEFAULT 0)"
    )
    await source.execute(
        f'CREATE INDEX IF NOT EXISTS "{OUTBOX}_pending" ON "{OUTBOX}" (id) WHERE done_at IS NULL'
    )


def _sqlite_object(prefix: str, columns: Sequence[str]) -> str:
    pairs = ", ".join(f"'{c}', {prefix}.{quote('sqlite', c)}" for c in columns)
    return f"json_object({pairs})"


async def install(source: DataSource, table: str, primary_key: str) -> None:
    """Triggers on *table* that write its changes to the outbox. Replaces earlier ones, so it also picks up new columns."""
    dialect = source.dialect
    if not supported(dialect):
        raise ValueError(f"change capture is not available on {dialect}")
    await ensure_outbox(source)
    await uninstall(source, table)
    quoted = quote(dialect, table)
    if dialect == "postgres":
        schema = await _schema(source)
        function = f"{quote(dialect, schema)}.{quote(dialect, FUNCTION)}"
        key = primary_key.replace("'", "''")
        await source.execute(
            f"CREATE TRIGGER {quote(dialect, trigger_name(table))} AFTER INSERT OR UPDATE OR DELETE ON {quoted} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}('{key}')"
        )
        return
    columns = [
        c["name"]
        for c in await db_inspect.list_columns(source, table)
        if "blob" not in str(c["type"]).lower()
    ]
    pk = f"CAST(NEW.{quote(dialect, primary_key)} AS TEXT)"
    old_pk = f"CAST(OLD.{quote(dialect, primary_key)} AS TEXT)"
    outbox = quote(dialect, OUTBOX)
    table_literal = table.replace("'", "''")
    await source.execute(
        f"CREATE TRIGGER {quote(dialect, trigger_name(table, '_i'))} AFTER INSERT ON {quoted} BEGIN "
        f"INSERT INTO {outbox} (tbl, op, pk, new_row) VALUES ('{table_literal}', 'insert', {pk}, {_sqlite_object('NEW', columns)}); END"
    )
    await source.execute(
        f"CREATE TRIGGER {quote(dialect, trigger_name(table, '_u'))} AFTER UPDATE ON {quoted} BEGIN "
        f"INSERT INTO {outbox} (tbl, op, pk, new_row, old_row) VALUES ('{table_literal}', 'update', {pk}, "
        f"{_sqlite_object('NEW', columns)}, {_sqlite_object('OLD', columns)}); END"
    )
    await source.execute(
        f"CREATE TRIGGER {quote(dialect, trigger_name(table, '_d'))} AFTER DELETE ON {quoted} BEGIN "
        f"INSERT INTO {outbox} (tbl, op, pk, old_row) VALUES ('{table_literal}', 'delete', {old_pk}, {_sqlite_object('OLD', columns)}); END"
    )


async def uninstall(source: DataSource, table: str) -> None:
    """Remove *table*'s capture triggers (none is fine)."""
    dialect = source.dialect
    if dialect == "postgres":
        await source.execute(
            f"DROP TRIGGER IF EXISTS {quote(dialect, trigger_name(table))} ON {quote(dialect, table)}"
        )
    elif dialect == "sqlite":
        for suffix in ("_i", "_u", "_d"):
            await source.execute(
                f"DROP TRIGGER IF EXISTS {quote(dialect, trigger_name(table, suffix))}"
            )


async def is_installed(source: DataSource, table: str) -> bool:
    if source.dialect == "postgres":
        rows = await source.fetch(
            "SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE t.tgname = $1 AND c.relname = $2 AND n.nspname = current_schema() AND NOT t.tgisinternal",
            [trigger_name(table), table],
        )
        return bool(rows)
    if source.dialect == "sqlite":
        rows = await source.fetch(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            [trigger_name(table, "_i")],
        )
        return bool(rows)
    return False


# ── reading the outbox ───────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Change:
    id: int
    table: str
    op: str
    pk: str | None
    new: dict[str, Any] | None
    old: dict[str, Any] | None
    at: Any
    attempts: int

    @property
    def change(self) -> str:
        return CHANGES[self.op]


def _document(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        with contextlib.suppress(ValueError):
            value = json.loads(value)
    return value if isinstance(value, dict) else None


async def claim(source: DataSource, limit: int = 200, *, token: str | None = None) -> list[Change]:
    """Take up to *limit* undelivered changes, oldest first. Atomic: no two callers are given the same row."""
    dialect = source.dialect
    token = token or new_ulid()
    now = _now(dialect)
    moment = datetime.now(UTC) - timedelta(seconds=CLAIM_SECONDS)
    stale = moment.isoformat() if dialect == "sqlite" else moment
    outbox = quote(dialect, OUTBOX)
    candidates = await source.fetch(
        f"SELECT id FROM {outbox} WHERE done_at IS NULL AND attempts < {MAX_ATTEMPTS} "
        f"AND (claimed_by IS NULL OR claimed_at < {_ph(dialect, 1)}) ORDER BY id LIMIT {int(limit)}",
        [stale],
    )
    ids = [int(row["id"]) for row in candidates]
    if not ids:
        return []
    listed = ", ".join(str(i) for i in ids)
    await source.execute(
        f"UPDATE {outbox} SET claimed_by = {_ph(dialect, 1)}, claimed_at = {_ph(dialect, 2)}, attempts = attempts + 1 "
        f"WHERE id IN ({listed}) AND done_at IS NULL AND (claimed_by IS NULL OR claimed_at < {_ph(dialect, 3)})",
        [token, now, stale],
    )
    rows = await source.fetch(
        f"SELECT id, tbl, op, pk, new_row, old_row, at, attempts FROM {outbox} WHERE claimed_by = {_ph(dialect, 1)} "
        "AND done_at IS NULL ORDER BY id",
        [token],
    )
    return [
        Change(
            id=int(r["id"]),
            table=r["tbl"],
            op=r["op"],
            pk=r["pk"],
            new=_document(r["new_row"]),
            old=_document(r["old_row"]),
            at=r["at"],
            attempts=int(r["attempts"]),
        )
        for r in rows
    ]


async def finish(source: DataSource, ids: Sequence[int]) -> None:
    """Mark changes delivered."""
    if not ids:
        return
    dialect = source.dialect
    listed = ", ".join(str(int(i)) for i in ids)
    await source.execute(
        f"UPDATE {quote(dialect, OUTBOX)} SET done_at = {_ph(dialect, 1)} WHERE id IN ({listed})",
        [_now(dialect)],
    )


async def prune(source: DataSource) -> int:
    """Forget changes delivered more than :data:`RETENTION` ago, and ones given up on."""
    dialect = source.dialect
    cutoff = datetime.now(UTC) - RETENTION
    value = cutoff.isoformat() if dialect == "sqlite" else cutoff
    return await source.execute(
        f"DELETE FROM {quote(dialect, OUTBOX)} WHERE (done_at IS NOT NULL AND done_at < {_ph(dialect, 1)}) "
        f"OR (done_at IS NULL AND attempts >= {MAX_ATTEMPTS} AND at < {_ph(dialect, 2)})",
        [value, value],
    )


async def pending(source: DataSource) -> int:
    rows = await source.fetch(
        f"SELECT COUNT(*) AS total FROM {quote(source.dialect, OUTBOX)} WHERE done_at IS NULL AND attempts < {MAX_ATTEMPTS}"
    )
    return int(rows[0]["total"]) if rows else 0


def changed_columns(change: Change) -> list[str]:
    """The columns an update changed, in the old row's order."""
    if change.op != "update" or change.new is None or change.old is None:
        return []
    return [name for name in change.new if change.new.get(name) != change.old.get(name)]


def record_of(change: Change, field_map: dict[str, Any]) -> dict[str, Any]:
    """The row as the API reports it: booleans as booleans, JSON columns as documents."""
    row = change.new if change.new is not None else (change.old or {})
    return {name: decode_value(field_map.get(name), value) for name, value in row.items()}


# ── applying a resource's setting, and delivering changes ────────────────────────────────────────


async def reconcile(
    platform: Any, env: str, *, removed: Sequence[str] = (), force: bool = True
) -> dict[str, Any]:
    """Make the database's triggers match which resources have ``capture`` on. Returns what it did.

    *removed* names tables of resources just deleted: their triggers are dropped even though the state no longer lists them.
    *force* lays triggers down again even where they exist (a definition or table changed); without it only missing ones
    are added, which is what the poller's periodic self-heal wants.
    """
    state = await platform.state(env)
    source = await state.source()
    if not supported(source.dialect):
        return {"supported": False, "dialect": source.dialect, "installed": [], "removed": []}
    installed, dropped = [], []
    tables = set(await db_inspect.list_tables(source))
    for name, resource in state.resources.items():
        spec = state.specs.get(name)
        if spec is None or spec.table not in tables:
            continue  # no table yet: capture is laid down when it is migrated
        try:
            if resource.capture:
                if force or not await is_installed(source, spec.table):
                    await install(source, spec.table, spec.primary_key)
                    installed.append(name)
            elif await is_installed(source, spec.table):
                await uninstall(source, spec.table)
                dropped.append(name)
        except Exception:  # noqa: BLE001 - one table that cannot be captured must not stop the others
            logger.exception("change capture: could not apply to %s.%s", env, name)
    for table in removed:
        with contextlib.suppress(Exception):
            await uninstall(source, table)
            dropped.append(table)
    return {
        "supported": True,
        "dialect": source.dialect,
        "installed": installed,
        "removed": dropped,
    }


async def apply(platform: Any, env: str, *, removed: Sequence[str] = ()) -> None:
    """:func:`reconcile` after a definition changed. Never raises: a table that cannot be captured yet (not migrated, say)
    is picked up by the poller's next self-heal, and the definition itself is already saved."""
    try:
        await reconcile(platform, env, removed=removed)
    except Exception:  # noqa: BLE001
        logger.exception("change capture: could not apply to %s", env)
    capture = getattr(platform, "capture", None)
    if capture is not None:
        capture.forget()


async def deliver(platform: Any, state: Any, change: Change) -> bool:
    """Announce one change like the API would have. False when no resource owns the table (nothing to do)."""
    from pawabase_server.data.resources import announce

    name = next((n for n, spec in state.specs.items() if spec.table == change.table), None)
    resource = state.resources.get(name) if name else None
    if resource is None or not resource.capture:
        return False
    spec = state.specs[name]
    record = record_of(change, spec.field_map)
    await announce(
        platform,
        state,
        name,
        change.change,
        record,
        extra={"changed": changed_columns(change), "source": "database"},
    )
    return True


class ChangeCapture:
    """Delivers captured changes. One per server process; any number may run against the same environments."""

    def __init__(self, platform: Any, *, interval: float = 0.5, batch: int = 200) -> None:
        self.platform = platform
        self.interval = interval
        self.batch = batch
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._envs: tuple[float, list[str]] = (0.0, [])
        self._last_prune = 0.0
        self._last_heal = 0.0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="change-capture")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    def forget(self) -> None:
        """The set of captured environments may have changed: look it up again on the next pass."""
        self._envs = (0.0, self._envs[1])
        self.kick()

    def kick(self) -> None:
        """Look now instead of at the next beat: called after the API's own writes so their announcement is immediate."""
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop must outlive any one failure
                logger.exception("change capture: a pass failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval)
            self._wake.clear()

    async def environments(self) -> list[str]:
        """Environments that have at least one captured resource (looked up every few seconds, not every beat)."""
        from pawabase_server.db.models import Resource

        loop = asyncio.get_running_loop()
        stamp, names = self._envs
        if loop.time() - stamp > 5:
            rows = await Resource.filter(capture=True).prefetch_related("environment")
            names = sorted({row.environment.name for row in rows})
            self._envs = (loop.time(), names)
        return names

    async def tick(self) -> int:
        """One pass over every captured environment. Returns how many changes were delivered."""
        total = 0
        for env in await self.environments():
            try:
                total += await self.drain(env)
            except Exception:  # noqa: BLE001
                logger.exception("change capture: could not read %s", env)
        loop = asyncio.get_running_loop()
        if loop.time() - self._last_heal > 30:
            self._last_heal = loop.time()
            for env in await self.environments():
                with contextlib.suppress(Exception):
                    await reconcile(self.platform, env, force=False)
        if loop.time() - self._last_prune > 300:
            self._last_prune = loop.time()
            for env in await self.environments():
                with contextlib.suppress(Exception):
                    await prune(await (await self.platform.state(env)).source())
        return total

    async def drain(self, env: str) -> int:
        state = await self.platform.state(env)
        source = await state.source()
        if not supported(source.dialect):
            return 0
        delivered = 0
        while True:
            try:
                changes = await claim(source, self.batch)
            except Exception:  # noqa: BLE001 - the outbox does not exist yet: nothing has been captured
                return delivered
            if not changes:
                return delivered
            done = []
            for change in changes:
                try:
                    await deliver(self.platform, state, change)
                    done.append(change.id)
                except Exception:  # noqa: BLE001 - left claimed: taken again after CLAIM_SECONDS, up to MAX_ATTEMPTS
                    logger.exception(
                        "change capture: could not deliver %s #%s", change.table, change.id
                    )
            await finish(source, done)
            delivered += len(done)
            if len(changes) < self.batch:
                return delivered
