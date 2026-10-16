"""Minting an environment's API keys."""

from __future__ import annotations

from datetime import datetime

from sillo.auth.apikey import generate_api_key
from sillo.exceptions import HTTPException

from pawabase_server.db.models import ApiKey, Environment, Project
from pawabase_server.projects import quotas


async def create_key(
    environment: Environment,
    name: str,
    role: str,
    *,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
    allowed_ips: list[str] | None = None,
    allowed_routes: list[str] | None = None,
    created_by: str | None = None,
    max_keys: int = 0,
) -> tuple[str, ApiKey]:
    """Mint a key with Sillo's API-key generator; only the hash is stored."""
    if (
        max_keys
        and await ApiKey.filter(environment=environment, revoked_at=None).count() >= max_keys
    ):
        raise HTTPException(
            status_code=403, detail="the environment API key limit has been reached"
        )
    if environment.project_id:
        project = await Project.get_or_none(id=environment.project_id)
        if project is not None:
            await quotas.check_project_count(project, "max_api_keys")
    full, _raw, digest = generate_api_key(prefix="pb_pk" if role == "publishable" else "pb_sk")
    key = await ApiKey.create(
        environment=environment,
        name=name,
        role=role,
        prefix=full[:14],
        key_hash=digest,
        scopes=scopes or [],
        expires_at=expires_at,
        allowed_ips=allowed_ips or [],
        allowed_routes=allowed_routes or [],
        created_by=created_by,
    )
    return full, key
