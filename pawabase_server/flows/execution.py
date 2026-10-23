"""Running flows, from any trigger, with a record of each run."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from pawabase_server.core.telemetry import note, span
from pawabase_server.db.models import FlowRun as FlowRunRecord
from pawabase_server.environments.state import EnvironmentState
from pawabase_server.flows import FlowError, FlowRun
from pawabase_server.flows.api_runtime import ApiRuntime
from pawabase_server.platform import Platform, json_safe
from pawabase_server.projects import quotas

logger = logging.getLogger("pawabase.execution")


class NotFound(LookupError):
    pass


async def metered(platform: Platform, state: EnvironmentState, limit: str) -> None:
    """Count one run against the project's monthly plan, as a :class:`FlowError` the caller already knows how to answer."""
    try:
        await platform.usage.consume(state, limit)
    except quotas.QuotaExceeded as exc:
        raise FlowError(exc.message, status=exc.status, code=exc.code, details=exc.details) from exc


async def run_flow(
    platform: Platform,
    state: EnvironmentState,
    name: str,
    input: Any,
    *,
    trigger: str,
    auth: Mapping[str, Any] | None = None,
    credential: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    job_id: str | None = None,
    entry: str | None = None,
    depth: int = 0,
) -> FlowRun:
    """Execute flow *name* and record the run. Raises :class:`FlowError`."""
    flow = state.flows.get(name)
    if flow is None:
        raise NotFound(f"no flow {name!r}")
    if not flow.enabled:
        raise FlowError(f"flow {name!r} is disabled", status=409, code="disabled")
    await metered(platform, state, "max_flow_runs_per_month")
    runtime = ApiRuntime(platform, state, auth=auth, request_id=request_id, depth=depth)
    run = FlowRun(
        flow.definition,
        runtime=runtime,
        input=input,
        auth=auth,
        env=state.env_name,
        trigger=entry,
        name=name,
        timeout=flow.timeout,
    )
    run.state["credential"] = dict(credential or {"is_service": False})
    run.state["run"]["request_id"] = request_id
    run.state["run"]["trigger"] = trigger
    run.state["run"]["api_version"] = state.api_version
    run.state["run"]["release_id"] = state.release_id
    run.state["run"]["revision_id"] = state.revision_id
    if job_id:
        run.state["run"]["job_id"] = job_id
    started = time.perf_counter()
    status, error = "succeeded", None
    request_flow = {"run_id": run.id, "flow": name, "trigger": trigger, "status": "running"}
    note("flow_runs", request_flow, append=True)
    try:
        with span("flow", name, trigger=trigger):
            await run.execute()
        return run
    except FlowError as exc:
        status, error = "failed", exc.message
        raise
    except Exception as exc:
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        raise FlowError(error, code="flow_failed") from exc
    finally:
        request_flow["status"] = status
        request_flow["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        if flow.record_runs:
            await platform.records.add(
                FlowRunRecord(
                    id=run.id,
                    env=state.env_name,
                    flow=name,
                    trigger=trigger,
                    status=status,
                    input=json_safe(input),
                    output=json_safe(run.result()) if status == "succeeded" else None,
                    error=error,
                    trace=run.trace(),
                    logs=json_safe(run.logs),
                    duration_ms=request_flow["duration_ms"],
                    request_id=request_id,
                    job_id=job_id,
                )
            )
