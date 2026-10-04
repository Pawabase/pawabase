"""Pawabase policies as Sillo storage policies.

Sillo's :class:`~sillo.storage.Bucket` asks its policy two questions:
``allows(action, key, user)`` and ``signable(action)``. This adapter answers the
first from a Pawabase policy, with the object key and the path segments of the
key available to the condition::

    {"eq": ["$object.segments.0", "$auth.user_id"]}   # users/<id>/... layout

Sillo's storage API is synchronous at the policy boundary, so only JSON
conditions are accepted here. Python policies are enforced in the storage
routes, before the bucket is called.
"""

from __future__ import annotations

from typing import Any

from ..principal import policy_auth
from .engine import PolicyEngine, evaluate


class PolicyStorage:
    """A Sillo storage policy backed by Pawabase policies.

    Args:
        engine: Resolves references.
        read: Policy reference for reading and listing.
        write: Policy reference for uploads and deletes.
        credential: The request's credential context (service keys bypass).
        signed_reads: Whether signed URLs may grant reads.
        signed_writes: Whether signed URLs may grant uploads.
    """

    def __init__(
        self,
        engine: PolicyEngine,
        *,
        read: Any = "authenticated",
        write: Any = "authenticated",
        credential: dict[str, Any] | None = None,
        env: str | None = None,
        signed_reads: bool = True,
        signed_writes: bool = True,
    ) -> None:
        self.engine = engine
        self.read_policy = engine.resolve_ref(read)
        self.write_policy = engine.resolve_ref(write)
        for resolved in (self.read_policy, self.write_policy):
            if resolved.handler is not None:
                raise ValueError("storage policies must be JSON conditions")
        self.credential = credential or {"is_service": False, "role": "anon", "scopes": []}
        self.env = env
        self.signed_reads = signed_reads
        self.signed_writes = signed_writes

    def allows(self, action: Any, key: str, user: Any = None) -> bool:
        if self.credential.get("is_service"):
            return True
        name = getattr(action, "value", str(action))
        policy = self.read_policy if name in ("read", "list", "stat") else self.write_policy
        context = {
            "auth": policy_auth(user),
            "credential": self.credential,
            "env": self.env,
            "object": {"key": key, "segments": key.split("/"), "action": name},
        }
        return evaluate(policy.condition, context)

    def signable(self, action: Any) -> bool:
        name = getattr(action, "value", str(action))
        return self.signed_reads if name in ("read", "list", "stat") else self.signed_writes
