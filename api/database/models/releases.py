"""Immutable definition revisions and public API releases."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField


class Branch(Model):
    """A movable authoring pointer within one environment."""

    id = fields.IntField(primary_key=True)
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="branches", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=63)
    head_revision_id = fields.CharField(max_length=32, null=True)
    #: Mutable definition snapshot for an isolated working branch. ``main``
    #: continues to use the environment's live definitions.
    draft = AnyJSONField(default=dict)
    #: Snapshot the branch started from, used for safe three-way merge checks.
    base_snapshot = AnyJSONField(default=dict)
    #: Append-only authoring actions performed against this branch.
    changes = AnyJSONField(default=list)
    protected = fields.BooleanField(default=False)

    class Meta:
        table = "pb_branches"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class DefinitionRevision(Model):
    """An immutable, validated snapshot of an environment's definitions."""

    id = fields.CharField(max_length=32, primary_key=True)
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="revisions", on_delete=fields.CASCADE
    )
    number = fields.IntField()
    branch = fields.CharField(max_length=63, default="main")
    parent_revision_id = fields.CharField(max_length=32, null=True)
    checksum = fields.CharField(max_length=64, db_index=True)
    snapshot = AnyJSONField(default=dict)
    status = fields.CharField(max_length=16, default="valid")
    problems = AnyJSONField(default=list)
    message = fields.TextField(default="")
    created_by = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "pb_definition_revisions"
        unique_together = (("environment", "number"),)
        ordering = ["-number"]


class ApiVersion(Model):
    """A stable public URL namespace such as ``v1`` or ``v2``."""

    id = fields.IntField(primary_key=True)
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="api_versions", on_delete=fields.CASCADE
    )
    name = fields.CharField(max_length=32)
    status = fields.CharField(max_length=16, default="draft")
    is_default = fields.BooleanField(default=False)
    active_release_id = fields.CharField(max_length=32, null=True)
    previous_release_id = fields.CharField(max_length=32, null=True)
    sunset_at = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_api_versions"
        unique_together = (("environment", "name"),)
        ordering = ["name"]


class Release(Model):
    """A revision prepared for, or active on, one public API version."""

    id = fields.CharField(max_length=32, primary_key=True)
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="releases", on_delete=fields.CASCADE
    )
    revision_id = fields.CharField(max_length=32, db_index=True)
    api_version = fields.CharField(max_length=32, db_index=True)
    name = fields.CharField(max_length=128)
    status = fields.CharField(max_length=16, default="ready")
    notes = fields.TextField(default="")
    compatibility = AnyJSONField(default=dict)
    created_by = fields.CharField(max_length=255, null=True)
    deployed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "pb_releases"
        ordering = ["-created_at"]


class Deployment(Model):
    """Append-only history of activations and rollbacks."""

    id = fields.IntField(primary_key=True)
    environment = fields.ForeignKeyField(
        "models.Environment", related_name="deployments", on_delete=fields.CASCADE
    )
    api_version = fields.CharField(max_length=32, db_index=True)
    release_id = fields.CharField(max_length=32)
    previous_release_id = fields.CharField(max_length=32, null=True)
    action = fields.CharField(max_length=16)
    status = fields.CharField(max_length=16, default="succeeded")
    actor = fields.CharField(max_length=255, null=True)
    details = AnyJSONField(default=dict)

    class Meta:
        table = "pb_deployments"
        ordering = ["-id"]
