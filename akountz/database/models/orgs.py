"""Organizations, their members, teams and invitations."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField
from pawabase_core.records import ulid_pk

ORG_ROLES = ("owner", "admin", "member", "viewer")


class Organization(Model):
    """An organization **your application's users** create and belong to (a company account, a workspace).

    Nothing to do with administering Pawabase itself, which has no organizations.
    """

    id = ulid_pk()
    env = fields.CharField(max_length=63)
    slug = fields.CharField(max_length=63)
    name = fields.CharField(max_length=200)
    metadata = AnyJSONField(default=dict)
    created_by = fields.CharField(max_length=26, null=True)

    class Meta:
        table = "akz_organizations"
        unique_together = (("env", "slug"),)


class Membership(Model):
    id = ulid_pk()
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="memberships", on_delete=fields.CASCADE
    )
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="memberships", on_delete=fields.CASCADE
    )
    role = fields.CharField(max_length=32, default="member")

    class Meta:
        table = "akz_memberships"
        unique_together = (("organization", "user"),)


class Team(Model):
    id = ulid_pk()
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="teams", on_delete=fields.CASCADE
    )
    slug = fields.CharField(max_length=63)
    name = fields.CharField(max_length=200)

    class Meta:
        table = "akz_teams"
        unique_together = (("organization", "slug"),)


class TeamMember(Model):
    id = ulid_pk()
    team = fields.ForeignKeyField("models.Team", related_name="members", on_delete=fields.CASCADE)
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="team_memberships", on_delete=fields.CASCADE
    )

    class Meta:
        table = "akz_team_members"
        unique_together = (("team", "user"),)


class Invitation(Model):
    id = ulid_pk()
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="invitations", on_delete=fields.CASCADE
    )
    email = fields.CharField(max_length=255)
    role = fields.CharField(max_length=32, default="member")
    token_id = fields.CharField(max_length=32)
    invited_by = fields.CharField(max_length=26, null=True)
    accepted_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)
    expires_at = fields.DatetimeField()

    class Meta:
        table = "akz_invitations"
