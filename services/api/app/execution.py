"""Running flows and functions, from any trigger, with a record of each run."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.platform import Platform, json_safe
from app.runtime import ApiRuntime, function_context
from app.state import EnvironmentState
from database.models import FlowRun as FlowRunRecord
from pawabase_kit.flows import FlowError, FlowRun
from pawabase_kit.functions import get_function
from pawabase_kit.schemas import validate_payload
from pawabase_kit.telemetry import note


class NotFound(LookupError):
    pass


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
) -> FlowRun:
    """Execute flow *name* and record the run. Raises :class:`FlowError`."""
    flow = state.flows.get(name)
    if flow is None:
        raise NotFound(f"no flow {name!r}")
    if not flow.enabled:
        raise FlowError(f"flow {name!r} is disabled", status=409, code="disabled")
    runtime = ApiRuntime(platform, state, auth=auth, request_id=request_id)
    run = FlowRun(
        flow.definition,
        runtime=runtime,
        input=input,
        auth=auth,
        project=state.project_ref,
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
            await FlowRunRecord.create(
                id=run.id,
                project=state.project_ref,
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


async def call_function(
    platform: Platform,
    state: EnvironmentState,
    name: str,
    input: Any,
    *,
    trigger: str,
    auth: Mapping[str, Any] | None = None,
    request_id: str | None = None,
    depth: int = 0,
    request: Mapping[str, Any] | None = None,
) -> Any:
    """Call a Python function registered for this project."""
    spec = get_function(state.project_ref, name)
    if spec is None:
        raise NotFound(f"no function {name!r}")
    if spec.input_fields:
        try:
            input = validate_payload(spec.input_fields, input or {}, mode="create")
        except ValidationError as exc:
            import json

            raise FlowError(
                "invalid function input",
                status=422,
                code="invalid",
                details=json.loads(exc.json(include_url=False)),
            ) from exc
    runtime = ApiRuntime(platform, state, auth=auth, request_id=request_id, depth=depth)
    context = function_context(runtime, input, trigger, request)
    try:
        return await asyncio.wait_for(spec.handler(context), timeout=spec.timeout)
    except TimeoutError as exc:
        raise FlowError(
            f"function {name!r} exceeded {spec.timeout}s", status=504, code="timeout"
        ) from exc
