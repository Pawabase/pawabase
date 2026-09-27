"""The user object Sillo puts on ``ctx.user`` in every Pawabase service.

Sillo resolves ``ctx.user`` by calling ``user_model.load_user(identity)`` with the
identity string a backend returned. Pawabase backends verify a token and return
its claims, JSON-encoded, as that identity. :meth:`Principal.load_user` turns them
back into an object. The claims are already verified, so nothing is fetched.
"""

from __future__ import annotations

import json
from typing import Any

from sillo.users.protocol import UserProtocol

PRINCIPAL_PREFIX = "pawabase:"


def encode_identity(kind: str, claims: dict[str, Any]) -> str:
    """Pack verified claims into the identity string a backend returns."""
    return PRINCIPAL_PREFIX + json.dumps({"kind": kind, "claims": claims}, default=str)


class Principal(UserProtocol):
    """An authenticated caller: a project user, an operator, or a service.

    Attributes:
        kind: ``user`` (a project's end user), ``operator`` (a Studio user) or
            ``service`` (another Pawabase service).
        claims: The verified token claims.
    """

    def __init__(self, kind: str, claims: dict[str, Any]) -> None:
        self.kind = kind
        self.claims = claims

    # -- UserProtocol ------------------------------------------------------

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def identity(self) -> str:
        return str(self.claims.get("sub") or self.claims.get("svc") or "")

    @property
    def display_name(self) -> str:
        return str(self.claims.get("email") or self.identity)

    @property
    def user_id(self) -> str:
        """The user id, for policies (``$auth.user_id``)."""
        return self.identity

    @property
    def email(self) -> str | None:
        return self.claims.get("email")

    @property
    def roles(self) -> list[str]:
        return list(self.claims.get("roles") or [])

    @property
    def permissions(self) -> list[str]:
        return list(self.claims.get("perms") or [])

    @property
    def org(self) -> str | None:
        return self.claims.get("org")

    def has_permission(self, permission: str) -> bool:
        if self.kind in ("service", "operator") and "admin" in self.roles:
            return True
        perms = self.permissions
        return permission in perms or "*" in perms

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def as_policy_context(self) -> dict[str, Any]:
        """What policies see under ``auth``."""
        return {
            "authenticated": True,
            "kind": self.kind,
            "user_id": self.identity,
            "email": self.email,
            "roles": self.roles,
            "permissions": self.permissions,
            "org": self.org,
            "org_role": self.claims.get("org_role"),
            "aal": self.claims.get("aal"),
            "session_id": self.claims.get("sid"),
            "claims": self.claims,
        }

    @classmethod
    async def load_user(cls, identity: str) -> Principal | None:
        if not identity.startswith(PRINCIPAL_PREFIX):
            return None
        data = json.loads(identity[len(PRINCIPAL_PREFIX) :])
        return cls(data["kind"], data["claims"])

    def __repr__(self) -> str:
        return f"<Principal {self.kind}:{self.identity}>"


ANONYMOUS_POLICY_CONTEXT: dict[str, Any] = {
    "authenticated": False,
    "kind": "anonymous",
    "user_id": None,
    "email": None,
    "roles": [],
    "permissions": [],
    "org": None,
    "org_role": None,
    "aal": None,
    "session_id": None,
    "claims": {},
}


def policy_auth(user: Any) -> dict[str, Any]:
    """The ``auth`` policy context for whatever is on ``ctx.user``."""
    if isinstance(user, Principal):
        return user.as_policy_context()
    return dict(ANONYMOUS_POLICY_CONTEXT)
