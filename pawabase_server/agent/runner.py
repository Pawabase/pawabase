"""The agent's loop: ask the control plane for instructions, carry them out, report, repeat.

It speaks only outbound. A control plane that is unreachable is waited out (the loop backs off and keeps going); the runtime's own work
does not depend on it, and nothing here is in the path of an application request.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any
from urllib.parse import urlsplit

import httpx

from pawabase_server.agent.executor import Executor, OperationError
from pawabase_server.agent.protocol import ProtocolError, sign_request, verify_job
from pawabase_server.db.database import database
from pawabase_server.settings import Settings

logger = logging.getLogger("pawabase.agent")

MAX_BACKOFF = 60.0


class ConfigurationError(Exception):
    pass


def check_configuration(settings: Settings) -> None:
    missing = [
        name
        for name, value in (
            ("PAWABASE_AGENT_URL", settings.agent_url),
            ("PAWABASE_AGENT_ID", settings.agent_id),
            ("PAWABASE_AGENT_TOKEN", settings.agent_token),
            ("PAWABASE_AGENT_POOL", settings.agent_pool),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(f"the agent needs {', '.join(missing)}")
    parts = urlsplit(settings.agent_url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (
        parts.scheme == "http" and local and settings.app_env != "production"
    ):
        raise ConfigurationError(
            "PAWABASE_AGENT_URL must be https (http only for localhost outside production)"
        )


class ControlPlane:
    """The control plane as the agent sees it: two signed calls."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(
            base_url=settings.agent_url.rstrip("/"), timeout=30.0, follow_redirects=False
        )

    async def call(self, function: str, body: dict[str, Any]) -> dict[str, Any]:
        envelope = sign_request(self.settings.agent_token, self.settings.agent_id, body)
        reply = await self.http.post(f"/functions/v1/{function}", json=envelope)
        reply.raise_for_status()
        return reply.json()

    async def poll(self) -> list[dict[str, Any]]:
        answer = await self.call("agent.poll", {"pool": self.settings.agent_pool, "capacity": 4})
        return list(answer.get("jobs") or [])

    async def report(
        self,
        job: dict[str, Any],
        *,
        ok: bool,
        result: Any = None,
        error: str = "",
        retryable: bool = False,
    ) -> None:
        await self.call(
            "agent.report",
            {
                "job_id": job["id"],
                "attempt": job.get("attempt", 1),
                "status": "done" if ok else "failed",
                "result": result if ok else None,
                "error": error[:500],
                "retryable": retryable,
            },
        )

    async def close(self) -> None:
        await self.http.aclose()


class Agent:
    def __init__(
        self,
        settings: Settings,
        plane: ControlPlane | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.settings = settings
        self.plane = plane or ControlPlane(settings)
        self.executor = executor or Executor(settings)
        self.handled = 0

    async def step(self) -> int:
        """One round: poll, carry out what was handed over, report each result. Returns how many instructions were handled."""
        jobs = await self.plane.poll()
        done = 0
        for raw in jobs:
            try:
                job = verify_job(self.settings.agent_token, raw, pool=self.settings.agent_pool)
            except ProtocolError as exc:
                logger.warning("refused an instruction: %s", exc)
                if raw.get("id"):
                    await self._report_quietly(raw, error=f"refused: {exc}")
                continue
            try:
                result = await self.executor.execute(job)
            except OperationError as exc:
                logger.warning("%s %s failed: %s", job["kind"], job["id"], exc)
                await self._report_quietly(job, error=str(exc), retryable=exc.retryable)
            except Exception as exc:  # noqa: BLE001 - a bug in an operation must not stop the loop
                logger.exception("%s %s crashed", job["kind"], job["id"])
                await self._report_quietly(
                    job, error=f"{type(exc).__name__}: {exc}", retryable=True
                )
            else:
                await self.plane.report(job, ok=True, result=result)
                done += 1
        self.handled += done
        return done

    async def _report_quietly(
        self, job: dict[str, Any], *, error: str, retryable: bool = False
    ) -> None:
        try:
            await self.plane.report(job, ok=False, error=error, retryable=retryable)
        except httpx.HTTPError as exc:
            logger.warning("could not report %s: %s", job.get("id"), exc)

    async def run(self, stop: asyncio.Event) -> None:
        delay = self.settings.agent_poll_seconds
        failures = 0
        while not stop.is_set():
            try:
                handled = await self.step()
                failures = 0
                wait = 0.2 if handled else delay
            except (httpx.HTTPError, ValueError) as exc:
                failures += 1
                wait = min(MAX_BACKOFF, delay * (2**failures))
                logger.warning(
                    "control plane unreachable (%s); retrying in %.0fs", type(exc).__name__, wait
                )
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except TimeoutError:
                pass
        await self.plane.close()


async def run(settings: Settings, *, once: bool = False) -> int:
    check_configuration(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(signal, name), stop.set)
        except (
            NotImplementedError,
            AttributeError,
        ):  # pragma: no cover - platforms without signal handlers
            pass
    async with database(settings):
        agent = Agent(settings)
        if once:
            try:
                await agent.step()
            finally:
                await agent.plane.close()
            return 0
        logger.info("agent %s serving pool %s", settings.agent_id, settings.agent_pool)
        await agent.run(stop)
    return 0
