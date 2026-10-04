"""Organizations: the top level of the platform. Projects live inside one."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField
from pawabase_core.records import ulid_pk

#: Least to most privileged. A role can do everything the ones before it can.
#:
#: ``viewer`` reads a project; ``developer`` changes it; ``admin`` also manages
#: the organization's members, projects' lifecycle and settings; ``owner`` also
#: deletes the organization and grants ownership.
ORG_ROLES = ("viewer", "developer", "admin", "owner")


class Organization(Model):
    """A company or team. Members are platform operators, and projects belong to it."""

    id = ulid_pk()
    slug = fields.CharField(max_length=63, unique=True, db_index=True)
    name = fields.CharField(max_length=200)
    created_by = fields.CharField(max_length=255, null=True)

    class Meta:
        table = "pb_organizations"
        ordering = ["slug"]


class OrgMember(Model):
    """An operator (an Akountz user of ``_platform``) in an organization."""

    id = ulid_pk()
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="members", on_delete=fields.CASCADE
    )
    user_id = fields.CharField(max_length=64, db_index=True)
    email = fields.CharField(max_length=255)
    name = fields.CharField(max_length=200, default="")
    role = fields.CharField(max_length=16, default="developer")

    class Meta:
        table = "pb_org_members"
        unique_together = (("organization", "user_id"),)
        ordering = ["id"]


class OrgInvitation(Model):
    """An invitation to join. Only the token's hash is stored."""

    id = ulid_pk()
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="invitations", on_delete=fields.CASCADE
    )
    email = fields.CharField(max_length=255, db_index=True)
    role = fields.CharField(max_length=16, default="developer")
    token_hash = fields.CharField(max_length=64, unique=True, db_index=True)
    invited_by = fields.CharField(max_length=255, null=True)
    expires_at = fields.DatetimeField()
    accepted_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)
    details = AnyJSONField(default=dict)

    class Meta:
        table = "pb_org_invitations"
        ordering = ["-id"]
