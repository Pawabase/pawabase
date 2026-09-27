"""Projects, environments, and the credentials and secrets they own."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField


class Project(Model):
    """One application backend.

    Attributes:
        ref: The stable, URL-safe reference clients and keys carry (``acme``).
        name: Display name.
    """

    id = fields.IntField(primary_key=True)
    ref = fields.CharField(max_length=63, unique=True, db_index=True)
    name = fields.CharField(max_length=200)
    description = fields.TextField(default="")
    created_by = fields.CharField(max_length=255, null=True)

    environments: fields.ReverseRelation[Environment]

    class Meta:
        table = "pb_projects"
        ordering = ["ref"]


class Environment(Model):
    """An isolated stage of a project (``development``, ``production``…).

    Attributes:
        name: Unique within the project.
        infra: The developer's infrastructure for this environment: database,
            Redis, storage, mail. Credentials are ``secret://NAME`` references.
        auth: Akountz configuration: token lifetimes, password rules, enabled
            providers (credentials again by secret reference), redirect URLs.
        settings: Everything else: CORS origins, public docs, rate limits.
        version: Bumped on every definition change, so compiled APIs, engines
            and caches know when to rebuild.
    """

    id = fields.IntField(primary_key=True)
    project = fields.ForeignKeyField("models.Project", related_name="environments", on_delete=fields.CASCADE)
    name = fields.CharField(max_length=63)
    is_default = fields.BooleanField(default=False)
    infra = AnyJSONField(default=dict)
    auth = AnyJSONField(default=dict)
    settings = AnyJSONField(default=dict)
    version = fields.IntField(default=1)

    class Meta:
        table = "pb_environments"
        unique_together = (("project", "name"),)
        ordering = ["name"]


class ProjectKey(Model):
    """An API key for one environment.

    The plaintext is shown once at creation. Only a SHA-256 hash is stored,
    computed with Sillo's :func:`sillo.auth.apikey.hash_api_key`.

    Attributes:
        role: ``publishable`` (browser-safe, policies apply) or ``secret``
            (server-side, bypasses policies).
        prefix: The first characters of the key, so Studio can identify it.
        scopes: Optional restrictions (``resource:read``, ``storage:*``…).
    """

    id = fields.UUIDField(primary_key=True)
    environment = fields.ForeignKeyField("models.Environment", related_name="keys", on_delete=fields.CASCADE)
    name = fields.CharField(max_length=200)
    role = fields.CharField(max_length=20, default="publishable")
    prefix = fields.CharField(max_length=24)
    key_hash = fields.CharField(max_length=128, unique=True, db_index=True)
    scopes = AnyJSONField(default=list)
    expires_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)
    last_used_at = fields.DatetimeField(null=True)
    use_count = fields.IntField(default=0)
    created_by = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "pb_project_keys"


class Secret(Model):
    """An encrypted configuration value. Its plaintext never leaves the API."""

    id = fields.IntField(primary_key=True)
    environment = fields.ForeignKeyField("models.Environment", related_name="secrets", on_delete=fields.CASCADE)
    name = fields.CharField(max_length=128)
    ciphertext = fields.TextField()
    description = fields.TextField(default="")
    updated_by = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "pb_secrets"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class AuditEntry(Model):
    """Who changed what on the management plane."""

    id = fields.IntField(primary_key=True)
    project = fields.CharField(max_length=63, null=True, db_index=True)
    env = fields.CharField(max_length=63, null=True)
    actor = fields.CharField(max_length=255, null=True)
    action = fields.CharField(max_length=100)
    target = fields.CharField(max_length=255, default="")
    details = AnyJSONField(default=dict)

    class Meta:
        table = "pb_audit"
        ordering = ["-id"]
