"""Administering an environment's identities: Studio, secret keys and other services.

Every route takes the environment from its path and requires a service token
(Studio, or the API). This is the whole of "Studio must
provide complete operational management of Akountz".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException
from tortoise.expressions import Q
from tortoise.functions import Count

from app import backup, mfa, rbac
from app.accounts import check_password_policy, create_account, get_user, user_view
from app.environment import load_config
from app.platform import Akountz
from app.sessions import list_sessions, revoke_all, revoke_session
from database.models import (
    AuthUser,
    Identity,
    LoginEvent,
    Membership,
    OneTimeToken,
    Organization,
    Team,
)
from database.models.framework import JWTToken
from database.models.orgs import ORG_ROLES
from pawabase_core.service import SERVICE_ONLY


class AdminUserCreate(BaseModel):
    email: str
    password: str | None = None
    username: str | None = None
    name: str = ""
    user_metadata: dict[str, Any] = Field(default_factory=dict)
    app_metadata: dict[str, Any] = Field(default_factory=dict)
    email_verified: bool = True
    roles: list[str] = Field(default_factory=list)


class AdminUserUpdate(BaseModel):
    name: str | None = None
    email_verified: bool | None = None
    disabled: bool | None = None
    password: str | None = None
    user_metadata: dict[str, Any] | None = None
    app_metadata: dict[str, Any] | None = None
    roles: list[str] | None = None
    unlock: bool | None = None


class RoleBody(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_:-]{0,62}$")
    description: str = ""
    permissions: list[str] = Field(default_factory=list)


class AdminOrgCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=200)
    owner_email: str | None = None
    metadata: dict = Field(default_factory=dict)


class AdminOrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    metadata: dict | None = None


class AdminMemberAdd(BaseModel):
    email: str
    role: str = "member"


class AdminMemberRole(BaseModel):
    role: str


class GrantBody(BaseModel):
    permissions: list[str]


class RestoreBody(BaseModel):
    identities: dict[str, Any]
    replace: bool = False
    dry_run: bool = False


def register(r: Router, akountz: Akountz) -> None:
    base = "/envs/{env}"

    async def user_or_404(env: str, user_id: Any) -> AuthUser:
        user = await get_user(env, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="no such user")
        return user

    # ── users ────────────────────────────────────────────────────────────

    @r.get(f"{base}/users", auth=SERVICE_ONLY, tags=["admin"], summary="Search users")
    async def users(ctx: HttpContext, env: str):
        q = ctx.query_params
        limit = max(1, min(int(q.get("limit", 50)), 200))
        offset = max(0, int(q.get("offset", 0)))
        query = AuthUser.filter(env=env, deleted_at=None)
        if q.get("search"):
            term = q["search"]
            query = query.filter(
                Q(email__icontains=term) | Q(username__icontains=term) | Q(name__icontains=term)
            )
        if q.get("status") == "disabled":
            query = query.filter(disabled_at__not_isnull=True)
        elif q.get("status") == "unverified":
            query = query.filter(email_verified_at=None)
        total = await query.count()
        rows = await query.order_by("-id").offset(offset).limit(limit)
        return {"data": [await user_view(u, admin=True) for u in rows], "total": total}

    @r.delete(
        f"{base}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Permanently purge one environment's identities",
    )
    async def purge_environment(ctx: HttpContext, env: str):
        """Remove all identity data belonging to an environment.

        This endpoint is intentionally service-only: Studio never calls it
        directly; the API's environment-purge worker does after confirmation.
        """
        users = await AuthUser.filter(env=env).values_list("id", flat=True)
        counts = {"users": len(users), "organizations": await Organization.filter(env=env).count()}
        if users:
            await JWTToken.filter(user_id__in=[str(user_id) for user_id in users]).delete()
        await OneTimeToken.filter(env=env).delete()
        await LoginEvent.filter(env=env).delete()
        # Cascades remove memberships, teams, invitations, identities, MFA,
        # recovery codes and sessions linked to these rows.
        await Organization.filter(env=env).delete()
        await AuthUser.filter(env=env).delete()
        return {"purged": counts}

    @r.post(
        f"{base}/users",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminUserCreate,
        summary="Create a user",
    )
    async def create_user(ctx: HttpContext, env: str, body: AdminUserCreate):
        config = await load_config(akountz, env)
        user = await create_account(
            config,
            email=body.email,
            password=body.password,
            username=body.username,
            name=body.name,
            user_metadata=body.user_metadata,
            app_metadata=body.app_metadata,
            verified=body.email_verified,
            max_users=akountz.max_users(config.env),
        )
        for role in body.roles:
            await rbac.assign_role(user, role)
        await akountz.emit(
            env,
            "user.created",
            {"user_id": str(user.id), "email": user.email, "method": "admin"},
            actor="admin",
        )
        return created(await user_view(user, admin=True))

    @r.get(f"{base}/users/{{user_id}}", auth=SERVICE_ONLY, tags=["admin"], summary="One user")
    async def get_one(ctx: HttpContext, env: str, user_id: str):
        user = await user_or_404(env, user_id)
        return {**await user_view(user, admin=True), "sessions": await list_sessions(user)}

    @r.patch(
        f"{base}/users/{{user_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminUserUpdate,
        summary="Update, disable, verify or unlock a user",
    )
    async def update(ctx: HttpContext, env: str, user_id: str, body: AdminUserUpdate):
        user = await user_or_404(env, user_id)
        config = await load_config(akountz, env)
        if body.name is not None:
            user.name = body.name
        if body.email_verified is not None:
            user.email_verified_at = datetime.now(UTC) if body.email_verified else None
        if body.user_metadata is not None:
            user.user_metadata = body.user_metadata
        if body.app_metadata is not None:
            user.app_metadata = body.app_metadata
        if body.password:
            check_password_policy(config, body.password)
            user.set_password(body.password)
        if body.unlock:
            user.failed_logins, user.locked_until = 0, None
        if body.disabled is not None:
            user.disabled_at = datetime.now(UTC) if body.disabled else None
            if body.disabled:
                await revoke_all(user)
        await user.save()
        if body.roles is not None:
            current = set(await rbac.roles_of(user))
            for role in set(body.roles) - current:
                await rbac.assign_role(user, role)
            for role in current - set(body.roles):
                await rbac.revoke_role(user, role)
        if body.disabled is not None:
            await akountz.emit(
                env,
                "user.disabled" if body.disabled else "user.enabled",
                {"user_id": str(user.id)},
                actor="admin",
            )
        return await user_view(user, admin=True)

    @r.delete(
        f"{base}/users/{{user_id}}", auth=SERVICE_ONLY, tags=["admin"], summary="Delete a user"
    )
    async def delete(ctx: HttpContext, env: str, user_id: str):
        user = await user_or_404(env, user_id)
        await revoke_all(user)
        await Identity.filter(user=user).delete()
        await mfa.disable(user)
        # Soft delete keeps history attributable; the address is released for re-use.
        user.deleted_at = datetime.now(UTC)
        user.email = f"deleted+{user.id}@pawabase.invalid"
        user.username = f"deleted-{user.id}"
        user.is_active = False
        await user.save()
        await akountz.emit(env, "user.deleted", {"user_id": str(user.id)}, actor="admin")
        return no_content()

    @r.post(
        f"{base}/users/{{user_id}}/sessions/revoke",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Sign a user out everywhere",
    )
    async def sign_out(ctx: HttpContext, env: str, user_id: str):
        user = await user_or_404(env, user_id)
        return {"revoked": await revoke_all(user)}

    @r.delete(
        f"{base}/users/{{user_id}}/sessions/{{session_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="End one session",
    )
    async def end_session(ctx: HttpContext, env: str, user_id: str, session_id: str):
        user = await user_or_404(env, user_id)
        if not await revoke_session(user, session_id):
            raise HTTPException(status_code=404, detail="no such session")
        return no_content()

    @r.post(
        f"{base}/users/{{user_id}}/mfa/reset",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Remove a user's second factors",
    )
    async def reset_mfa(ctx: HttpContext, env: str, user_id: str):
        user = await user_or_404(env, user_id)
        await mfa.disable(user)
        await akountz.emit(env, "user.mfa_reset", {"user_id": str(user.id)}, actor="admin")
        return {"mfa_enabled": False}

    @r.get(
        f"{base}/users/{{user_id}}/history",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="A user's sign-in history",
    )
    async def user_history(ctx: HttpContext, env: str, user_id: str):
        user = await user_or_404(env, user_id)
        rows = await LoginEvent.filter(env=env, user_id=user.id).order_by("-id").limit(100)
        return {"data": [_event(e) for e in rows]}

    @r.post(
        f"{base}/users/{{user_id}}/permissions",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=GrantBody,
        summary="Grant permissions directly",
    )
    async def grant(ctx: HttpContext, env: str, user_id: str, body: GrantBody):
        user = await user_or_404(env, user_id)
        await rbac.grant(user, *body.permissions)
        return {"permissions": await rbac.permissions_of(user)}

    @r.delete(
        f"{base}/users/{{user_id}}/permissions",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=GrantBody,
        summary="Revoke direct permissions",
    )
    async def revoke_perms(ctx: HttpContext, env: str, user_id: str, body: GrantBody):
        user = await user_or_404(env, user_id)
        await rbac.revoke(user, *body.permissions)
        return {"permissions": await rbac.permissions_of(user)}

    # ── roles and permissions ────────────────────────────────────────────

    @r.get(
        f"{base}/roles", auth=SERVICE_ONLY, tags=["admin"], summary="Roles with their permissions"
    )
    async def roles(ctx: HttpContext, env: str):
        return {
            "data": await rbac.list_roles(env),
            "permissions": await rbac.list_permissions(env),
        }

    @r.put(
        f"{base}/roles",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=RoleBody,
        summary="Create or replace a role",
    )
    async def put_role(ctx: HttpContext, env: str, body: RoleBody):
        await rbac.define_role(
            env, body.name, description=body.description, permissions=body.permissions
        )
        return next(role for role in await rbac.list_roles(env) if role["name"] == body.name)

    @r.delete(f"{base}/roles/{{name}}", auth=SERVICE_ONLY, tags=["admin"], summary="Delete a role")
    async def delete_role(ctx: HttpContext, env: str, name: str):
        if not await rbac.delete_role(env, name):
            raise HTTPException(status_code=404, detail="no such role")
        return no_content()

    # ── backup and restore ───────────────────────────────────────────────

    @r.get(
        f"{base}/backup",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Every role, user (with password hashes), and organization",
    )
    async def export_backup(ctx: HttpContext, env: str):
        return await backup.export_identities(env)

    @r.post(
        f"{base}/restore",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=RestoreBody,
        summary="Apply the identities of a backup",
    )
    async def restore_backup(ctx: HttpContext, env: str, body: RestoreBody):
        document = body.identities
        if document.get("format") != backup.FORMAT:
            raise HTTPException(status_code=422, detail="not an identities backup of this version")
        if body.dry_run:
            return {"dry_run": True, **backup.counts(document), "replace": body.replace}
        try:
            report = await backup.restore_identities(env, document, replace=body.replace)
        except (KeyError, TypeError) as exc:
            raise HTTPException(
                status_code=422, detail=f"the backup is malformed: {type(exc).__name__}: {exc}"
            ) from exc
        return report

    # ── organizations ────────────────────────────────────────────────────

    @r.get(f"{base}/orgs", auth=SERVICE_ONLY, tags=["admin"], summary="All organizations")
    async def orgs(ctx: HttpContext, env: str):
        rows = (
            await Organization.filter(env=env)
            .annotate(member_count=Count("memberships"))
            .order_by("slug")
        )
        return {
            "data": [
                {
                    "id": o.id,
                    "slug": o.slug,
                    "name": o.name,
                    "members": o.member_count,
                    "created_at": o.created_at.isoformat() if o.created_at else None,
                }
                for o in rows
            ]
        }

    @r.get(
        f"{base}/orgs/{{slug}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="One organization with members and teams",
    )
    async def org(ctx: HttpContext, env: str, slug: str):
        found = await Organization.get_or_none(env=env, slug=slug)
        if found is None:
            raise HTTPException(status_code=404, detail="no such organization")
        members = await Membership.filter(organization=found).prefetch_related("user")
        teams = await Team.filter(organization=found)
        return {
            "slug": found.slug,
            "name": found.name,
            "metadata": found.metadata,
            "members": [
                {"user_id": str(m.user.id), "email": m.user.email, "role": m.role} for m in members
            ],
            "teams": [{"slug": t.slug, "name": t.name} for t in teams],
        }

    async def org_or_404(env: str, slug: str) -> Organization:
        found = await Organization.get_or_none(env=env, slug=slug)
        if found is None:
            raise HTTPException(status_code=404, detail="no such organization")
        return found

    def check_org_role(role: str) -> None:
        if role not in ORG_ROLES:
            raise HTTPException(status_code=422, detail=f"role is one of {', '.join(ORG_ROLES)}")

    async def user_by_email(env: str, email: str) -> AuthUser:
        found = await AuthUser.get_or_none(env=env, email=email.strip().lower(), deleted_at=None)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no user with email {email}")
        return found

    async def last_owner(org: Organization, user_id: str) -> bool:
        owners = await Membership.filter(organization=org, role="owner").values_list(
            "user_id", flat=True
        )
        return len(owners) == 1 and str(owners[0]) == str(user_id)

    @r.post(
        f"{base}/orgs",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminOrgCreate,
        summary="Create an organization, optionally with an owner",
    )
    async def create_org(ctx: HttpContext, env: str, body: AdminOrgCreate):
        if await Organization.filter(env=env, slug=body.slug).exists():
            raise HTTPException(status_code=409, detail="that slug is taken")
        owner = await user_by_email(env, body.owner_email) if body.owner_email else None
        org = await Organization.create(
            env=env, slug=body.slug, name=body.name, metadata=body.metadata, created_by=None
        )
        if owner is not None:
            await Membership.create(organization=org, user=owner, role="owner")
        await akountz.emit(
            env,
            "organization.created",
            {"organization": org.slug, "user_id": str(owner.id) if owner else None},
            actor="admin",
        )
        return created({"slug": org.slug, "name": org.name, "id": org.id})

    @r.patch(
        f"{base}/orgs/{{slug}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminOrgUpdate,
        summary="Rename an organization or change its metadata",
    )
    async def update_org(ctx: HttpContext, env: str, slug: str, body: AdminOrgUpdate):
        org = await org_or_404(env, slug)
        if body.name is not None:
            org.name = body.name
        if body.metadata is not None:
            org.metadata = body.metadata
        await org.save()
        return {"slug": org.slug, "name": org.name, "metadata": org.metadata}

    @r.delete(
        f"{base}/orgs/{{slug}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Delete an organization with its memberships, teams and invitations",
    )
    async def delete_org(ctx: HttpContext, env: str, slug: str):
        org = await org_or_404(env, slug)
        await org.delete()
        await akountz.emit(env, "organization.deleted", {"organization": slug}, actor="admin")
        return no_content()

    @r.post(
        f"{base}/orgs/{{slug}}/members",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminMemberAdd,
        summary="Add an existing user to an organization",
    )
    async def add_member(ctx: HttpContext, env: str, slug: str, body: AdminMemberAdd):
        org = await org_or_404(env, slug)
        check_org_role(body.role)
        user = await user_by_email(env, body.email)
        if await Membership.filter(organization=org, user=user).exists():
            raise HTTPException(status_code=409, detail="already a member")
        await Membership.create(organization=org, user=user, role=body.role)
        return created({"user_id": str(user.id), "email": user.email, "role": body.role})

    @r.patch(
        f"{base}/orgs/{{slug}}/members/{{user_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminMemberRole,
        summary="Change a member's role",
    )
    async def set_member_role(
        ctx: HttpContext, env: str, slug: str, user_id: str, body: AdminMemberRole
    ):
        org = await org_or_404(env, slug)
        check_org_role(body.role)
        member = await Membership.get_or_none(organization=org, user_id=user_id)
        if member is None:
            raise HTTPException(status_code=404, detail="not a member")
        if member.role == "owner" and body.role != "owner" and await last_owner(org, user_id):
            raise HTTPException(status_code=422, detail="an organization needs at least one owner")
        member.role = body.role
        await member.save()
        return {"user_id": user_id, "role": member.role}

    @r.delete(
        f"{base}/orgs/{{slug}}/members/{{user_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Remove a member from an organization",
    )
    async def remove_member(ctx: HttpContext, env: str, slug: str, user_id: str):
        org = await org_or_404(env, slug)
        member = await Membership.get_or_none(organization=org, user_id=user_id)
        if member is None:
            raise HTTPException(status_code=404, detail="not a member")
        if member.role == "owner" and await last_owner(org, user_id):
            raise HTTPException(status_code=422, detail="an organization needs at least one owner")
        await member.delete()
        return no_content()

    # ── activity ─────────────────────────────────────────────────────────

    @r.get(f"{base}/events", auth=SERVICE_ONLY, tags=["admin"], summary="Authentication events")
    async def events(ctx: HttpContext, env: str):
        q = ctx.query_params
        query = LoginEvent.filter(env=env)
        if q.get("kind"):
            query = query.filter(kind=q["kind"])
        if q.get("failed") == "true":
            query = query.filter(success=False)
        rows = await query.order_by("-id").limit(min(int(q.get("limit", 100)), 500))
        return {"data": [_event(e) for e in rows]}

    @r.get(f"{base}/stats", auth=SERVICE_ONLY, tags=["admin"], summary="Identity statistics")
    async def stats(ctx: HttpContext, env: str):
        day = datetime.now(UTC) - timedelta(days=1)
        users = AuthUser.filter(env=env, deleted_at=None)
        return {
            "users": await users.count(),
            "verified": await users.filter(email_verified_at__not_isnull=True).count(),
            "mfa": await users.filter(mfa_enabled=True).count(),
            "disabled": await users.filter(disabled_at__not_isnull=True).count(),
            "signups_24h": await users.filter(created_at__gte=day).count(),
            "sign_ins_24h": await LoginEvent.filter(
                env=env, kind="sign_in", success=True, created_at__gte=day
            ).count(),
            "failures_24h": await LoginEvent.filter(
                env=env, success=False, created_at__gte=day
            ).count(),
            "organizations": await Organization.filter(env=env).count(),
            "providers": await Identity.filter(env=env)
            .group_by("provider")
            .annotate(n=Count("id"))
            .values("provider", "n"),
        }

    # ── for other services: the environment comes from the context header ──

    @r.get(
        "/users/{user_id}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="One user of the calling context's environment",
    )
    async def context_user(ctx: HttpContext, user_id: str):
        from pawabase_core.context import require_context

        context = require_context(ctx)
        return await user_view(await user_or_404(context.env, user_id), admin=True)


def _event(e: LoginEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "user_id": str(e.user_id) if e.user_id else None,
        "email": e.email,
        "kind": e.kind,
        "method": e.method,
        "success": e.success,
        "reason": e.reason,
        "ip": e.ip,
        "user_agent": e.user_agent,
        "at": e.created_at.isoformat(),
    }
