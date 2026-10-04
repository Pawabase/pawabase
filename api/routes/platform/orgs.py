"""Organizations: the top level of the platform. Members, roles and invitations.

An organization is a team of operators that owns projects. Every project lives
in exactly one, and an operator sees and changes only the projects of the
organizations they belong to (see ``OperatorGate`` in ``routes.common``).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app.platform import Platform
from database.models import ORG_ROLES, Organization, OrgInvitation, OrgMember, Project
from routes.common import (
    OPERATOR,
    ROLE_RANK,
    audit,
    get_org,
    membership,
    operator_of,
    require_role,
)

SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,62}$"
INVITATION_DAYS = 7


class OrgCreate(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN, description="Stable, URL-safe, e.g. acme")
    name: str = Field(min_length=1, max_length=200)


class OrgUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RoleBody(BaseModel):
    role: str


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    role: str = "developer"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _email(value: str) -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="that is not an email address")
    return email


def _check_role(role: str) -> None:
    if role not in ORG_ROLES:
        raise HTTPException(status_code=422, detail=f"role is one of {', '.join(ORG_ROLES)}")


def org_view(org: Organization, role: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "slug": org.slug,
        "name": org.name,
        "role": role,
        "created_at": org.created_at.isoformat() if org.created_at else None,
        **extra,
    }


def member_view(member: OrgMember) -> dict[str, Any]:
    return {
        "user_id": member.user_id,
        "email": member.email,
        "name": member.name,
        "role": member.role,
        "joined_at": member.created_at.isoformat() if member.created_at else None,
    }


def invitation_view(invitation: OrgInvitation) -> dict[str, Any]:
    return {
        "id": invitation.id,
        "email": invitation.email,
        "role": invitation.role,
        "invited_by": invitation.invited_by,
        "expires_at": invitation.expires_at.isoformat(),
        "created_at": invitation.created_at.isoformat() if invitation.created_at else None,
    }


async def _owners(org: Organization) -> int:
    return await OrgMember.filter(organization=org, role="owner").count()


def _operator(ctx: HttpContext) -> tuple[str, str | None]:
    operator = operator_of(ctx)
    if operator is None:
        raise HTTPException(status_code=403, detail="sign in as an operator to do this")
    return operator


async def _valid_invitation(token: str) -> OrgInvitation:
    invitation = (
        await OrgInvitation.filter(token_hash=_hash(token), accepted_at=None, revoked_at=None)
        .prefetch_related("organization")
        .first()
    )
    if invitation is None or invitation.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=404, detail="this invitation is no longer valid")
    return invitation


def register(r: Router, platform: Platform) -> None:
    @r.get("/orgs", auth=OPERATOR, tags=["organizations"], summary="Organizations I belong to")
    async def my_orgs(ctx: HttpContext):
        operator = operator_of(ctx)
        if operator is None:
            orgs = await Organization.all()
            roles = {org.id: "owner" for org in orgs}
        else:
            rows = await OrgMember.filter(user_id=operator[0]).prefetch_related("organization")
            orgs = sorted((m.organization for m in rows), key=lambda o: o.slug)
            roles = {m.organization_id: m.role for m in rows}
        return {
            "data": [
                org_view(
                    org,
                    roles[org.id],
                    projects=await Project.filter(organization=org).count(),
                    members=await OrgMember.filter(organization=org).count(),
                )
                for org in orgs
            ]
        }

    @r.post(
        "/orgs",
        auth=OPERATOR,
        tags=["organizations"],
        request_model=OrgCreate,
        summary="Create an organization (the creator becomes its owner)",
    )
    async def create_org(ctx: HttpContext, body: OrgCreate):
        if await Organization.filter(slug=body.slug).exists():
            raise HTTPException(status_code=409, detail="that slug is taken")
        first = not await Organization.all().exists()
        operator = operator_of(ctx)
        org = await Organization.create(
            slug=body.slug, name=body.name, created_by=operator[1] if operator else None
        )
        if operator is not None:
            await OrgMember.create(
                organization=org,
                user_id=operator[0],
                email=(operator[1] or "").lower(),
                name=str((ctx.scope["user"].claims or {}).get("name") or ""),
                role="owner",
            )
        adopted = 0
        if first:
            # Projects made before organizations existed belong to the first one.
            adopted = await Project.filter(organization_id__isnull=True).update(
                organization_id=org.id
            )
        await audit(
            ctx,
            "org.created",
            org=org.slug,
            target=org.slug,
            details={"adopted_projects": adopted} if adopted else None,
        )
        return created(org_view(org, "owner", projects=adopted, members=1 if operator else 0))

    @r.get("/orgs/{slug}", auth=OPERATOR, tags=["organizations"], summary="An organization")
    async def get_one(ctx: HttpContext, slug: str):
        org = await get_org(ctx, slug)
        member = await membership(ctx, org)
        return org_view(
            org,
            member.role if member else "owner",
            projects=await Project.filter(organization=org).count(),
            members=await OrgMember.filter(organization=org).count(),
        )

    @r.patch(
        "/orgs/{slug}",
        auth=OPERATOR,
        tags=["organizations"],
        request_model=OrgUpdate,
        summary="Rename an organization",
    )
    async def update_org(ctx: HttpContext, slug: str, body: OrgUpdate):
        org = await get_org(ctx, slug, "admin")
        org.name = body.name
        await org.save(update_fields=["name"])
        await audit(ctx, "org.updated", org=slug, target=slug)
        return org_view(org)

    @r.delete(
        "/orgs/{slug}",
        auth=OPERATOR,
        tags=["organizations"],
        summary="Delete an empty organization (owners only)",
    )
    async def delete_org(ctx: HttpContext, slug: str):
        org = await get_org(ctx, slug, "owner")
        if await Project.filter(organization=org).exists():
            raise HTTPException(
                status_code=409, detail="delete or move the organization's projects first"
            )
        await audit(ctx, "org.deleted", org=slug, target=slug)
        await org.delete()
        return no_content()

    # ── members ──────────────────────────────────────────────────────────

    @r.get("/orgs/{slug}/members", auth=OPERATOR, tags=["organizations"], summary="Members")
    async def members(ctx: HttpContext, slug: str):
        org = await get_org(ctx, slug)
        rows = await OrgMember.filter(organization=org)
        return {"data": [member_view(m) for m in rows]}

    @r.put(
        "/orgs/{slug}/members/{user_id}",
        auth=OPERATOR,
        tags=["organizations"],
        request_model=RoleBody,
        summary="Change a member's role",
    )
    async def set_role(ctx: HttpContext, slug: str, user_id: str, body: RoleBody):
        org = await get_org(ctx, slug, "admin")
        me = await require_role(ctx, org, "admin")
        _check_role(body.role)
        target = await OrgMember.get_or_none(organization=org, user_id=user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="not a member")
        if (body.role == "owner" or target.role == "owner") and me != "owner":
            raise HTTPException(status_code=403, detail="only owners can grant or remove ownership")
        if target.role == "owner" and body.role != "owner" and await _owners(org) <= 1:
            raise HTTPException(status_code=409, detail="an organization keeps at least one owner")
        target.role = body.role
        await target.save(update_fields=["role"])
        await audit(
            ctx, "org.member_role", org=slug, target=target.email, details={"role": body.role}
        )
        return member_view(target)

    @r.delete(
        "/orgs/{slug}/members/{user_id}",
        auth=OPERATOR,
        tags=["organizations"],
        summary="Remove a member, or leave",
    )
    async def remove_member(ctx: HttpContext, slug: str, user_id: str):
        org = await get_org(ctx, slug)
        operator = operator_of(ctx)
        leaving = operator is not None and operator[0] == user_id
        me = await require_role(ctx, org, "viewer" if leaving else "admin")
        target = await OrgMember.get_or_none(organization=org, user_id=user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="not a member")
        if target.role == "owner":
            if me != "owner":
                raise HTTPException(status_code=403, detail="only owners can remove an owner")
            if await _owners(org) <= 1:
                raise HTTPException(
                    status_code=409, detail="an organization keeps at least one owner"
                )
        await target.delete()
        await audit(
            ctx,
            "org.member_left" if leaving else "org.member_removed",
            org=slug,
            target=target.email,
        )
        return no_content()

    # ── invitations ──────────────────────────────────────────────────────

    @r.post(
        "/orgs/{slug}/invitations",
        auth=OPERATOR,
        tags=["organizations"],
        request_model=InviteBody,
        summary="Invite someone by email (the token is shown once)",
    )
    async def invite(ctx: HttpContext, slug: str, body: InviteBody):
        org = await get_org(ctx, slug, "admin")
        me = await require_role(ctx, org, "admin")
        _check_role(body.role)
        if body.role == "owner" and me != "owner":
            raise HTTPException(status_code=403, detail="only owners can invite an owner")
        email = _email(body.email)
        if await OrgMember.filter(organization=org, email=email).exists():
            raise HTTPException(status_code=409, detail="already a member")
        # A newer invitation replaces any open one for the same address.
        await OrgInvitation.filter(
            organization=org, email=email, accepted_at=None, revoked_at=None
        ).update(revoked_at=datetime.now(UTC))
        token = secrets.token_urlsafe(32)
        operator = operator_of(ctx)
        invitation = await OrgInvitation.create(
            organization=org,
            email=email,
            role=body.role,
            token_hash=_hash(token),
            invited_by=operator[1] if operator else None,
            expires_at=datetime.now(UTC) + timedelta(days=INVITATION_DAYS),
        )
        await audit(ctx, "org.invited", org=slug, target=email, details={"role": body.role})
        return created({**invitation_view(invitation), "token": token})

    @r.get(
        "/orgs/{slug}/invitations",
        auth=OPERATOR,
        tags=["organizations"],
        summary="Pending invitations",
    )
    async def invitations(ctx: HttpContext, slug: str):
        org = await get_org(ctx, slug, "admin")
        rows = await OrgInvitation.filter(
            organization=org, accepted_at=None, revoked_at=None, expires_at__gt=datetime.now(UTC)
        )
        return {"data": [invitation_view(i) for i in rows]}

    @r.delete(
        "/orgs/{slug}/invitations/{invitation_id}",
        auth=OPERATOR,
        tags=["organizations"],
        summary="Revoke an invitation",
    )
    async def revoke(ctx: HttpContext, slug: str, invitation_id: str):
        invitation_id = invitation_id.upper()  # ULIDs are case-insensitive
        org = await get_org(ctx, slug, "admin")
        updated = await OrgInvitation.filter(
            id=invitation_id, organization=org, accepted_at=None, revoked_at=None
        ).update(revoked_at=datetime.now(UTC))
        if not updated:
            raise HTTPException(status_code=404, detail="no such invitation")
        await audit(ctx, "org.invitation_revoked", org=slug, target=str(invitation_id))
        return no_content()

    @r.get(
        "/invitations/{token}",
        auth=OPERATOR,
        tags=["organizations"],
        summary="What an invitation offers (for the page the link opens)",
    )
    async def preview(ctx: HttpContext, token: str):
        invitation = await _valid_invitation(token)
        return {
            "organization": invitation.organization.name,
            "slug": invitation.organization.slug,
            "email": invitation.email,
            "role": invitation.role,
            "invited_by": invitation.invited_by,
            "expires_at": invitation.expires_at.isoformat(),
        }

    @r.post(
        "/invitations/{token}/accept",
        auth=OPERATOR,
        tags=["organizations"],
        summary="Accept an invitation as the signed-in operator",
    )
    async def accept(ctx: HttpContext, token: str):
        user_id, email = _operator(ctx)
        invitation = await _valid_invitation(token)
        if (email or "").lower() != invitation.email:
            raise HTTPException(
                status_code=403, detail="this invitation was sent to another email address"
            )
        org = invitation.organization
        member = await OrgMember.get_or_none(organization=org, user_id=user_id)
        if member is None:
            member = await OrgMember.create(
                organization=org,
                user_id=user_id,
                email=invitation.email,
                name=str((ctx.scope["user"].claims or {}).get("name") or ""),
                role=invitation.role,
            )
        elif ROLE_RANK[invitation.role] > ROLE_RANK[member.role]:
            member.role = invitation.role
            await member.save(update_fields=["role"])
        invitation.accepted_at = datetime.now(UTC)
        await invitation.save(update_fields=["accepted_at"])
        await audit(ctx, "org.joined", org=org.slug, target=invitation.email)
        return org_view(org, member.role)
