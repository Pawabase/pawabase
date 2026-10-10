"""Sending and re-sending organization invitations, for the application and for Studio."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sillo.exceptions import HTTPException

from app import emails, links
from app.accounts import find_by_email, normalise_email
from app.environment import AuthConfig
from app.platform import Akountz
from database.models import Invitation, Membership, OneTimeToken, Organization
from database.models.orgs import ORG_ROLES

LIFETIME = timedelta(days=7)


def check_role(role: str) -> None:
    if role not in ORG_ROLES:
        raise HTTPException(status_code=422, detail=f"role is one of {', '.join(ORG_ROLES)}")


async def _send(
    akountz: Akountz,
    config: AuthConfig,
    org: Organization,
    invitation: Invitation,
    token: str,
    redirect_to: str | None,
) -> None:
    await emails.send(
        akountz,
        config,
        "invite",
        invitation.email,
        link=links.link(config, "invite", token, redirect_to),
        organization=org.name,
        role=invitation.role,
    )


async def invite(
    akountz: Akountz,
    config: AuthConfig,
    org: Organization,
    *,
    email: str,
    role: str,
    invited_by: str | None,
    redirect_to: str | None,
    actor: str,
) -> Invitation:
    """Create an invitation and email its link. Refuses someone who is already a member."""
    check_role(role)
    email = normalise_email(email)
    existing = await find_by_email(config.env, email)
    if existing and await Membership.filter(organization=org, user=existing).exists():
        raise HTTPException(status_code=409, detail="already a member")
    token = await links.issue(
        akountz, config, "invite", email=email, data={"organization": org.id, "role": role}
    )
    row_id = akountz.serializer(config.env, "invite").loads(token)["id"]
    invitation = await Invitation.create(
        organization=org,
        email=email,
        role=role,
        token_id=row_id,
        invited_by=invited_by,
        expires_at=datetime.now(UTC) + LIFETIME,
    )
    await _send(akountz, config, org, invitation, token, redirect_to)
    await akountz.emit(
        config.env,
        "invitation.created",
        {"organization": org.slug, "email": email, "role": role},
        actor=actor,
    )
    return invitation


async def resend(
    akountz: Akountz,
    config: AuthConfig,
    invitation: Invitation,
    *,
    redirect_to: str | None,
    actor: str,
) -> Invitation:
    """Replace a pending invitation's link with a fresh one and email it again; the old link stops working."""
    org = invitation.organization
    await OneTimeToken.filter(id=invitation.token_id).delete()
    token = await links.issue(
        akountz,
        config,
        "invite",
        email=invitation.email,
        data={"organization": org.id, "role": invitation.role},
    )
    invitation.token_id = akountz.serializer(config.env, "invite").loads(token)["id"]
    invitation.expires_at = datetime.now(UTC) + LIFETIME
    await invitation.save(update_fields=["token_id", "expires_at"])
    await _send(akountz, config, org, invitation, token, redirect_to)
    await akountz.emit(
        config.env,
        "invitation.resent",
        {"organization": org.slug, "email": invitation.email, "role": invitation.role},
        actor=actor,
    )
    return invitation
