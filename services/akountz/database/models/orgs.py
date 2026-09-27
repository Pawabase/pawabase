"""Organizations, their members, teams and invitations."""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields

from database.fields import AnyJSONField

ORG_ROLES = ("owner", "admin", "member", "viewer")


class Organization(Model):
    id = fields.IntField(primary_key=True)
    project = fields.CharField(max_length=63, db_index=True)
    env = fields.CharField(max_length=63)
    slug = fields.CharField(max_length=63)
    name = fields.CharField(max_length=200)
    metadata = AnyJSONField(default=dict)
    created_by = fields.IntField(null=True)

    class Meta:
        table = "akz_organizations"
        unique_together = (("project", "env", "slug"),)


class Membership(Model):
    id = fields.IntField(primary_key=True)
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
    id = fields.IntField(primary_key=True)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="teams", on_delete=fields.CASCADE
    )
    slug = fields.CharField(max_length=63)
    name = fields.CharField(max_length=200)

    class Meta:
        table = "akz_teams"
        unique_together = (("organization", "slug"),)


class TeamMember(Model):
    id = fields.IntField(primary_key=True)
    team = fields.ForeignKeyField("models.Team", related_name="members", on_delete=fields.CASCADE)
    user = fields.ForeignKeyField(
        "models.AuthUser", related_name="team_memberships", on_delete=fields.CASCADE
    )

    class Meta:
        table = "akz_team_members"
        unique_together = (("team", "user"),)


class Invitation(Model):
    id = fields.IntField(primary_key=True)
    organization = fields.ForeignKeyField(
        "models.Organization", related_name="invitations", on_delete=fields.CASCADE
    )
    email = fields.CharField(max_length=255)
    role = fields.CharField(max_length=32, default="member")
    token_id = fields.CharField(max_length=32)
    invited_by = fields.IntField(null=True)
    accepted_at = fields.DatetimeField(null=True)
    revoked_at = fields.DatetimeField(null=True)
    expires_at = fields.DatetimeField()

    class Meta:
        table = "akz_invitations"
