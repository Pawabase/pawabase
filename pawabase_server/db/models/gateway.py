"""Gateway replicas: which processes are serving this installation's public traffic right now.

A row is one running ``pawabase-server serve`` process (Edge + the ``Platform`` app). It self-registers at start-up and renews a
heartbeat while it runs; a row whose heartbeat has gone stale is not deleted (so its history stays), it is simply excluded from
"healthy" by every reader (``is_healthy``). This is the same shape as :mod:`pawabase_server.core.leases`: a heartbeat *is* a lease on
"I am up," read, never written, by anything other than the instance itself.

Nothing about a replica's identity lives only here: the row is observability and fleet management for Studio and a load balancer's
health check, never a precondition for the replica serving requests (see ``pawabase_server/gateway/registry.py``).
"""

from __future__ import annotations

from sillo.record import Model
from tortoise import fields


class GatewayInstance(Model):
    """One gateway replica of this installation."""

    #: Stable across restarts only if the operator sets ``PAWABASE_GATEWAY_INSTANCE_ID``; otherwise a fresh id per process start,
    #: which is correct too (a restarted process is a new instance, not a resurrection of the old one).
    id = fields.CharField(max_length=128, primary_key=True)
    #: Which API plane placed it, for an installation with more than one (empty for a plain self-hosted single-host deployment).
    plane_id = fields.CharField(max_length=64, default="", db_index=True)
    #: Where a load balancer or Studio reaches its public port (host:port, or a container/service name Traefik resolves).
    host = fields.CharField(max_length=255)
    #: Its internal listener, for Studio's per-instance calls (telemetry, functions-runtime status).
    internal_url = fields.CharField(max_length=255)
    #: The running build/version string, for staged rollouts and the Studio fleet view.
    version = fields.CharField(max_length=64, default="")
    #: True once this instance has asked to stop taking new traffic; a load balancer's health check and Studio both read this.
    draining = fields.BooleanField(default=False)
    #: Set once, at registration; read by the reconciler to prune a replica that asked to drain and never came back.
    drain_requested_at = fields.DatetimeField(null=True)
    heartbeat_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "pb_gateway_instances"
        ordering = ["host"]

    def is_healthy(self, *, stale_after: float) -> bool:
        """Whether this row should still be treated as a live, routable replica."""
        if self.draining:
            return False
        from datetime import UTC, datetime

        age = (datetime.now(UTC) - self.heartbeat_at).total_seconds()
        return age <= stale_after

    def describe(self) -> dict[str, object]:
        return {
            "id": self.id,
            "plane_id": self.plane_id or None,
            "host": self.host,
            "internal_url": self.internal_url,
            "version": self.version,
            "draining": self.draining,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
