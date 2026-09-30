"""Administering an environment's identities: Studio, secret keys and other services.

Every route takes the environment from its path and requires a service token
(Studio acting for an operator, or the API). This is the whole of "Studio must
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

from app import mfa, rbac
from app.accounts import check_password_policy, create_account, get_user, user_view
from app.environment import load_config
from app.platform import Akountz
from app.sessions import list_sessions, revoke_all, revoke_session
from database.models import AuthUser, Identity, LoginEvent, Membership, Organization, Team
from pawabase_kit.service import SERVICE_ONLY


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


class GrantBody(BaseModel):
    permissions: list[str]


def register(r: Router, akountz: Akountz) -> None:
    base = "/projects/{project}/envs/{env}"

    async def user_or_404(project: str, env: str, user_id: Any) -> AuthUser:
        user = await get_user(project, env, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="no such user")
        return user

    # ── users ────────────────────────────────────────────────────────────

    @r.get(f"{base}/users", auth=SERVICE_ONLY, tags=["admin"], summary="Search users")
    async def users(ctx: HttpContext, project: str, env: str):
        q = ctx.query_params
        limit = max(1, min(int(q.get("limit", 50)), 200))
        offset = max(0, int(q.get("offset", 0)))
        query = AuthUser.filter(project=project, env=env, deleted_at=None)
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

    @r.post(
        f"{base}/users",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminUserCreate,
        summary="Create a user",
    )
    async def create_user(ctx: HttpContext, project: str, env: str, body: AdminUserCreate):
        config = await load_config(akountz, project, env)
        user = await create_account(
            config,
            email=body.email,
            password=body.password,
            username=body.username,
            name=body.name,
            user_metadata=body.user_metadata,
            app_metadata=body.app_metadata,
            verified=body.email_verified,
        )
        for role in body.roles:
            await rbac.assign_role(user, role)
        await akountz.emit(
            project,
            env,
            "user.created",
            {"user_id": str(user.id), "email": user.email, "method": "admin"},
            actor="admin",
        )
        return created(await user_view(user, admin=True))

    @r.get(f"{base}/users/{{user_id}}", auth=SERVICE_ONLY, tags=["admin"], summary="One user")
    async def get_one(ctx: HttpContext, project: str, env: str, user_id: str):
        user = await user_or_404(project, env, user_id)
        return {**await user_view(user, admin=True), "sessions": await list_sessions(user)}

    @r.patch(
        f"{base}/users/{{user_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=AdminUserUpdate,
        summary="Update, disable, verify or unlock a user",
    )
    async def update(ctx: HttpContext, project: str, env: str, user_id: str, body: AdminUserUpdate):
        user = await user_or_404(project, env, user_id)
        config = await load_config(akountz, project, env)
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
                project,
                env,
                "user.disabled" if body.disabled else "user.enabled",
                {"user_id": str(user.id)},
                actor="admin",
            )
        return await user_view(user, admin=True)

    @r.delete(
        f"{base}/users/{{user_id}}", auth=SERVICE_ONLY, tags=["admin"], summary="Delete a user"
    )
    async def delete(ctx: HttpContext, project: str, env: str, user_id: str):
        user = await user_or_404(project, env, user_id)
        await revoke_all(user)
        await Identity.filter(user=user).delete()
        await mfa.disable(user)
        # Soft delete keeps history attributable; the address is released for re-use.
        user.deleted_at = datetime.now(UTC)
        user.email = f"deleted+{user.id}@{project}.invalid"
        user.username = f"deleted-{user.id}"
        user.is_active = False
        await user.save()
        await akountz.emit(project, env, "user.deleted", {"user_id": str(user.id)}, actor="admin")
        return no_content()

    @r.post(
        f"{base}/users/{{user_id}}/sessions/revoke",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Sign a user out everywhere",
    )
    async def sign_out(ctx: HttpContext, project: str, env: str, user_id: str):
        user = await user_or_404(project, env, user_id)
        return {"revoked": await revoke_all(user)}

    @r.delete(
        f"{base}/users/{{user_id}}/sessions/{{session_id}}",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="End one session",
    )
    async def end_session(ctx: HttpContext, project: str, env: str, user_id: str, session_id: str):
        user = await user_or_404(project, env, user_id)
        if not await revoke_session(user, session_id):
            raise HTTPException(status_code=404, detail="no such session")
        return no_content()

    @r.post(
        f"{base}/users/{{user_id}}/mfa/reset",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="Remove a user's second factors",
    )
    async def reset_mfa(ctx: HttpContext, project: str, env: str, user_id: str):
        user = await user_or_404(project, env, user_id)
        await mfa.disable(user)
        await akountz.emit(project, env, "user.mfa_reset", {"user_id": str(user.id)}, actor="admin")
        return {"mfa_enabled": False}

    @r.get(
        f"{base}/users/{{user_id}}/history",
        auth=SERVICE_ONLY,
        tags=["admin"],
        summary="A user's sign-in history",
    )
    async def user_history(ctx: HttpContext, project: str, env: str, user_id: str):
        user = await user_or_404(project, env, user_id)
        rows = (
            await LoginEvent.filter(project=project, env=env, user_id=user.id)
            .order_by("-id")
            .limit(100)
        )
        return {"data": [_event(e) for e in rows]}

    @r.post(
        f"{base}/users/{{user_id}}/permissions",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=GrantBody,
        summary="Grant permissions directly",
    )
    async def grant(ctx: HttpContext, project: str, env: str, user_id: str, body: GrantBody):
        user = await user_or_404(project, env, user_id)
        await rbac.grant(user, *body.permissions)
        return {"permissions": await rbac.permissions_of(user)}

    @r.delete(
        f"{base}/users/{{user_id}}/permissions",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=GrantBody,
        summary="Revoke direct permissions",
    )
    async def revoke_perms(ctx: HttpContext, project: str, env: str, user_id: str, body: GrantBody):
        user = await user_or_404(project, env, user_id)
        await rbac.revoke(user, *body.permissions)
        return {"permissions": await rbac.permissions_of(user)}

    # ── roles and permissions ────────────────────────────────────────────

    @r.get(
        f"{base}/roles", auth=SERVICE_ONLY, tags=["admin"], summary="Roles with their permissions"
    )
    async def roles(ctx: HttpContext, project: str, env: str):
        return {
            "data": await rbac.list_roles(project, env),
            "permissions": await rbac.list_permissions(project, env),
        }

    @r.put(
        f"{base}/roles",
        auth=SERVICE_ONLY,
        tags=["admin"],
        request_model=RoleBody,
        summary="Create or replace a role",
    )
    async def put_role(ctx: HttpContext, project: str, env: str, body: RoleBody):
        await rbac.define_role(
            project, env, body.name, description=body.description, permissions=body.permissions
        )
        return next(
            role for role in await rbac.list_roles(project, env) if role["name"] == body.name
        )

    @r.delete(f"{base}/roles/{{name}}", auth=SERVICE_ONLY, tags=["admin"], summary="Delete a role")
    async def delete_role(ctx: HttpContext, project: str, env: str, name: str):
        if not await rbac.delete_role(project, env, name):
            raise HTTPException(status_code=404, detail="no such role")
        return no_content()

    # ── organizations ────────────────────────────────────────────────────

    @r.get(f"{base}/orgs", auth=SERVICE_ONLY, tags=["admin"], summary="All organizations")
    async def orgs(ctx: HttpContext, project: str, env: str):
        rows = (
            await Organization.filter(project=project, env=env)
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
    async def org(ctx: HttpContext, project: str, env: str, slug: str):
        found = await Organization.get_or_none(project=project, env=env, slug=slug)
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

    # ── activity ─────────────────────────────────────────────────────────

    @r.get(f"{base}/events", auth=SERVICE_ONLY, tags=["admin"], summary="Authentication events")
    async def events(ctx: HttpContext, project: str, env: str):
        q = ctx.query_params
        query = LoginEvent.filter(project=project, env=env)
        if q.get("kind"):
            query = query.filter(kind=q["kind"])
        if q.get("failed") == "true":
            query = query.filter(success=False)
        rows = await query.order_by("-id").limit(min(int(q.get("limit", 100)), 500))
        return {"data": [_event(e) for e in rows]}

    @r.get(f"{base}/stats", auth=SERVICE_ONLY, tags=["admin"], summary="Identity statistics")
    async def stats(ctx: HttpContext, project: str, env: str):
        day = datetime.now(UTC) - timedelta(days=1)
        users = AuthUser.filter(project=project, env=env, deleted_at=None)
        return {
            "users": await users.count(),
            "verified": await users.filter(email_verified_at__not_isnull=True).count(),
            "mfa": await users.filter(mfa_enabled=True).count(),
            "disabled": await users.filter(disabled_at__not_isnull=True).count(),
            "signups_24h": await users.filter(created_at__gte=day).count(),
            "sign_ins_24h": await LoginEvent.filter(
                project=project, env=env, kind="sign_in", success=True, created_at__gte=day
            ).count(),
            "failures_24h": await LoginEvent.filter(
                project=project, env=env, success=False, created_at__gte=day
            ).count(),
            "organizations": await Organization.filter(project=project, env=env).count(),
            "providers": await Identity.filter(project=project, env=env)
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
        from pawabase_kit.context import require_context

        context = require_context(ctx)
        return await user_view(await user_or_404(context.project, context.env, user_id), admin=True)


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
