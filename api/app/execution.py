"""Running flows and functions, from any trigger, with a record of each run."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from app.platform import Platform, json_safe
from app.runtime import ApiRuntime, function_context
from app.state import EnvironmentState
from database.models import FlowRun as FlowRunRecord
from database.models import FunctionRun
from pawabase_core.errors import FunctionFailed
from pawabase_core.failures import report
from pawabase_core.flows import FlowError, FlowRun
from pawabase_core.functions import MAIN, FunctionError
from pawabase_core.ids import new_ulid
from pawabase_core.schemas import validate_payload
from pawabase_core.telemetry import note, span

logger = logging.getLogger("pawabase.execution")


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
    depth: int = 0,
) -> FlowRun:
    """Execute flow *name* and record the run. Raises :class:`FlowError`."""
    flow = state.flows.get(name)
    if flow is None:
        raise NotFound(f"no flow {name!r}")
    if not flow.enabled:
        raise FlowError(f"flow {name!r} is disabled", status=409, code="disabled")
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
    branch: str | None = None,
) -> Any:
    """Call a Python function visible to this environment (on *branch*, when given) and retain its run log."""
    spec = platform.function_spec(state.env_name, name, branch)
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
    runtime = ApiRuntime(
        platform, state, auth=auth, request_id=request_id, depth=depth, branch=branch
    )
    context = function_context(runtime, input, trigger, request)
    started = time.perf_counter()
    status, output, error = "succeeded", None, None
    try:
        with span("function", name, trigger=trigger):
            owner = _owner_of(spec) if platform.isolated(state, spec) else None
            work = (
                platform.sandboxes.invoke(
                    platform.deployments,
                    env=owner[0],
                    branch=owner[1],
                    function=name,
                    context=context,
                    runtime=runtime,
                    transactions=platform.sandbox_transactions,
                )
                if owner is not None
                else spec.handler(context)
            )
            output = await asyncio.wait_for(work, timeout=spec.timeout)
        return output
    except TimeoutError as exc:
        status, error = "failed", f"function {name!r} exceeded {spec.timeout}s"
        raise FlowError(
            f"function {name!r} exceeded {spec.timeout}s", status=504, code="timeout"
        ) from exc
    except FunctionError as exc:
        # A function that chose to fail: its status and code reach the caller as they are, not as a 500.
        status, error = "failed", exc.message
        raise FlowError(exc.message, status=exc.status, code=exc.code, details=exc.details) from exc
    except Exception as exc:
        # Whatever the function raised without meaning to: the run keeps what broke and where in the function (not thirty frames of the
        # framework), and the caller gets a function error carrying a request id. The original stays as its cause, for the log.
        failure = report(exc, roots=_function_roots(platform))
        status, error = "failed", failure.line()
        raise FunctionFailed(f"function {name!r} failed") from exc
    finally:
        try:
            await platform.records.add(
                FunctionRun(
                    id=new_ulid(),
                    env=state.env_name,
                    branch=branch or MAIN,
                    function=name,
                    deployment_id=_deployment_of(platform, spec),
                    trigger=trigger,
                    status=status,
                    input=json_safe(input) if input is not None else {},
                    # The columns are NOT NULL on Postgres (the migration gave them no null=True): a failed run records an empty output, not SQL NULL.
                    output=json_safe(output)
                    if status == "succeeded" and output is not None
                    else {},
                    error=error,
                    logs=json_safe([*context.logs, *runtime.logs]),
                    duration_ms=round((time.perf_counter() - started) * 1000, 3),
                    request_id=request_id,
                )
            )
        except Exception:
            # Observability must not turn a successful user function into a
            # failure when the platform control database is degraded.
            logger.exception("could not record function run %s", name)


def _function_roots(platform: Platform) -> tuple[str, ...]:
    """Where function code lives, so a failure is placed in the function's own file."""
    settings = platform.settings
    return tuple(str(path) for path in (settings.deployments_path, settings.code_path) if path)


def _owner_of(spec: Any) -> tuple[str, str] | None:
    """The environment and branch whose deployment defines *spec* (a branch may use the environment's), or ``None`` for mounted code."""
    if "/" not in spec.project:
        return None
    _, rest = spec.project.split("/", 1)
    env, _, branch = rest.partition("@")
    return env, branch or MAIN


def _deployment_of(platform: Platform, spec: Any) -> str | None:
    """The id of the deployment a function came from (``None`` for code mounted on the runtime)."""
    if "/" not in spec.project:
        return None
    _, rest = spec.project.split("/", 1)
    env, _, branch = rest.partition("@")
    stamp = platform.deployments.stamp(env, branch or MAIN)
    return stamp["id"] if stamp else None
