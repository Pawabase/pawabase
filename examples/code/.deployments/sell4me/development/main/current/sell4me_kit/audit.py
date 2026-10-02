"""The audit log: who did what, written once and never changed."""

from __future__ import annotations

from typing import Any

from . import q

REDACTED_FIELDS = frozenset(
    {"password", "secret_key", "secret_key_encrypted", "webhook_secret", "webhook_secret_encrypted",
     "key_hash", "token", "secret", "recovery_token", "verification_token"}
)


def changes_between(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The fields that differ, as ``{field: {from, to}}``: only what changed."""
    before, after = before or {}, after or {}
    diff: dict[str, dict[str, Any]] = {}
    for key in set(before) | set(after):
        if key in REDACTED_FIELDS:
            continue
        old, new = before.get(key), after.get(key)
        if old != new:
            diff[key] = {"from": _safe(old), "to": _safe(new)}
    return diff


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value][:20]
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items() if k not in REDACTED_FIELDS}
    return str(value)[:200]


async def record(
    db: Any,
    *,
    store: Any | None,
    actor: Any | None,
    action: str,
    resource_type: str,
    resource_id: Any = None,
    summary: str,
    changes: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> q.Row:
    """Write one entry. The actor is a user id string or a dict with ``id``/``email``/``full_name``.

    The label is stored as text as well, because the log must still name the person after
    their account is gone.
    """
    actor_id = actor.get("id") if isinstance(actor, dict) else actor
    label = (actor.get("full_name") or actor.get("email")) if isinstance(actor, dict) else actor
    return await q.insert(
        db,
        "audit_logs",
        {
            "store_id": store.pk if store else None,
            "actor_id": str(actor_id) if actor_id else None,
            "actor_label": str(label) if label else "System",
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id is not None else None,
            "summary": summary[:500],
            "changes": changes or {},
            "ip_address": ip_address,
            "user_agent": (user_agent or "")[:500] or None,
        },
    )


async def record_from(c: Any, *, action: str, resource_type: str, resource_id: Any = None, summary: str, changes: dict[str, Any] | None = None) -> None:
    """Write an entry for the request's caller and store. Never raises."""
    try:
        await record(
            await c.db(),
            store=c.store,
            actor={"id": c.user_id, "email": c.email},
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary=summary,
            changes=changes,
            ip_address=c.client_ip,
            user_agent=c.headers.get("user-agent"),
        )
    except Exception:  # noqa: BLE001
        pass
