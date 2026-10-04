"""Sillo's permission and JWT models, keyed by ULID.

Same tables, same columns and the same methods as Sillo's own (these are
subclasses), with a ULID primary key instead of an auto-increment integer.
:func:`~pawabase_core.ulid_auth.adopt` then points Sillo's modules at them, so
``Permission.assign``, ``JWTToken.revoke_family`` and the rest use these tables.
Sillo's own classes are not registered with the database.
"""

from __future__ import annotations

from sillo.auth.jwt_auth import models as _jwt
from sillo.permissions import models as _permissions
from tortoise import fields

from pawabase_core.records import ulid_pk
from pawabase_core.ulid_auth import adopt

# Sillo's own classes, kept by name: adopt() rebinds the module attributes to the subclasses below.
_SilloGroup = _permissions.Group


class Permission(_permissions.Permission):
    id = ulid_pk()

    class Meta:
        table = "permissions"


class Group(_SilloGroup):
    id = ulid_pk()

    class Meta:
        table = "perm_groups"

    @classmethod
    async def get_or_create(cls, name: str, description: str | None = None) -> Group:  # ty: ignore[invalid-method-override]
        """Sillo's version calls ``super(cls, cls)``, which on a subclass lands back in Sillo's override."""
        group, _ = await super(_SilloGroup, cls).get_or_create(
            name=name, defaults={"description": description}
        )
        return group


class UserPermission(_permissions.UserPermission):
    id = ulid_pk()

    class Meta:
        table = "user_permissions"


class UserGroup(_permissions.UserGroup):
    id = ulid_pk()

    class Meta:
        table = "perm_user_groups"
        unique_together = (("user_id", "group"),)


class GroupPermission(_permissions.GroupPermission):
    id = ulid_pk()

    class Meta:
        table = "perm_group_permissions"
        unique_together = (("group", "permission"),)


class JWTToken(_jwt.JWTToken):
    id = ulid_pk()
    user_id = fields.CharField(max_length=26, db_index=True)

    class Meta:
        table = "jwt_tokens"


class TokenBlacklist(_jwt.TokenBlacklist):
    id = ulid_pk()

    class Meta:
        table = "token_blacklist"


adopt(
    Permission=Permission,
    Group=Group,
    UserPermission=UserPermission,
    UserGroup=UserGroup,
    GroupPermission=GroupPermission,
    JWTToken=JWTToken,
    TokenBlacklist=TokenBlacklist,
)
