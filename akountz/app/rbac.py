"""Roles and permissions, on Sillo's permission models.

Sillo's ``Permission`` and ``Group`` names are unique across the whole
database, and Akountz serves several environments. Names are therefore stored
with their environment as a prefix (``production/editor``) and the prefix
is removed on the way out. Every operation below is Sillo's: define, assign,
revoke, and resolve a user's direct and group permissions.
"""

from __future__ import annotations

from database.models import AuthUser, Group, Permission, UserGroup


def scoped(env: str, name: str) -> str:
    return f"{env}/{name}"


def _strip(env: str, names: list[str]) -> list[str]:
    prefix = f"{env}/"
    return sorted({name[len(prefix) :] for name in names if name.startswith(prefix)})


async def define_role(
    env: str,
    name: str,
    *,
    description: str = "",
    permissions: list[str] | None = None,
) -> Group:
    group = await Group.get_or_create(scoped(env, name), description)
    if permissions is not None:
        current = set(await group.get_permissions())
        wanted = {scoped(env, p) for p in permissions}
        for perm in wanted - current:
            await Permission.define(perm)
        if wanted - current:
            await group.add_permissions(*(wanted - current))
        if current - wanted:
            await group.remove_permissions(*(current - wanted))
    if description and group.description != description:
        group.description = description
        await group.save(update_fields=["description"])
    return group


async def delete_role(env: str, name: str) -> bool:
    group = await Group.filter(name=scoped(env, name)).first()
    if group is None:
        return False
    await UserGroup.filter(group=group).delete()
    await group.delete()
    return True


async def list_roles(env: str) -> list[dict]:
    prefix = f"{env}/"
    roles = []
    for group in await Group.filter(name__startswith=prefix):
        roles.append(
            {
                "name": group.name[len(prefix) :],
                "description": group.description or "",
                "permissions": _strip(env, await group.get_permissions()),
                "members": await group.get_member_count(),
            }
        )
    return sorted(roles, key=lambda role: role["name"])


async def assign_role(user: AuthUser, name: str) -> None:
    group = await Group.filter(name=scoped(user.env, name)).first()
    if group is None:
        group = await define_role(user.env, name)
    await group.add_user(user)


async def revoke_role(user: AuthUser, name: str) -> None:
    group = await Group.filter(name=scoped(user.env, name)).first()
    if group is not None:
        await group.remove_user(user)


async def grant(user: AuthUser, *permissions: str) -> None:
    for perm in permissions:
        await Permission.define(scoped(user.env, perm))
    await Permission.assign(user, *(scoped(user.env, p) for p in permissions))


async def revoke(user: AuthUser, *permissions: str) -> None:
    await Permission.revoke(user, *(scoped(user.env, p) for p in permissions))


async def roles_of(user: AuthUser) -> list[str]:
    return _strip(user.env, await Group.names_of_user(user))


async def permissions_of(user: AuthUser) -> list[str]:
    direct = await Permission.of(user)
    via_groups: list[str] = []
    for group in await Group.of_user(user):
        via_groups.extend(await group.get_permissions())
    return _strip(user.env, [*direct, *via_groups])


async def list_permissions(env: str) -> list[str]:
    prefix = f"{env}/"
    return sorted(p.name[len(prefix) :] for p in await Permission.filter(name__startswith=prefix))
