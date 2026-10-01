"""Backing up and restoring one environment's identities.

A backup is the part of an environment that lives in Akountz: roles and their
permissions, users (password hashes included, so people keep their passwords),
the roles and permissions each user holds, linked sign-in identities, MFA
factors and recovery codes, and organizations with members and teams.

It leaves out what is only meaningful while it is live: sessions, tokens,
sign-in history and pending invitations. Everyone signs in again after a
restore.

User ids are kept. Application data refers to users by id (an ``owner_id``
column, an organization member), so a restore that renumbered people would
silently detach their rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sillo.permissions import Permission
from tortoise import connections
from tortoise.transactions import in_transaction

from app import rbac
from database.models import (
    AuthUser,
    Identity,
    Membership,
    MfaFactor,
    Organization,
    RecoveryCode,
    Team,
    TeamMember,
)

FORMAT = 1


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ── export ───────────────────────────────────────────────────────────────────


async def export_identities(project: str, env: str) -> dict[str, Any]:
    roles = [
        {"name": r["name"], "description": r["description"], "permissions": r["permissions"]}
        for r in await rbac.list_roles(project, env)
    ]
    users: list[dict[str, Any]] = []
    for user in await AuthUser.filter(project=project, env=env, deleted_at=None).order_by("id"):
        identities = await Identity.filter(user=user)
        factors = await MfaFactor.filter(user=user)
        codes = await RecoveryCode.filter(user=user)
        direct = rbac._strip(project, env, await Permission.of(user))
        users.append(
            {
                "id": user.id,
                "email": user.email,
                "username": user.username,
                "name": user.name,
                "avatar_url": user.avatar_url,
                "phone": user.phone,
                "password": user.password,
                "is_active": user.is_active,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "email_verified_at": _iso(user.email_verified_at),
                "last_login": _iso(user.last_login),
                "created_at": _iso(user.created_at),
                "user_metadata": user.user_metadata or {},
                "app_metadata": user.app_metadata or {},
                "disabled_at": _iso(user.disabled_at),
                "mfa_enabled": user.mfa_enabled,
                "roles": await rbac.roles_of(user),
                "permissions": direct,
                "identities": [
                    {
                        "provider": i.provider,
                        "subject": i.subject,
                        "email": i.email,
                        "email_verified": i.email_verified,
                    }
                    for i in identities
                ],
                "mfa_factors": [
                    {
                        "kind": f.kind,
                        "secret_ciphertext": f.secret_ciphertext,
                        "confirmed_at": _iso(f.confirmed_at),
                        "friendly_name": f.friendly_name,
                    }
                    for f in factors
                ],
                "recovery_codes": [
                    {"code_hash": c.code_hash, "used_at": _iso(c.used_at)} for c in codes
                ],
            }
        )

    orgs: list[dict[str, Any]] = []
    for org in await Organization.filter(project=project, env=env).order_by("id"):
        memberships = await Membership.filter(organization=org)
        teams = []
        for team in await Team.filter(organization=org):
            members = await TeamMember.filter(team=team)
            teams.append(
                {"slug": team.slug, "name": team.name, "members": [m.user_id for m in members]}
            )
        orgs.append(
            {
                "slug": org.slug,
                "name": org.name,
                "metadata": org.metadata or {},
                "created_by": org.created_by,
                "members": [{"user_id": m.user_id, "role": m.role} for m in memberships],
                "teams": teams,
            }
        )
    return {"format": FORMAT, "roles": roles, "users": users, "orgs": orgs}


# ── restore ──────────────────────────────────────────────────────────────────


async def _wipe(project: str, env: str) -> None:
    await Organization.filter(project=project, env=env).delete()
    await AuthUser.filter(project=project, env=env).delete()
    for role in await rbac.list_roles(project, env):
        await rbac.delete_role(project, env, role["name"])
    await Permission.filter(name__startswith=f"{project}/{env}/").delete()


async def _reset_sequences() -> None:
    """Make auto-increment counters pass the ids a restore inserted explicitly."""
    connection = connections.get("default")
    if "postgres" not in type(connection).__module__ and "asyncpg" not in type(connection).__module__:
        return
    for table in ("akz_users", "akz_organizations"):
        await connection.execute_query(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
        )


async def restore_identities(
    project: str, env: str, document: dict[str, Any], *, replace: bool
) -> dict[str, Any]:
    """Apply the identities of a backup. Returns counts and warnings.

    ``replace`` first removes every user, organization and role in the
    environment, so afterwards it holds exactly what the backup held. Without
    it, users are matched by email and updated, and anything the backup does
    not mention is left alone.
    """
    report: dict[str, Any] = {
        "roles": 0,
        "users": {"created": 0, "updated": 0},
        "organizations": 0,
        "warnings": [],
    }
    async with in_transaction():
        if replace:
            await _wipe(project, env)

        for role in document.get("roles", []):
            await rbac.define_role(
                project,
                env,
                role["name"],
                description=role.get("description", ""),
                permissions=role.get("permissions", []),
            )
            report["roles"] += 1

        id_map: dict[int, int] = {}
        taken = {u.email: u for u in await AuthUser.filter(project=project, env=env, deleted_at=None)}
        for source in document.get("users", []):
            existing = taken.get(source["email"])
            fields = {
                "username": source["username"],
                "name": source.get("name", ""),
                "avatar_url": source.get("avatar_url"),
                "phone": source.get("phone"),
                "password": source.get("password", ""),
                "is_active": source.get("is_active", True),
                "is_staff": source.get("is_staff", False),
                "is_superuser": source.get("is_superuser", False),
                "email_verified_at": _when(source.get("email_verified_at")),
                "last_login": _when(source.get("last_login")),
                "user_metadata": source.get("user_metadata") or {},
                "app_metadata": source.get("app_metadata") or {},
                "disabled_at": _when(source.get("disabled_at")),
                "mfa_enabled": source.get("mfa_enabled", False),
            }
            if existing is not None:
                for key, value in fields.items():
                    setattr(existing, key, value)
                await existing.save()
                user = existing
                report["users"]["updated"] += 1
            else:
                wanted_id = source.get("id")
                id_free = wanted_id is not None and not await AuthUser.filter(id=wanted_id).exists()
                clash = await AuthUser.filter(
                    project=project, env=env, username=fields["username"]
                ).exists()
                if clash:
                    fields["username"] = f"{fields['username']}-restored"
                    report["warnings"].append(
                        f"{source['email']}: username was taken, restored as {fields['username']}"
                    )
                user = AuthUser(
                    project=project,
                    env=env,
                    email=source["email"],
                    **({"id": wanted_id} if id_free else {}),
                    **fields,
                )
                await user.save(force_create=True)
                if not id_free:
                    report["warnings"].append(
                        f"{source['email']}: id {wanted_id} is in use, restored as {user.id}; "
                        "records that refer to the old id will not follow"
                    )
                report["users"]["created"] += 1
            if source.get("id") is not None:
                id_map[int(source["id"])] = user.id

            await _restore_user_links(user, source)

        for source in document.get("orgs", []):
            org = await Organization.filter(project=project, env=env, slug=source["slug"]).first()
            if org is None:
                org = await Organization.create(
                    project=project,
                    env=env,
                    slug=source["slug"],
                    name=source["name"],
                    metadata=source.get("metadata") or {},
                    created_by=id_map.get(source.get("created_by")),
                )
            else:
                org.name = source["name"]
                org.metadata = source.get("metadata") or {}
                await org.save()
            for member in source.get("members", []):
                user_id = id_map.get(member["user_id"])
                if user_id is None:
                    continue
                await Membership.update_or_create(
                    organization=org, user_id=user_id, defaults={"role": member.get("role", "member")}
                )
            for team_source in source.get("teams", []):
                team, _ = await Team.get_or_create(
                    organization=org,
                    slug=team_source["slug"],
                    defaults={"name": team_source["name"]},
                )
                for member_id in team_source.get("members", []):
                    user_id = id_map.get(member_id)
                    if user_id is not None:
                        await TeamMember.get_or_create(team=team, user_id=user_id)
            report["organizations"] += 1
    await _reset_sequences()
    return report


async def _restore_user_links(user: AuthUser, source: dict[str, Any]) -> None:
    current = set(await rbac.roles_of(user))
    for role in source.get("roles", []):
        if role not in current:
            await rbac.assign_role(user, role)
    wanted = list(source.get("permissions", []))
    if wanted:
        await rbac.grant(user, *wanted)

    for item in source.get("identities", []):
        await Identity.update_or_create(
            user=user,
            provider=item["provider"],
            subject=item["subject"],
            defaults={
                "project": user.project,
                "env": user.env,
                "email": item.get("email"),
                "email_verified": item.get("email_verified", False),
            },
        )
    await MfaFactor.filter(user=user).delete()
    for item in source.get("mfa_factors", []):
        await MfaFactor.create(
            user=user,
            kind=item.get("kind", "totp"),
            secret_ciphertext=item["secret_ciphertext"],
            confirmed_at=_when(item.get("confirmed_at")),
            friendly_name=item.get("friendly_name", "Authenticator app"),
        )
    await RecoveryCode.filter(user=user).delete()
    for item in source.get("recovery_codes", []):
        await RecoveryCode.create(
            user=user, code_hash=item["code_hash"], used_at=_when(item.get("used_at"))
        )


def counts(document: dict[str, Any]) -> dict[str, int]:
    return {
        "roles": len(document.get("roles", [])),
        "users": len(document.get("users", [])),
        "organizations": len(document.get("orgs", [])),
    }


__all__ = ["FORMAT", "counts", "export_identities", "restore_identities"]
