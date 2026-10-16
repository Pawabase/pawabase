"""The management audit log: one query for listing and exporting, and its retention.

Every service records management actions through ``routes.common.audit`` with the same fields (actor, action, target, environment,
details). Actions are dotted, service first (``webhook.redelivered``, ``database.write``), so ``action=webhook.*`` selects a service.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.db.models import AuditEntry

EXPORT_LIMIT = 100_000


def query(params: Mapping[str, str]) -> Any:
    """Entries matching ``env``, ``action`` (exact, or ``prefix.*``), ``actor``, ``target``, ``since`` and ``until`` (ISO times)."""
    rows = AuditEntry.all()
    if params.get("env"):
        rows = rows.filter(env=params["env"])
    action = params.get("action") or ""
    if action.endswith("*"):
        rows = rows.filter(action__startswith=action[:-1])
    elif action:
        rows = rows.filter(action=action)
    if params.get("actor"):
        rows = rows.filter(actor=params["actor"])
    if params.get("target"):
        rows = rows.filter(target=params["target"])
    for name, lookup in (("since", "created_at__gte"), ("until", "created_at__lt")):
        if params.get(name):
            rows = rows.filter(
                **{lookup: datetime.fromisoformat(params[name].replace("Z", "+00:00"))}
            )
    return rows


def _row(entry: AuditEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "at": entry.created_at.isoformat() if entry.created_at else None,
        "env": entry.env,
        "actor": entry.actor,
        "action": entry.action,
        "target": entry.target,
        "details": entry.details,
    }


async def export(params: Mapping[str, str], fmt: str) -> tuple[str, str]:
    """The matching entries, oldest first, as ``jsonl`` or ``csv``: ``(body, content type)``."""
    entries = [_row(e) for e in await query(params).order_by("id").limit(EXPORT_LIMIT)]
    if fmt == "csv":
        out = io.StringIO()
        writer = csv.DictWriter(
            out, fieldnames=["id", "at", "env", "actor", "action", "target", "details"]
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow({**entry, "details": json.dumps(entry["details"], default=str)})
        return out.getvalue(), "text/csv; charset=utf-8"
    return "".join(json.dumps(e, default=str) + "\n" for e in entries), "application/x-ndjson"


async def purge(retention_days: int) -> int:
    """Remove entries older than the retention. Returns how many went."""
    if retention_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    return await AuditEntry.filter(created_at__lt=cutoff).delete()
