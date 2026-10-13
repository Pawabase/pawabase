"""Carrying out one instruction, once.

An instruction's id is its idempotency key: it is recorded before anything is done, and a repeat of one that finished is answered with
what it produced, not done again. One that was left unfinished by a crash is finished (every operation underneath is itself safe to
repeat). Nothing here runs a shell, opens a socket on someone's say-so or touches a database other than through the project manager.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from tortoise.exceptions import IntegrityError

from pawabase_server.agent.protocol import ALLOWED
from pawabase_server.core.crypto import SecretBox
from pawabase_server.db.models import AgentOperation, Environment, ProjectDatabaseBinding
from pawabase_server.projects import refs
from pawabase_server.projects.manager import ProjectError, ProjectManager, view
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.agent")


class OperationError(Exception):
    """An instruction that cannot be carried out; ``retryable`` says whether asking again could help."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def derive_password(seed: str, role: str) -> str:
    """The password of a managed database role: derived, so an instruction names the role and never carries the password."""
    return hmac.new(
        seed.encode(), f"pawabase-agent:db-role:{role}".encode(), hashlib.sha256
    ).hexdigest()[:40]


class Executor:
    def __init__(self, settings: Settings, box: SecretBox | None = None) -> None:
        self.settings = settings
        self.manager = ProjectManager(settings, box)

    # ── the database an instruction names ────────────────────────────────

    def database_address(self, spec: dict[str, Any]) -> tuple[str | None, str | None, str]:
        """``(url, url_env, mode)`` for a binding instruction, from references only.

        ``{"mode": "managed", "host": alias, "database": …, "role": …}`` is built from this runtime's own list of database hosts and
        the derived role password. ``{"mode": "external", "env": NAME}`` reads a variable set on this runtime. A plaintext address is
        never accepted from an instruction.
        """
        mode = spec.get("mode", "managed")
        if "url" in spec or "password" in spec:
            raise OperationError("an instruction may not carry a database address or password")
        if mode == "external":
            name = spec.get("env")
            if not isinstance(name, str):
                raise OperationError("an external database is named by a variable")
            return None, refs.env_ref(name), "external"
        hosts = json.loads(os.environ.get("PAWABASE_AGENT_DATABASES", "{}") or "{}")
        template = hosts.get(spec.get("host"))
        if not template:
            raise OperationError(f"this runtime has no database host {spec.get('host')!r}")
        role, database = str(spec.get("role", "")), str(spec.get("database", ""))
        if not (role.replace("_", "").isalnum() and database.replace("_", "").isalnum()):
            raise OperationError("a database or role name is letters, digits and underscores")
        if not self.settings.agent_secret_seed:
            raise OperationError(
                "this runtime has no secret seed to derive database passwords from"
            )
        url = template.format(
            role=role,
            database=database,
            password=derive_password(self.settings.agent_secret_seed, role),
        )
        return url, None, "managed"

    # ── operations ───────────────────────────────────────────────────────

    async def project_create(self, job: dict[str, Any]) -> dict[str, Any]:
        p = job["payload"]
        project, created = await self.manager.create_project(
            name=p["name"],
            slug=p["slug"],
            id=job["project_id"],
            origin="cloud",
            external_ref=job["project_id"],
        )
        return {"project": view(project), "created": created}

    async def project_update(self, job: dict[str, Any]) -> dict[str, Any]:
        return await self.manager.update(job["project_id"], name=job["payload"].get("name"))

    async def project_bind_database(self, job: dict[str, Any]) -> dict[str, Any]:
        url, url_env, mode = self.database_address(job["payload"].get("database") or {})
        return await self.manager.bind_database(
            job["project_id"],
            url=url,
            url_env=url_env,
            mode=mode,
            rebind=bool(job["payload"].get("rebind")),
        )

    async def project_delete(self, job: dict[str, Any]) -> dict[str, Any]:
        return await self.manager.delete(job["project_id"])

    async def environment_create(self, job: dict[str, Any]) -> dict[str, Any]:
        made = await self.manager.create_environment(
            job["project_id"],
            job["payload"]["slug"],
            make_keys=bool(job["payload"].get("keys", True)),
        )
        # Keys are shown once, on creation. They go back to the control plane only when it asked for them.
        if not job["payload"].get("return_keys"):
            made = {**made, "keys": {name: "(created)" for name in made.get("keys", {})}}
        return made

    async def project_status(self, job: dict[str, Any]) -> dict[str, Any]:
        shown = await self.manager.show(job["project_id"])
        tested = await self.manager.test_database(job["project_id"])
        environments = await Environment.filter(project_id=job["project_id"]).count()
        ready = tested["ok"] and shown["status"] == "ready" and environments > 0
        return {
            "ready": ready,
            "database": tested["database"],
            "environments": shown["environments"],
        }

    # ── one instruction, exactly once ────────────────────────────────────

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        """Do what *job* says and return what to report. Raises :class:`OperationError` when it cannot be done."""
        kind = job["kind"]
        if kind not in ALLOWED:
            raise OperationError(f"{kind!r} is not an operation an agent performs")
        record = await AgentOperation.get_or_none(id=job["id"])
        if record is not None and record.status == "done":
            return {**record.result, "replayed": True}
        if record is None:
            try:
                record = await AgentOperation.create(
                    id=job["id"], kind=kind, project_id=job.get("project_id"), status="running"
                )
            except IntegrityError:
                record = await AgentOperation.get(id=job["id"])
                if record.status == "done":
                    return {**record.result, "replayed": True}
        else:
            record.attempts += 1
        handler = getattr(self, kind.replace(".", "_"))
        try:
            result = await handler(job)
        except ProjectError as exc:
            record.status, record.error = "failed", str(exc)[:500]
            await record.save()
            raise OperationError(str(exc), retryable=exc.code in ("unreachable",)) from exc
        except OperationError as exc:
            record.status, record.error = "failed", str(exc)[:500]
            await record.save()
            raise
        record.status, record.result, record.error = "done", result, None
        await record.save()
        return result


async def count_unfinished() -> int:
    return (
        await AgentOperation.filter(status="running").count()
        + await ProjectDatabaseBinding.filter(status="pending").count()
    )
