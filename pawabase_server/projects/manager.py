"""The project manager: the one implementation of "create a project, give it a database, add an environment".

Called by the command line and by the runtime agent, in-process. It holds no HTTP surface of its own, and it never receives database
administrator rights: it works with the address a project was bound to, which can create schemas in its own database and nothing else.

Every operation here is safe to repeat. A repeat that changes nothing returns what is already there.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from tortoise.exceptions import IntegrityError
from tortoise.expressions import F

from pawabase_server.core import envvars
from pawabase_server.core.crypto import SecretBox
from pawabase_server.core.ids import new_ulid
from pawabase_server.data.source import DataSource, DataSourceError
from pawabase_server.db.models import Environment, Project, ProjectDatabaseBinding
from pawabase_server.environments.keys import create_key
from pawabase_server.environments.state import data_schema_name, project_data_url
from pawabase_server.projects import quotas, refs
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.projects")

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SCHEMA = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
LIMIT_FIELDS = frozenset(
    {
        "daily_request_limit",
        "monthly_request_limit",
        "rate_limit",
        "rate_window",
        "max_active_connections",
        "max_users_per_month",
        "max_active_users",
        "max_api_keys",
        "max_auth_sessions_per_user",
        "max_mfa_factors_per_user",
        "max_auth_providers",
        "max_flow_runs_per_month",
        "max_scheduled_jobs",
        "max_storage_bytes",
        "max_upload_bytes",
        "max_database_bytes",
        "max_backups",
        "max_environments",
        "max_realtime_channels",
        "max_webhooks",
        "max_inbound_hooks",
        "max_queued_jobs",
        "max_buckets",
        "max_user_api_keys_per_user",
        "max_emails_per_month",
        "max_realtime_messages_per_month",
        "monthly_bandwidth_bytes",
    }
)


class ProjectError(Exception):
    """A request the manager refuses, with a stable ``code`` for a caller to branch on."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


#: Limits of features that no longer exist (custom Functions). Projects created earlier still have them stored, and a caller written for
#: them may still send them: they are dropped, not refused.
RETIRED_LIMITS = frozenset(
    {
        "max_function_invocations_per_month",
        "max_function_deployments",
        "max_function_concurrency",
        "max_function_timeout_seconds",
        "max_function_memory_mb",
        "max_function_cpu_seconds",
        "max_concurrent_runs_per_function",
        "max_function_body_mb",
        "max_function_workers",
    }
)


def validate_limits(limits: dict[str, Any]) -> dict[str, int]:
    """Validate the persisted project gateway policy. Zero means unlimited."""
    if not isinstance(limits, dict):
        raise ProjectError("project limits must be an object")
    limits = {name: value for name, value in limits.items() if name not in RETIRED_LIMITS}
    unknown = set(limits) - LIMIT_FIELDS
    if unknown:
        raise ProjectError(f"unknown project limit: {sorted(unknown)[0]}")
    cleaned: dict[str, int] = {}
    for name, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProjectError(f"{name} must be a non-negative whole number")
        if value:
            cleaned[name] = value
    return cleaned


def view(project: Project, binding: ProjectDatabaseBinding | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "status": project.status,
        "origin": project.origin,
        "external_ref": project.external_ref,
        "is_default": project.is_default,
        "limits": validate_limits(project.limits or {}),
        "limits_version": project.limits_version,
        "created_at": project.created_at.isoformat() if project.created_at else None,
    }
    if binding is not None:
        out["database"] = binding_view(binding)
    return out


def binding_view(binding: ProjectDatabaseBinding) -> dict[str, Any]:
    """A binding as it may be shown: what it is and whether it works, never the address's password."""
    return {
        "id": binding.id,
        "provider": binding.provider,
        "mode": binding.mode,
        "reference": refs.describe(binding.connection_ref),
        "status": binding.status,
        "last_checked_at": binding.last_checked_at.isoformat() if binding.last_checked_at else None,
        "last_error": binding.last_error,
        "generation": binding.generation,
    }


def schema_for(project_is_default: bool, name: str, slug: str, provider: str) -> str:
    """The schema an environment gets: the legacy hashed name for environments that predate projects, else its own slug."""
    if project_is_default:
        return data_schema_name(name) if provider == "postgres" else name
    return slug.replace("-", "_")


class ProjectManager:
    def __init__(self, settings: Settings, box: SecretBox | None = None) -> None:
        self.settings = settings
        self.box = box or SecretBox(settings.master_key)

    # ── projects ─────────────────────────────────────────────────────────

    async def find(self, ref: str) -> Project:
        project = await Project.get_or_none(id=ref) or await Project.get_or_none(slug=ref)
        if project is None:
            raise ProjectError(f"no project {ref!r}", "not_found")
        return project

    async def list_projects(self) -> list[dict[str, Any]]:
        out = []
        for project in await Project.all():
            binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
            counted = await Environment.filter(project_id=project.id).count()
            out.append({**view(project, binding), "environments": counted})
        return out

    async def show(self, ref: str) -> dict[str, Any]:
        project = await self.find(ref)
        binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
        environments = await Environment.filter(project_id=project.id).order_by("slug")
        return {
            **view(project, binding),
            "environments": [
                {
                    "id": e.id,
                    "slug": e.slug,
                    "key": e.name,
                    "schema": e.schema_name,
                    "status": e.status or "ready",
                    "is_default": e.is_default,
                }
                for e in environments
            ],
        }

    async def create_project(
        self,
        *,
        name: str,
        slug: str | None = None,
        id: str | None = None,
        origin: str = "local",
        external_ref: str | None = None,
        limits: dict[str, Any] | None = None,
    ) -> tuple[Project, bool]:
        """Register a project. Returns ``(project, created)``; asking again with the same id or slug answers with the one that exists."""
        name = name.strip()
        slug = slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:39]
        if not name or len(name) > 120:
            raise ProjectError("a project needs a name of up to 120 characters")
        if not SLUG.match(slug or ""):
            raise ProjectError(
                "a slug is lowercase letters, digits and hyphens, starting with a letter (2 to 40 characters)"
            )
        if id is not None and not ID.match(id):
            raise ProjectError("a project id is 8 to 64 letters, digits, - or _")
        existing = await Project.get_or_none(id=id) if id else None
        if existing is None:
            existing = await Project.get_or_none(slug=slug)
        if existing is not None:
            if id and existing.id != id:
                raise ProjectError(f"the slug {slug!r} belongs to another project", "slug_taken")
            return existing, False
        try:
            project = await Project.create(
                id=id or new_ulid(),
                name=name,
                slug=slug,
                origin=origin,
                external_ref=external_ref,
                status="pending",
                limits=validate_limits(limits or {}),
            )
        except IntegrityError:  # another process made it a moment ago
            return await self.find(id or slug), False
        logger.info("project %s created (%s)", project.slug, project.id)
        return project, True

    async def update(
        self, ref: str, *, name: str | None = None, limits: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        project = await self.find(ref)
        changed: list[str] = []
        if name is not None:
            if not name.strip() or len(name) > 120:
                raise ProjectError("a project needs a name of up to 120 characters")
            project.name = name.strip()
            changed.append("name")
        if limits is not None:
            project.limits = validate_limits(limits)
            project.limits_version += 1
            changed.extend(["limits", "limits_version"])
        if changed:
            await project.save(update_fields=[*changed, "updated_at"])
            if limits is not None:
                await self._invalidate(project)
        return await self.show(project.id)

    async def patch_limits(
        self, ref: str, changes: dict[str, Any], *, reset: bool = False
    ) -> dict[str, Any]:
        project = await self.find(ref)
        policy = {} if reset else validate_limits(project.limits or {})
        policy.update(changes)
        return await self.update(project.id, limits=policy)

    async def delete(self, ref: str) -> dict[str, Any]:
        """Unregister an empty project. Environments and databases are never removed as a side effect: delete the environments first, and
        drop the database yourself if you mean to."""
        project = await self.find(ref)
        if project.is_default:
            raise ProjectError("the default project cannot be deleted", "protected")
        count = await Environment.filter(project_id=project.id).count()
        if count:
            raise ProjectError(
                f"the project still has {count} environment(s); delete them first", "not_empty"
            )
        await ProjectDatabaseBinding.filter(project_id=project.id).delete()
        await Project.filter(id=project.id).delete()
        return {"deleted": project.slug}

    # ── the database binding ─────────────────────────────────────────────

    def _reference(self, *, url: str | None, url_env: str | None) -> tuple[str, str]:
        if bool(url) == bool(url_env):
            raise ProjectError(
                "give the database as an address or as the name of a variable, not both"
            )
        try:
            if url_env:
                return refs.env_ref(url_env), refs.resolve_ref(self.box, refs.env_ref(url_env))
            return refs.seal_url(self.box, url or ""), url or ""
        except refs.RefError as exc:
            raise ProjectError(str(exc)) from exc

    async def bind_database(
        self,
        ref: str,
        *,
        url: str | None = None,
        url_env: str | None = None,
        mode: str = "external",
        rebind: bool = False,
        test: bool = True,
    ) -> dict[str, Any]:
        """Bind the project's one database. Binding a different database to a project that has one needs ``rebind``, and even then moves
        no data: environments simply read the new database, so use it only for a database that already holds the same data."""
        project = await self.find(ref)
        if mode not in ("managed", "external", "local"):
            raise ProjectError("mode is managed, external or local")
        reference, address = self._reference(url=url, url_env=url_env)
        binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
        if binding is not None:
            try:
                current = refs.resolve_ref(self.box, binding.connection_ref)
            except refs.RefError:
                current = None
            if current == address and (url_env is None or binding.connection_ref == reference):
                return {**view(project, binding), "unchanged": True}
            if not rebind:
                raise ProjectError(
                    "this project already has a database; rebinding is explicit (--rebind) and moves no data",
                    "already_bound",
                )
        provider = refs.provider_of(address)
        problem = await self.probe(address) if test else None
        if problem:
            raise ProjectError(f"the database cannot be used: {problem}", "unreachable")
        if binding is None:
            binding = await ProjectDatabaseBinding.create(
                project=project,
                provider=provider,
                mode=mode,
                connection_ref=reference,
                status="ready" if test else "pending",
            )
        else:
            binding.provider, binding.mode, binding.connection_ref = provider, mode, reference
            binding.generation += 1
            binding.status = "ready" if test else "pending"
            await binding.save()
            await self._invalidate(project)
        project.status = "ready" if test else project.status
        await project.save(update_fields=["status", "updated_at"])
        return view(project, binding)

    async def rotate_credentials(
        self, ref: str, *, url: str | None = None, url_env: str | None = None
    ) -> dict[str, Any]:
        """Point the binding at new credentials for the *same* database. The new address must reach the same place."""
        project = await self.find(ref)
        binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
        if binding is None:
            raise ProjectError("the project has no database to rotate", "not_bound")
        reference, address = self._reference(url=url, url_env=url_env)
        if refs.provider_of(address) != binding.provider:
            raise ProjectError("rotation keeps the same kind of database")
        problem = await self.probe(address)
        if problem:
            raise ProjectError(f"the new credentials do not work: {problem}", "unreachable")
        binding.connection_ref = reference
        binding.generation += 1
        binding.status = "ready"
        binding.last_error = None
        await binding.save()
        await self._invalidate(project)
        return view(project, binding)

    async def test_database(self, ref: str) -> dict[str, Any]:
        project = await self.find(ref)
        binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
        if binding is None:
            raise ProjectError("the project has no database", "not_bound")
        try:
            address = refs.resolve_ref(self.box, binding.connection_ref)
            problem = await self.probe(address)
        except refs.RefError as exc:
            problem = str(exc)
        binding.status = "failed" if problem else "ready"
        binding.last_error = (problem or "")[:500] or None
        binding.last_checked_at = datetime.now(UTC)
        await binding.save()
        return {"ok": problem is None, "error": problem, "database": binding_view(binding)}

    async def probe(self, address: str) -> str | None:
        """Whether the address can be connected to and read. Returns the problem, or ``None``. Never creates anything."""
        source = DataSource(address, alias=f"probe-{new_ulid()[-6:]}")
        try:
            await source.connect()
            await source.fetch("SELECT 1 AS ok")
        except (DataSourceError, Exception) as exc:  # noqa: BLE001 - any failure is the answer
            return f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            await source.close()
        return None

    async def _invalidate(self, project: Project) -> None:
        """Make running servers rebuild the environments of this project, so none keeps a connection from the old binding."""
        await Environment.filter(project_id=project.id).update(version=F("version") + 1)

    # ── environments ─────────────────────────────────────────────────────

    async def create_environment(
        self, ref: str, slug: str, *, make_keys: bool = True
    ) -> dict[str, Any]:
        """Add an environment to the project and make its schema. Safe to repeat: a repeat finishes what a failed attempt left."""
        project = await self.find(ref)
        if not SLUG.match(slug or ""):
            raise ProjectError(
                "an environment name is lowercase letters, digits and hyphens, starting with a letter (2 to 40 characters)"
            )
        binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
        if binding is None:
            raise ProjectError(
                "bind the project's database before adding environments", "not_bound"
            )
        key = slug if project.is_default else f"{project.slug}-{slug}"
        if len(key) > 63:
            raise ProjectError("the project and environment names are too long together")
        environment = await Environment.get_or_none(project_id=project.id, slug=slug)
        created = environment is None
        if environment is None:
            others = await Environment.all().values_list("name", flat=True)
            if key in others:
                raise ProjectError(
                    f"the key {key!r} is already used by another environment in this installation",
                    "key_taken",
                )
            problem = envvars.name_problem(key, others)
            if problem:
                raise ProjectError(problem)
            await quotas.check_project_count(project, "max_environments")
            schema = schema_for(project.is_default, key, slug, binding.provider)
            if not SCHEMA.match(schema):
                raise ProjectError("the schema name this would get is not valid")
            try:
                environment = await Environment.create(
                    name=key,
                    project=project,
                    slug=slug,
                    schema_name=schema,
                    status="provisioning",
                    settings={"public_docs": False},
                )
            except IntegrityError:
                environment = await Environment.get(project_id=project.id, slug=slug)
                created = False
        problem = await self.initialize_schema(project, binding, environment)
        if problem:
            environment.status = "failed"
            await environment.save(update_fields=["status", "updated_at"])
            raise ProjectError(f"the schema could not be prepared: {problem}", "unreachable")
        environment.status = "ready"
        await environment.save(update_fields=["status", "updated_at"])
        keys: dict[str, str] = {}
        if make_keys and created:
            keys["publishable"], _ = await create_key(
                environment, "Default publishable key", "publishable", created_by="project-manager"
            )
            keys["secret"], _ = await create_key(
                environment, "Default secret key", "secret", created_by="project-manager"
            )
        return {
            "id": environment.id,
            "slug": environment.slug,
            "key": environment.name,
            "schema": environment.schema_name,
            "status": environment.status,
            "created": created,
            "keys": keys,
        }

    async def initialize_schema(
        self, project: Project, binding: ProjectDatabaseBinding, environment: Environment
    ) -> str | None:
        """Create the environment's schema in the project's database. Returns the problem, or ``None``."""
        try:
            address = refs.resolve_ref(self.box, binding.connection_ref)
        except refs.RefError as exc:
            return str(exc)
        target = project_data_url(address, environment.schema_name or environment.slug or "")
        source = DataSource(self.settings.tuned(target), alias=f"init-{new_ulid()[-6:]}")
        try:
            await source.connect()  # creating a PostgreSQL schema is part of connecting to it
            await source.fetch("SELECT 1 AS ok")
        except Exception as exc:  # noqa: BLE001
            return f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            await source.close()
        return None


async def attach_to_default_project(environment: Environment) -> Environment:
    """File an environment made through the management API under the default project, in the schema it would always have had.

    Studio and the CLI of earlier versions create environments by name; with projects they belong to one, and until a caller says
    otherwise it is the default project.
    """
    if environment.project_id:
        return environment
    project = await Project.get_or_none(is_default=True)
    if project is None:
        return environment
    binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
    provider = binding.provider if binding else "postgres"
    environment.project_id = project.id
    environment.slug = environment.slug or environment.name
    environment.schema_name = environment.schema_name or schema_for(
        True, environment.name, environment.name, provider
    )
    await environment.save(update_fields=["project_id", "slug", "schema_name", "updated_at"])
    return environment


async def ensure_default_project(
    settings: Settings, box: SecretBox | None = None
) -> dict[str, Any]:
    """Make sure the installation has its default project, bound to a database, with its environments attached.

    Called at start-up, so it is idempotent and safe to run from several processes at once, and it changes nothing destructively:

    * a project, a binding or an environment that exists is never replaced;
    * environments that predate projects are attached to the default project **with the schema they already use**, so their data is
      where it was;
    * if a different default database is configured than the one existing data lives in, nothing is attached and the conflict is
      reported, because attaching would silently leave the data behind.
    """
    manager = ProjectManager(settings, box)
    slug = os.environ.get("DEFAULT_PROJECT") or settings.default_project or "default"
    report: dict[str, Any] = {"project": slug, "attached": [], "created": [], "conflicts": []}
    project = await Project.get_or_none(slug=slug)
    legacy = await Environment.filter(project_id=None).exists()
    if project is None:
        if not SLUG.match(slug):
            raise ProjectError(f"DEFAULT_PROJECT {slug!r} is not a valid project slug")
        try:
            project = await Project.create(
                id=new_ulid(),
                name=slug.replace("-", " ").title(),
                slug=slug,
                status="ready" if legacy else "pending",
                origin="legacy" if legacy else "local",
                is_default=True,
            )
            report["created"].append(f"project {slug}")
        except IntegrityError:
            project = await Project.get(slug=slug)
    elif not project.is_default and not await Project.filter(is_default=True).exists():
        project.is_default = True
        await project.save(update_fields=["is_default", "updated_at"])

    binding = await ProjectDatabaseBinding.get_or_none(project_id=project.id)
    if binding is None:
        configured = os.environ.get("DEFAULT_DATABASE_URL") or settings.default_database_url
        existing_data = settings.default_data_url
        if legacy and configured and configured != existing_data:
            report["conflicts"].append(
                "DEFAULT_DATABASE_URL differs from PAWABASE_DEFAULT_DATA_URL, where this installation's existing environments keep their "
                "data. Nothing was attached. Bind the database you mean explicitly (`project db bind`), after moving or copying the data."
            )
            logger.error(report["conflicts"][-1])
            return report
        address = configured or existing_data
        try:
            reference = (
                refs.env_ref("PAWABASE_DEFAULT_DATA_URL")
                if (not configured and os.environ.get("PAWABASE_DEFAULT_DATA_URL"))
                else refs.seal_url(manager.box, address)
            )
            binding = await ProjectDatabaseBinding.create(
                project=project,
                provider=refs.provider_of(address),
                mode="local",
                connection_ref=reference,
                status="ready",
            )
            report["created"].append("database binding")
        except IntegrityError:
            binding = await ProjectDatabaseBinding.get(project_id=project.id)
        except refs.RefError as exc:
            report["conflicts"].append(f"the default database address is not usable: {exc}")
            logger.error(report["conflicts"][-1])
            return report

    # Attach environments that predate projects, keeping the schema (or file) each one already uses.
    if await Project.exclude(id=project.id).exclude(origin="cloud").exists() and legacy:
        # Other projects exist next to environments nobody has claimed: not ours to guess at.
        orphans = await Environment.filter(project_id=None).values_list("name", flat=True)
        if orphans:
            report["conflicts"].append(
                f"environments {', '.join(orphans)} belong to no project and other projects exist: attach them with the command line"
            )
            logger.error(report["conflicts"][-1])
        return report
    for environment in await Environment.filter(project_id=None):
        environment.project_id = project.id
        environment.slug = environment.slug or environment.name
        environment.schema_name = environment.schema_name or schema_for(
            True, environment.name, environment.name, binding.provider
        )
        await environment.save(update_fields=["project_id", "slug", "schema_name", "updated_at"])
        report["attached"].append(environment.name)
    if not await Environment.filter(project_id=project.id).exists():
        try:
            environment = await Environment.create(
                name="development",
                project=project,
                slug="development",
                schema_name=schema_for(True, "development", "development", binding.provider),
                status="ready",
                is_default=True,
                settings={"public_docs": False},
            )
            report["created"].append(f"environment {environment.name}")
        except IntegrityError:
            pass
    if project.status != "ready":
        project.status = "ready"
        await project.save(update_fields=["status", "updated_at"])
    return report
