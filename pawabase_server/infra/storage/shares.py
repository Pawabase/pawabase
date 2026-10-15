"""Temporary share links for one object.

The token is 256 bits from the system's CSPRNG and is returned once; only its SHA-256 is stored, so a database leak does not leak links.
A link names exactly one environment, bucket and key and a permission (``view``, ``download`` or ``both``); nothing in it widens.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.db.models import ObjectShare

PREFIX = "pbs_"
PERMISSIONS = ("view", "download", "both")
MIN_SECONDS = 60
MAX_SECONDS = 365 * 24 * 3600


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def status(row: ObjectShare, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if row.revoked_at is not None:
        return "revoked"
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    return "expired" if expires <= now else "active"


def describe(row: ObjectShare) -> dict[str, Any]:
    return {
        "id": row.id,
        "bucket": row.bucket,
        "key": row.key,
        "permissions": row.permissions,
        "label": row.label,
        "status": status(row),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "expires_at": row.expires_at.isoformat(),
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "created_by": row.created_by,
        "hits": row.hits,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


async def create(
    env: str,
    bucket: str,
    key: str,
    *,
    expires_in: int,
    permissions: str = "both",
    label: str = "",
    created_by: str = "",
    box: Any = None,
) -> tuple[ObjectShare, str]:
    if permissions not in PERMISSIONS:
        raise ValueError(f"permissions must be one of: {', '.join(PERMISSIONS)}")
    seconds = max(MIN_SECONDS, min(int(expires_in), MAX_SECONDS))
    token = PREFIX + secrets.token_urlsafe(32)
    row = await ObjectShare.create(
        env=env,
        bucket=bucket,
        key=key,
        token_hash=hash_token(token),
        token_ciphertext=box.seal(token, env) if box is not None else "",
        permissions=permissions,
        label=label[:120],
        created_by=created_by[:120],
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds),
    )
    return row, token


def reveal(row: ObjectShare, box: Any) -> str | None:
    """The link's token, for a link that is still active and was sealed when made."""
    if status(row) != "active" or not row.token_ciphertext or box is None:
        return None
    try:
        return box.open(row.token_ciphertext, row.env)
    except Exception:
        return None


async def find(token: str) -> ObjectShare | None:
    if not token.startswith(PREFIX) or len(token) > 128:
        return None
    return await ObjectShare.get_or_none(token_hash=hash_token(token))


async def touch(row: ObjectShare) -> None:
    await ObjectShare.filter(id=row.id).update(hits=row.hits + 1, last_used_at=datetime.now(UTC))


async def revoke_object(env: str, bucket: str, key: str) -> int:
    """Revoke every live link to an object (it was deleted or moved), so a later object at that key is not reachable by an old link."""
    return await ObjectShare.filter(
        env=env, bucket=bucket, key=key, revoked_at__isnull=True
    ).update(revoked_at=datetime.now(UTC))
