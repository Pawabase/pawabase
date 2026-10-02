"""Organizations, members, teams and invitations, for the signed-in user."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app import emails, links
from app.accounts import find_by_email, normalise_email
from app.environment import load_config
from app.platform import Akountz
from database.models import AuthUser, Invitation, Membership, Organization, Team, TeamMember
from database.models.orgs import ORG_ROLES
from pawabase_core.records import upsert
from routes.auth import signed_in_user

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
MANAGERS = ("owner", "admin")


class OrgBody(BaseModel):
    slug: str = Field(pattern=SLUG.pattern)
    name: str = Field(min_length=1, max_length=200)
    metadata: dict = Field(default_factory=dict)


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict | None = None


class InviteBody(BaseModel):
    email: str
    role: str = "member"
    redirect_to: str | None = None


class RoleBody(BaseModel):
    role: str


class TeamBody(BaseModel):
    slug: str = Field(pattern=SLUG.pattern)
    name: str = Field(min_length=1, max_length=200)


class AcceptBody(BaseModel):
    token: str


def org_view(org: Organization, role: str | None = None) -> dict:
    return {
        "id": org.id,
        "slug": org.slug,
        "name": org.name,
        "metadata": org.metadata or {},
        "role": role,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


async def membership(
    user: AuthUser, slug: str, *, manage: bool = False
) -> tuple[Organization, Membership]:
    org = await Organization.get_or_none(project=user.project, env=user.env, slug=slug)
    member = await Membership.get_or_none(organization=org, user=user) if org else None
    if org is None or member is None:
        raise HTTPException(status_code=404, detail="no such organization")
    if manage and member.role not in MANAGERS:
        raise HTTPException(status_code=403, detail="only owners and admins can do that")
    return org, member


def _check_role(role: str) -> None:
    if role not in ORG_ROLES:
        raise HTTPException(status_code=422, detail=f"role is one of {', '.join(ORG_ROLES)}")


def register(r: Router, akountz: Akountz) -> None:
    @r.get("/orgs", tags=["organizations"], summary="Organizations I belong to")
    async def my_orgs(ctx: HttpContext):
        user, _ = await signed_in_user(ctx)
        rows = await Membership.filter(user=user).prefetch_related("organization")
        return {"data": [org_view(m.organization, m.role) for m in rows]}

    @r.post(
        "/orgs", tags=["organizations"], request_model=OrgBody, summary="Create an organization"
    )
    async def create_org(ctx: HttpContext, body: OrgBody):
        user, _ = await signed_in_user(ctx)
        if await Organization.filter(project=user.project, env=user.env, slug=body.slug).exists():
            raise HTTPException(status_code=409, detail="that slug is taken")
        org = await Organization.create(
            project=user.project,
            env=user.env,
            slug=body.slug,
            name=body.name,
            metadata=body.metadata,
            created_by=user.id,
        )
        await Membership.create(organization=org, user=user, role="owner")
        await akountz.emit(
            user.project,
            user.env,
            "organization.created",
            {"organization": org.slug, "user_id": str(user.id)},
            actor=str(user.id),
        )
        return created(org_view(org, "owner"))

    @r.get("/orgs/{slug}", tags=["organizations"], summary="An organization")
    async def get_org(ctx: HttpContext, slug: str):
        user, _ = await signed_in_user(ctx)
        org, member = await membership(user, slug)
        return {
            **org_view(org, member.role),
            "members": await Membership.filter(organization=org).count(),
            "teams": await Team.filter(organization=org).count(),
        }

    @r.patch(
        "/orgs/{slug}",
        tags=["organizations"],
        request_model=OrgUpdate,
        summary="Update an organization",
    )
    async def update_org(ctx: HttpContext, slug: str, body: OrgUpdate):
        user, _ = await signed_in_user(ctx)
        org, member = await membership(user, slug, manage=True)
        if body.name is not None:
            org.name = body.name
        if body.metadata is not None:
            org.metadata = body.metadata
        await org.save()
        return org_view(org, member.role)

    @r.delete(
        "/orgs/{slug}", tags=["organizations"], summary="Delete an organization (owners only)"
    )
    async def delete_org(ctx: HttpContext, slug: str):
        user, _ = await signed_in_user(ctx)
        org, member = await membership(user, slug)
        if member.role != "owner":
            raise HTTPException(status_code=403, detail="only owners can delete an organization")
        await org.delete()
        await akountz.emit(
            user.project,
            user.env,
            "organization.deleted",
            {"organization": slug},
            actor=str(user.id),
        )
        return no_content()

    @r.get("/orgs/{slug}/members", tags=["organizations"], summary="Members")
    async def members(ctx: HttpContext, slug: str):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug)
        rows = await Membership.filter(organization=org).prefetch_related("user")
        return {
            "data": [
                {
                    "user_id": str(m.user.id),
                    "email": m.user.email,
                    "name": m.user.name,
                    "role": m.role,
                }
                for m in rows
            ]
        }

    @r.put(
        "/orgs/{slug}/members/{user_id}",
        tags=["organizations"],
        request_model=RoleBody,
        summary="Change a member's role",
    )
    async def set_role(ctx: HttpContext, slug: str, user_id: int, body: RoleBody):
        user, _ = await signed_in_user(ctx)
        org, me = await membership(user, slug, manage=True)
        _check_role(body.role)
        target = await Membership.get_or_none(organization=org, user_id=user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="not a member")
        if (body.role == "owner" or target.role == "owner") and me.role != "owner":
            raise HTTPException(status_code=403, detail="only owners can grant or remove ownership")
        if (
            target.role == "owner"
            and body.role != "owner"
            and await Membership.filter(organization=org, role="owner").count() <= 1
        ):
            raise HTTPException(status_code=409, detail="an organization keeps at least one owner")
        target.role = body.role
        await target.save(update_fields=["role"])
        return {"user_id": str(user_id), "role": body.role}

    @r.delete(
        "/orgs/{slug}/members/{user_id}",
        tags=["organizations"],
        summary="Remove a member (or leave)",
    )
    async def remove_member(ctx: HttpContext, slug: str, user_id: int):
        user, _ = await signed_in_user(ctx)
        org, me = await membership(user, slug)
        if user_id != user.id and me.role not in MANAGERS:
            raise HTTPException(status_code=403, detail="only owners and admins can remove members")
        target = await Membership.get_or_none(organization=org, user_id=user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="not a member")
        if (
            target.role == "owner"
            and await Membership.filter(organization=org, role="owner").count() <= 1
        ):
            raise HTTPException(status_code=409, detail="an organization keeps at least one owner")
        await target.delete()
        await TeamMember.filter(team__organization=org, user_id=user_id).delete()
        return no_content()

    # ── invitations ──────────────────────────────────────────────────────

    @r.post(
        "/orgs/{slug}/invitations",
        tags=["organizations"],
        request_model=InviteBody,
        summary="Invite someone by email",
    )
    async def invite(ctx: HttpContext, slug: str, body: InviteBody):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug, manage=True)
        _check_role(body.role)
        config = await load_config(akountz, user.project, user.env)
        email = normalise_email(body.email)
        existing = await find_by_email(user.project, user.env, email)
        if existing and await Membership.filter(organization=org, user=existing).exists():
            raise HTTPException(status_code=409, detail="already a member")
        token = await links.issue(
            akountz, config, "invite", email=email, data={"organization": org.id, "role": body.role}
        )
        row_id = akountz.serializer(config.project, config.env, "invite").loads(token)["id"]
        invitation = await Invitation.create(
            organization=org,
            email=email,
            role=body.role,
            token_id=row_id,
            invited_by=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        await emails.send(
            akountz,
            config,
            "invite",
            email,
            link=links.link(config, "invite", token, body.redirect_to),
            organization=org.name,
            role=body.role,
        )
        await akountz.emit(
            user.project,
            user.env,
            "invitation.created",
            {"organization": slug, "email": email, "role": body.role},
            actor=str(user.id),
        )
        return created(
            {
                "id": invitation.id,
                "email": email,
                "role": body.role,
                "expires_at": invitation.expires_at.isoformat(),
            }
        )

    @r.get("/orgs/{slug}/invitations", tags=["organizations"], summary="Pending invitations")
    async def invitations(ctx: HttpContext, slug: str):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug, manage=True)
        rows = await Invitation.filter(
            organization=org, accepted_at=None, revoked_at=None, expires_at__gt=datetime.now(UTC)
        )
        return {
            "data": [
                {
                    "id": i.id,
                    "email": i.email,
                    "role": i.role,
                    "expires_at": i.expires_at.isoformat(),
                }
                for i in rows
            ]
        }

    @r.delete(
        "/orgs/{slug}/invitations/{invitation_id}",
        tags=["organizations"],
        summary="Revoke an invitation",
    )
    async def revoke_invitation(ctx: HttpContext, slug: str, invitation_id: int):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug, manage=True)
        updated = await Invitation.filter(
            id=invitation_id, organization=org, accepted_at=None
        ).update(revoked_at=datetime.now(UTC))
        if not updated:
            raise HTTPException(status_code=404, detail="no such invitation")
        return no_content()

    @r.post(
        "/invitations/accept",
        tags=["organizations"],
        request_model=AcceptBody,
        summary="Accept an invitation",
    )
    async def accept(ctx: HttpContext, body: AcceptBody):
        user, _ = await signed_in_user(ctx)
        config = await load_config(akountz, user.project, user.env)
        row = await links.consume(akountz, config, "invite", body.token, peek=True)
        invitation = (
            await Invitation.filter(token_id=row.id, revoked_at=None, accepted_at=None)
            .prefetch_related("organization")
            .first()
        )
        if invitation is None:
            raise HTTPException(status_code=400, detail="the invitation is no longer valid")
        if invitation.email != user.email:
            raise HTTPException(
                status_code=403, detail="this invitation was sent to another address"
            )
        await links.consume(akountz, config, "invite", body.token)
        await upsert(
            Membership,
            organization=invitation.organization,
            user=user,
            defaults={"role": invitation.role},
        )
        invitation.accepted_at = datetime.now(UTC)
        await invitation.save(update_fields=["accepted_at"])
        await akountz.emit(
            user.project,
            user.env,
            "invitation.accepted",
            {"organization": invitation.organization.slug, "user_id": str(user.id)},
            actor=str(user.id),
        )
        return org_view(invitation.organization, invitation.role)

    # ── teams ────────────────────────────────────────────────────────────

    @r.get("/orgs/{slug}/teams", tags=["organizations"], summary="Teams")
    async def teams(ctx: HttpContext, slug: str):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug)
        rows = await Team.filter(organization=org)
        return {
            "data": [
                {"slug": t.slug, "name": t.name, "members": await TeamMember.filter(team=t).count()}
                for t in rows
            ]
        }

    @r.post(
        "/orgs/{slug}/teams",
        tags=["organizations"],
        request_model=TeamBody,
        summary="Create a team",
    )
    async def create_team(ctx: HttpContext, slug: str, body: TeamBody):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug, manage=True)
        if await Team.filter(organization=org, slug=body.slug).exists():
            raise HTTPException(status_code=409, detail="that team exists")
        team = await Team.create(organization=org, slug=body.slug, name=body.name)
        return created({"slug": team.slug, "name": team.name})

    @r.put(
        "/orgs/{slug}/teams/{team}/members/{user_id}",
        tags=["organizations"],
        summary="Add a member to a team",
    )
    async def add_to_team(ctx: HttpContext, slug: str, team: str, user_id: int):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug, manage=True)
        found = await Team.get_or_none(organization=org, slug=team)
        if found is None or not await Membership.filter(organization=org, user_id=user_id).exists():
            raise HTTPException(status_code=404, detail="no such team or member")
        await TeamMember.get_or_create(team=found, user_id=user_id)
        return {"team": team, "user_id": str(user_id)}

    @r.delete(
        "/orgs/{slug}/teams/{team}/members/{user_id}",
        tags=["organizations"],
        summary="Remove a member from a team",
    )
    async def remove_from_team(ctx: HttpContext, slug: str, team: str, user_id: int):
        user, _ = await signed_in_user(ctx)
        org, _ = await membership(user, slug, manage=True)
        await TeamMember.filter(team__organization=org, team__slug=team, user_id=user_id).delete()
        return no_content()
