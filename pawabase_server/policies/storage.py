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

from pawabase_server.core.principal import policy_auth
from pawabase_server.policies.engine import PolicyEngine, evaluate


class PolicyStorage:
    """A Sillo storage policy backed by Pawabase policies.

    Args:
        engine: Resolves references.
        read: Policy reference for reading.
        write: Policy reference for uploading a new object.
        list: Policy reference for listing; unset follows ``read``.
        update: Policy reference for replacing an object that exists; unset follows ``write``.
        delete: Policy reference for deleting; unset follows ``write``.
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
        list: Any = None,
        update: Any = None,
        delete: Any = None,
        credential: dict[str, Any] | None = None,
        env: str | None = None,
        signed_reads: bool = True,
        signed_writes: bool = True,
    ) -> None:
        self.engine = engine
        self.read_policy = engine.resolve_ref(read)
        self.write_policy = engine.resolve_ref(write)
        self.list_policy = engine.resolve_ref(list) if list is not None else self.read_policy
        self.update_policy = engine.resolve_ref(update) if update is not None else self.write_policy
        self.delete_policy = engine.resolve_ref(delete) if delete is not None else self.write_policy
        for resolved in (
            self.read_policy,
            self.write_policy,
            self.list_policy,
            self.update_policy,
            self.delete_policy,
        ):
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
        policy = {
            "read": self.read_policy,
            "stat": self.read_policy,
            "list": self.list_policy,
            "update": self.update_policy,
            "delete": self.delete_policy,
        }.get(name, self.write_policy)
        context = {
            "auth": policy_auth(user),
            "credential": self.credential,
            "env": self.env,
            "object": {"key": key, "segments": key.split("/"), "action": name},
        }
        return evaluate(policy.condition, context)

    def allows_update(self, key: str, user: Any = None) -> bool:
        """Whether this caller may replace an object that already exists."""
        return self.allows("update", key, user)

    def signable(self, action: Any) -> bool:
        name = getattr(action, "value", str(action))
        return self.signed_reads if name in ("read", "list", "stat") else self.signed_writes
