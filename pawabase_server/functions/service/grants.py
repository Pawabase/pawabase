"""Invocation grants: what the runtime may do on the platform on behalf of one invocation.

When the server decides an invocation may run (key, policy, quotas), it signs a **grant**: the environment, the branch, the caller's
``auth`` as policies see it, the request id, the call depth and the invocation's id, valid a little longer than the invocation may run.
The runtime never decides who a caller is. It passes the grant back with every ``ctx.runtime`` call and run report, and the server acts
exactly as the grant says, so a compromised runtime can do no more than the invocations it was handed were allowed to do, and only
while they last.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from sillo.helpers import jwt as sillo_jwt

from pawabase_server.core.tokens import TokenInvalid

TYPE = "fn-grant"
AUDIENCE = "pawabase:function-grant"
#: Added to the invocation's own limit, so a grant outlives the slowest legitimate runtime call it makes.
SLACK_SECONDS = 30


@dataclass(frozen=True)
class Grant:
    env: str
    branch: str
    function: str
    invocation: str
    auth: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    depth: int = 0
    kind: str = "call"
    deployment_id: str | None = None
    expires_at: float = 0.0

    def claims(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "branch": self.branch,
            "fn": self.function,
            "inv": self.invocation,
            "auth": self.auth,
            "rid": self.request_id,
            "depth": self.depth,
            "kind": self.kind,
            "dep": self.deployment_id,
        }

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> Grant:
        return cls(
            env=str(claims["env"]),
            branch=str(claims.get("branch") or "main"),
            function=str(claims.get("fn") or ""),
            invocation=str(claims["inv"]),
            auth=dict(claims.get("auth") or {}),
            request_id=claims.get("rid"),
            depth=int(claims.get("depth") or 0),
            kind=str(claims.get("kind") or "call"),
            deployment_id=claims.get("dep"),
            expires_at=float(claims.get("exp") or 0),
        )


def new_invocation_id() -> str:
    return secrets.token_hex(12)


def issue(secret: str, grant: Grant, *, seconds: float) -> str:
    """Sign *grant* for an invocation that may run *seconds*."""
    now = int(time.time())
    payload = {
        **grant.claims(),
        "typ": TYPE,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + int(seconds) + SLACK_SECONDS,
        "jti": secrets.token_hex(6),
    }
    return sillo_jwt.encode(payload, secret)


def verify(secret: str, token: str) -> Grant:
    """The grant *token* carries. Raises :class:`TokenInvalid` when it is not one, was tampered with or has expired."""
    try:
        claims = sillo_jwt.decode(token, secret, audience=AUDIENCE, leeway=5)
    except sillo_jwt.TokenError as exc:
        raise TokenInvalid(str(exc)) from exc
    except Exception as exc:  # malformed input reaching the JWT library
        raise TokenInvalid("malformed grant") from exc
    if claims.get("typ") != TYPE or not claims.get("env") or not claims.get("inv"):
        raise TokenInvalid("not an invocation grant")
    return Grant.from_claims(claims)
