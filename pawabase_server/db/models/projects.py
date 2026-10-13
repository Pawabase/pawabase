"""Projects, and the database each one is bound to.

A **project** is the technical unit an application lives in: it owns environments and exactly one application database. Projects exist
independently of any user; who may manage one is the business of whatever administers the installation (the local command line, or
Pawabase Cloud's runtime agent). Application Auth is for the people who use the application, not for administering it.
"""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from pawabase_server.core.records import ulid_pk
from pawabase_server.db.fields import AnyJSONField


class Project(Model):
    """One application. ``id`` is a ULID, or the identifier a control plane (Pawabase Cloud) supplied, kept as it is.

    Attributes:
        slug: Unique in the installation; the name the command line uses.
        status: ``pending`` (registered, database not ready), ``ready``, ``failed`` or ``deleting``.
        origin: ``local`` (made here), ``cloud`` (made by a runtime agent) or ``legacy`` (made from environments that predate projects).
        external_ref: The control plane's own id for it, when that is not ``id``.
    """

    id = fields.CharField(max_length=64, primary_key=True)
    name = fields.CharField(max_length=120)
    slug = fields.CharField(max_length=63, unique=True)
    status = fields.CharField(max_length=16, default="pending")
    origin = fields.CharField(max_length=16, default="local")
    external_ref = fields.CharField(max_length=64, null=True)
    is_default = fields.BooleanField(default=False)
    details = AnyJSONField(default=dict)
    #: Versioned gateway entitlement, carried by the API-key cache rather than read per request.
    limits = AnyJSONField(default=dict)
    limits_version = fields.IntField(default=1)

    class Meta:
        table = "pb_projects"
        ordering = ["slug"]


class ProjectDatabaseBinding(Model):
    """The one primary application database of a project.

    ``connection_ref`` never holds a password in the clear: it is ``env:NAME`` (read from the process environment, managed outside
    Pawabase), ``secret://NAME`` is not used here, or ``enc:<sealed>``, the URL sealed with the installation's key. Environments of the
    project are separated by schema (PostgreSQL) or by file (SQLite) inside this database, never by another database.
    """

    id = ulid_pk()
    project = fields.OneToOneField(
        "models.Project", related_name="database", on_delete=fields.CASCADE
    )
    provider = fields.CharField(max_length=16, default="postgres")
    mode = fields.CharField(max_length=16, default="local")
    connection_ref = fields.TextField()
    status = fields.CharField(max_length=16, default="pending")
    last_checked_at = fields.DatetimeField(null=True)
    last_error = fields.CharField(max_length=500, null=True)
    #: Counts credential changes, so a connection pool built from the old one is not reused.
    generation = fields.IntField(default=1)

    class Meta:
        table = "pb_project_databases"


class AgentOperation(Model):
    """One operation a runtime agent was asked to perform, kept so a repeat of it is answered, not done twice.

    ``id`` is the control plane's operation id (its idempotency key). ``result`` is what was reported back.
    """

    id = fields.CharField(max_length=64, primary_key=True)
    kind = fields.CharField(max_length=48)
    project_id = fields.CharField(max_length=64, null=True, db_index=True)
    status = fields.CharField(max_length=16, default="running")
    attempts = fields.IntField(default=1)
    nonce = fields.CharField(max_length=64, null=True)
    result = AnyJSONField(default=dict)
    error = fields.CharField(max_length=500, null=True)

    class Meta:
        table = "pb_agent_operations"
