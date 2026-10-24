"""The gateway instance registry: register, heartbeat, drain, list — and the background task one replica runs on itself.

Reading the registry never affects request handling (every query here is read-only against ``GatewayInstance``, and the request
path never consults it). Writing it is cheap and best-effort: a replica that cannot reach its own database to heartbeat keeps
serving (§9 of the design: the registry degrading is observability lost, not availability lost), it only stops being *shown* as
healthy once its heartbeat goes stale enough that readers exclude it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import socket
from datetime import UTC, datetime, timedelta

from pawabase_server.core.records import upsert
from pawabase_server.db.models import GatewayInstance
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.gateway")


def default_instance_id() -> str:
    """A reasonably stable id for a process that was not given one: its hostname plus a short random suffix.

    Not fully stable across restarts (a new suffix each time) on purpose: a plain restart of the same container is, for the
    registry's purposes, a new instance replacing an old one, not a resurrection — the old row simply ages out (never deleted, kept
    for history) while the new one registers fresh. An orchestrator that wants true identity continuity sets
    ``PAWABASE_GATEWAY_INSTANCE_ID`` itself.
    """
    return f"{socket.gethostname()}-{secrets.token_hex(4)}"


class GatewayRegistry:
    """One instance's handle on the registry: its own identity, and read access to the fleet."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.id = settings.gateway_instance_id or default_instance_id()
        self.plane_id = settings.gateway_plane_id
        self.host = (
            settings.gateway_public_host or f"{_display_host(settings.host)}:{settings.port}"
        )
        self.internal_url = (
            f"http://{_display_host(settings.internal_host)}:{settings.internal_port}"
        )
        self.version = settings.gateway_version
        self._task: asyncio.Task | None = None
        self._draining = False

    # ── this instance's own lifecycle ───────────────────────────────────

    async def register(self) -> None:
        """Upsert this instance's row. Safe to call again (a restart re-registers under the same id if one was configured)."""
        try:
            await upsert(
                GatewayInstance,
                {
                    "plane_id": self.plane_id,
                    "host": self.host,
                    "internal_url": self.internal_url,
                    "version": self.version,
                    "draining": False,
                },
                id=self.id,
            )
            logger.info(
                "gateway instance %s registered (plane=%s, host=%s)",
                self.id,
                self.plane_id or "-",
                self.host,
            )
        except Exception:  # noqa: BLE001 - registration must never stop the gateway from serving
            logger.exception("could not register gateway instance %s", self.id)

    async def heartbeat(self) -> None:
        try:
            await GatewayInstance.filter(id=self.id).update(
                heartbeat_at=datetime.now(UTC), draining=self._draining
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "could not renew heartbeat for gateway instance %s", self.id, exc_info=True
            )

    async def drain(self) -> None:
        """Mark this instance as draining: it stops being shown as healthy, so new traffic should stop reaching it.

        This never stops the process or rejects requests by itself (there is no custom proxy here, §3/§6 of the design —
        the load balancer and the orchestrator act on the flag); it only makes the flag visible as fast as the next heartbeat.
        """
        self._draining = True
        try:
            await GatewayInstance.filter(id=self.id).update(
                draining=True, drain_requested_at=datetime.now(UTC)
            )
            logger.info("gateway instance %s is draining", self.id)
        except Exception:  # noqa: BLE001
            logger.warning("could not mark gateway instance %s as draining", self.id, exc_info=True)

    def start(self) -> None:
        """Register once, then heartbeat on an interval until :meth:`stop`."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Mark this instance draining (one last, best-effort write) and stop the heartbeat loop."""
        await self.drain()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        await self.register()
        try:
            while True:
                await asyncio.sleep(self.settings.gateway_heartbeat_seconds)
                await self.heartbeat()
        except asyncio.CancelledError:
            return

    # ── reading the fleet ────────────────────────────────────────────────

    async def list_instances(self, *, include_draining: bool = True) -> list[GatewayInstance]:
        query = GatewayInstance.all().order_by("host")
        if not include_draining:
            query = query.filter(draining=False)
        return await query

    async def is_ready(self) -> bool:
        """Whether *this* instance should keep receiving new traffic: it has not asked to drain."""
        return not self._draining

    @staticmethod
    def stale_cutoff(settings: Settings) -> datetime:
        return datetime.now(UTC) - timedelta(seconds=settings.gateway_stale_seconds)

    async def prune_stale(self, *, older_than_seconds: float) -> int:
        """Delete rows whose heartbeat is far older than the staleness window (housekeeping, not correctness: a stale row is already
        excluded from every "healthy" reading; this only keeps the table from growing forever across many restarts)."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        return await GatewayInstance.filter(heartbeat_at__lt=cutoff).delete()


def _display_host(bind_host: str) -> str:
    """A bind address like ``0.0.0.0`` is meaningless to show as where to *reach* something; fall back to ``localhost``."""
    return "localhost" if bind_host in ("0.0.0.0", "::", "") else bind_host
