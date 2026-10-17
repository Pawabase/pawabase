"""Deployment control plane for uploaded custom Function source artifacts.

``pawabase deploy`` posts a bundle here. It is activated for one environment and one branch (``main`` unless the CLI says otherwise): a branch's functions
exist beside the environment's own, are reached by naming the branch, and fall back to the environment's functions for everything the branch does not
redefine. See :mod:`pawabase_server.functions.deployments` for how artifacts are stored and switched.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created
from sillo.exceptions import HTTPException

from pawabase_server.core.context import current_context
from pawabase_server.core.principal import ANONYMOUS_POLICY_CONTEXT
from pawabase_server.core.ids import new_ulid
from pawabase_server.db.models import Environment, FunctionDeployment, FunctionRun
from pawabase_server.functions.deployments import DeploymentError
from pawabase_server.functions import limits as limits_module
from pawabase_server.functions.registry import MAIN
from pawabase_server.functions.runtime import ApiRuntime
from pawabase_server.functions.sandbox import typescript
from pawabase_server.functions.service.grants import Grant, new_invocation_id
from pawabase_server.functions.service import watchdog
from pawabase_server.functions.service.runtime import HttpRequest, variables_version
from pawabase_server.platform import Platform
from pawabase_server.routes.common import (
    MANAGE,
    NAME_PATTERN,
    actor,
    audit,
    dump,
    get_environment,
    page_params,
)


def _manage_scope(ctx: HttpContext) -> None:
    context = current_context(ctx)
    if context is not None and context.is_service and not context.allows_scope("functions:manage"):
        raise HTTPException(
            status_code=403, detail="This API key lacks the 'functions:manage' scope"
        )


class TryBody(BaseModel):
    """A request to send to an HTTP function from the management plane (Studio's "Try it")."""

    method: str = Field(default="GET", pattern=r"^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)$")
    path: str = Field(default="/", max_length=2000, description="What follows /functions/v1/<name>, with its query string")
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, description="Text, or base64 when body_base64 is true")
    body_base64: bool = False
    as_user: dict[str, Any] | None = Field(default=None, description="The auth context to run as; anonymous when omitted")
    branch: str | None = None


class RestartBody(BaseModel):
    function: str | None = None


#: What a "Try it" request reads of a response before it stops (the rest is reported as cut off).
TRY_MAX_BYTES = 1024 * 1024


class DeployBody(BaseModel):
    archive: str = Field(description="base64 encoded .tar.gz source bundle")
    branch: str = Field(
        default=MAIN, pattern=NAME_PATTERN, description="The branch these functions belong to"
    )
    manifest: dict[str, Any] = Field(
        default_factory=dict,
        description="Facts about the build: git commit, kit version, the functions found locally",
    )
    runtime: str = Field(default="python3.11", max_length=64)
    limits: dict[str, Any] = Field(default_factory=dict)


def _problem(error: DeploymentError) -> HTTPException:
    return HTTPException(
        status_code=error.status, detail={"message": error.message, "problems": error.problems}
    )


SOURCE_SUFFIXES = {
    ".py",
    ".ts",
    ".mts",
    ".js",
    ".mjs",
    ".toml",
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
}
MAX_SOURCE_BYTES = 400_000


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    async def activate(
        env: str, deployment: FunctionDeployment, raw: bytes | None
    ) -> FunctionDeployment:
        """Install (or re-install) *deployment*'s artifact and make it the only active one on its branch."""
        try:
            # Libraries are installed off the event loop (it can take a while); activating then finds them already there.
            stored = raw
            if stored is None:
                source = platform.deployments.archive(env, deployment.branch, deployment.id)
                stored = source.read_bytes() if source.is_file() else None
            if stored is None:
                # The local copy is gone (another server, a lost volume): use the one kept in storage.
                stored = await platform.deployment_mirror.get(env, deployment.branch, deployment.id)
                raw = stored
            if stored is not None:
                await asyncio.to_thread(platform.deployments.prepare, stored)
            if raw is None:
                code = platform.deployments.reactivate(env, deployment.branch, deployment.id)
            else:
                code = platform.deployments.install(
                    env,
                    deployment.branch,
                    raw,
                    deployment_id=deployment.id,
                    checksum=deployment.checksum,
                )
        except DeploymentError as exc:
            deployment.status, deployment.error = (
                "failed",
                "; ".join([exc.message, *exc.problems])[:2000],
            )
            await deployment.save(update_fields=["status", "error"])
            raise _problem(exc) from exc
        if raw is not None:
            await platform.deployment_mirror.put(env, deployment.branch, deployment.id, raw)
        await (
            FunctionDeployment.filter(
                environment_id=deployment.environment_id, branch=deployment.branch, status="active"
            )
            .exclude(id=deployment.id)
            .update(status="superseded")
        )
        deployment.status, deployment.error, deployment.activated_at = (
            "active",
            "",
            datetime.now(UTC),
        )
        deployment.manifest = {
            **(deployment.manifest or {}),
            "functions": code.functions,
            "language": platform.deployments.language(env, deployment.branch),
        }
        await deployment.save(update_fields=["status", "error", "activated_at", "manifest"])
        platform.envs.forget(env)
        return deployment

    async def enforce_one_language(environment: Environment, raw: bytes) -> None:
        """A project's functions are one language: refuse a bundle in another one than the project's live deployments."""
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
                language = typescript.language_of(
                    [m.name.lstrip("./") for m in bundle.getmembers() if m.isfile()]
                )
        except tarfile.TarError:
            return  # installing reports a bad archive with its own message
        if language in (None, "mixed"):
            return  # installing refuses these with the reason
        scope = (
            FunctionDeployment.filter(
                environment__project_id=environment.project_id, status="active"
            )
            if environment.project_id
            else FunctionDeployment.filter(environment_id=environment.id, status="active")
        )
        for row in await scope.select_related("environment"):
            existing = (row.manifest or {}).get("language") or typescript.PYTHON
            if existing != language:
                raise HTTPException(
                    status_code=409,
                    detail=f"This project's functions are {existing.title()} (live in {row.environment.name}); a project's functions are one language.",
                )

    async def enforce_deployment_limit(environment: Environment, branch: str) -> None:
        """Reserve an active function-deployment slot for a new branch.

        A deployment revision replaces the active revision on its branch, so updating
        an existing branch does not consume another slot.  The limit is project-wide
        and is read from the cached environment state (which is invalidated when the
        project's plan changes), while the active count is authoritative in the
        control-plane database.
        """
        state = await platform.state(environment.name)
        raw_limit = state.project_limits.get("max_function_deployments")
        if raw_limit in (None, 0):  # zero/omitted means unlimited in project policies
            return
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return
        if limit < 1 or not state.project_id:
            return

        # Re-deploying or rolling back the current branch is a replacement, not a
        # new slot.  This check also makes the reactivate endpoint obey the same rule.
        if await FunctionDeployment.filter(
            environment_id=environment.id, branch=branch, status="active"
        ).exists():
            return

        environment_ids = await Environment.filter(project_id=state.project_id).values_list(
            "id", flat=True
        )
        used = await FunctionDeployment.filter(
            environment_id__in=environment_ids, status="active"
        ).count()
        if used >= limit:
            raise HTTPException(
                status_code=402,
                detail={
                    "message": (
                        f"This project's plan allows {limit} active function deployment"
                        f"{'s' if limit != 1 else ''}; upgrade your plan to deploy another."
                    ),
                    "code": "plan_limit",
                    "limit": limit,
                    "used": used,
                },
            )

    @r.get(f"{base}/function-deployments", auth=MANAGE, tags=["functions"])
    async def deployments(ctx: HttpContext, env: str):
        _manage_scope(ctx)
        environment = await get_environment(env)
        limit, offset = page_params(ctx)
        query = FunctionDeployment.filter(environment=environment)
        if branch := ctx.query_params.get("branch"):
            query = query.filter(branch=branch)
        return {"data": [dump(row) for row in await query.offset(offset).limit(limit)]}

    @r.post(
        f"{base}/function-deployments", auth=MANAGE, tags=["functions"], request_model=DeployBody
    )
    async def deploy(ctx: HttpContext, env: str, body: DeployBody):
        _manage_scope(ctx)
        environment = await get_environment(env)
        try:
            raw = base64.b64decode(body.archive, validate=True)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="archive is not valid base64") from exc
        if len(raw) > platform.limit(env, "MAX_UPLOAD_BYTES", platform.settings.max_upload_bytes):
            raise HTTPException(status_code=413, detail="archive exceeds the upload limit")
        await enforce_deployment_limit(environment, body.branch)
        await enforce_one_language(environment, raw)
        deployment = await FunctionDeployment.create(
            id=new_ulid(),
            environment=environment,
            branch=body.branch,
            checksum=hashlib.sha256(raw).hexdigest(),
            runtime=body.runtime,
            manifest=body.manifest,
            limits=body.limits,
            created_by=actor(ctx),
        )
        await activate(env, deployment, raw)
        await audit(
            ctx,
            "function.deployed",
            env=env,
            target=deployment.id,
            details={"checksum": deployment.checksum, "branch": body.branch},
        )
        specs = platform.function_specs(env, body.branch)
        return created(
            {"deployment": dump(deployment), "functions": [spec.describe() for spec in specs]}
        )

    @r.get(f"{base}/function-deployments/{{deployment_id}}", auth=MANAGE, tags=["functions"])
    async def deployment(ctx: HttpContext, env: str, deployment_id: str):
        _manage_scope(ctx)
        environment = await get_environment(env)
        row = await FunctionDeployment.get_or_none(id=deployment_id, environment=environment)
        if row is None:
            raise HTTPException(status_code=404, detail="no such function deployment")
        return dump(row)

    @r.post(
        f"{base}/function-deployments/{{deployment_id}}/activate", auth=MANAGE, tags=["functions"]
    )
    async def reactivate(ctx: HttpContext, env: str, deployment_id: str):
        """Roll back (or forward) to a deployment whose artifact is still stored."""
        _manage_scope(ctx)
        environment = await get_environment(env)
        row = await FunctionDeployment.get_or_none(id=deployment_id, environment=environment)
        if row is None or row.status == "removed":
            raise HTTPException(status_code=404, detail="no such function deployment")
        await enforce_deployment_limit(environment, row.branch)
        await activate(env, row, None)
        await audit(
            ctx, "function.activated", env=env, target=row.id, details={"branch": row.branch}
        )
        return {
            "deployment": dump(row),
            "functions": [spec.describe() for spec in platform.function_specs(env, row.branch)],
        }

    @r.delete(f"{base}/function-deployments/{{deployment_id}}", auth=MANAGE, tags=["functions"])
    async def remove(ctx: HttpContext, env: str, deployment_id: str):
        _manage_scope(ctx)
        environment = await get_environment(env)
        row = await FunctionDeployment.get_or_none(id=deployment_id, environment=environment)
        if row is None:
            raise HTTPException(status_code=404, detail="no such function deployment")
        if row.status == "active":
            raise HTTPException(status_code=409, detail="an active deployment cannot be removed")
        archive = platform.deployments.archive(env, row.branch, row.id)
        archive.unlink(missing_ok=True)
        row.status, row.removed_at = "removed", datetime.now(UTC)
        await row.save(update_fields=["status", "removed_at"])
        await audit(ctx, "function.deployment_removed", env=env, target=deployment_id)
        return {"removed": True}

    @r.get(f"{base}/function-branches", auth=MANAGE, tags=["functions"])
    async def branches(ctx: HttpContext, env: str):
        """Every branch with code deployed, and the deployment that is active on it."""
        _manage_scope(ctx)
        await get_environment(env)
        out = []
        for name in [MAIN, *[b for b in platform.deployments.branches(env) if b != MAIN]]:
            stamp = platform.deployments.stamp(env, name)
            if stamp is None and name == MAIN:
                continue
            functions = platform.function_specs(env, name)
            out.append(
                {
                    "branch": name,
                    "deployment_id": (stamp or {}).get("id"),
                    "checksum": (stamp or {}).get("checksum"),
                    "functions": [spec.name for spec in functions],
                }
            )
        return {"data": out}

    @r.delete(f"{base}/function-branches/{{branch}}", auth=MANAGE, tags=["functions"])
    async def remove_branch(ctx: HttpContext, env: str, branch: str):
        """Delete a branch's functions. The environment's own are untouched."""
        _manage_scope(ctx)
        environment = await get_environment(env)
        try:
            removed = platform.deployments.remove_branch(env, branch)
        except DeploymentError as exc:
            raise _problem(exc) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="that branch has no deployed functions")
        await (
            FunctionDeployment.filter(environment=environment, branch=branch)
            .exclude(status="removed")
            .update(status="removed", removed_at=datetime.now(UTC))
        )
        await audit(ctx, "function.branch_removed", env=env, target=branch)
        return {"removed": True}

    # ── source: what is deployed, readable ───────────────────────────────

    def _source_root(env: str, branch: str) -> Path:
        """Where the code being served sits: the active deployment, else the code mounted for the project."""
        current = platform.deployments.current(env, branch)
        return current if current.is_dir() else Path(platform.settings.code_path)

    def _listable(root: Path):
        for item in sorted(root.rglob("*")):
            if not item.is_file():
                continue
            parts = item.relative_to(root).parts
            if any(
                part.startswith(".") or part in ("__pycache__", "node_modules", "packages")
                for part in parts
            ):
                continue
            if item.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            yield item

    @r.get(
        f"{base}/function-source",
        auth=MANAGE,
        tags=["functions"],
        summary="The files of the code being served",
    )
    async def source_tree(ctx: HttpContext, env: str):
        _manage_scope(ctx)
        await get_environment(env)
        branch = ctx.query_params.get("branch") or MAIN
        root = _source_root(env, branch)
        files = []
        if root.is_dir():
            for item in _listable(root):
                files.append(
                    {"path": item.relative_to(root).as_posix(), "size": item.stat().st_size}
                )
                if len(files) >= 400:
                    break
        stamp = platform.deployments.stamp(env, branch) or {}
        return {
            "branch": branch,
            "deployment_id": stamp.get("id"),
            "checksum": stamp.get("checksum"),
            "mounted": not platform.deployments.current(env, branch).is_dir(),
            "files": files,
        }

    @r.get(
        f"{base}/function-source/file", auth=MANAGE, tags=["functions"], summary="One source file"
    )
    async def source_file(ctx: HttpContext, env: str):
        _manage_scope(ctx)
        await get_environment(env)
        branch = ctx.query_params.get("branch") or MAIN
        requested = ctx.query_params.get("path") or ""
        root = _source_root(env, branch).resolve()
        target = (root / requested).resolve()
        if not requested or root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        if (
            any(part.startswith(".") for part in target.relative_to(root).parts)
            or target.suffix.lower() not in SOURCE_SUFFIXES
        ):
            raise HTTPException(status_code=404, detail="no such file")
        size = target.stat().st_size
        if size > MAX_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail="that file is too large to show")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="that file is not text") from exc
        return {"path": requested, "size": size, "content": text, "lines": text.count("\n") + 1}

    @r.get(f"{base}/function-runs", auth=MANAGE, tags=["functions"])
    async def runs(ctx: HttpContext, env: str):
        _manage_scope(ctx)
        limit, offset = page_params(ctx)
        query = FunctionRun.filter(env=env)
        if name := ctx.query_params.get("function"):
            query = query.filter(function=name)
        if branch := ctx.query_params.get("branch"):
            query = query.filter(branch=branch)
        if status := ctx.query_params.get("status"):
            query = query.filter(status=status)
        if after := ctx.query_params.get(
            "after"
        ):  # a follow loop asks only for what is newer than the last row it saw
            query = query.filter(created_at__gt=datetime.fromisoformat(after))
        return {
            "data": [
                dump(row, exclude=("logs",)) for row in await query.offset(offset).limit(limit)
            ]
        }

    @r.get(f"{base}/function-runs/{{run_id}}", auth=MANAGE, tags=["functions"])
    async def run(ctx: HttpContext, env: str, run_id: str):
        _manage_scope(ctx)
        row = await FunctionRun.get_or_none(id=run_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such function run")
        return dump(row)

    # ── HTTP functions and the runtime ───────────────────────────────────

    @r.get(f"{base}/functions-runtime", auth=MANAGE, tags=["functions"], summary="Where functions run, and what is running")
    async def runtime_status(ctx: HttpContext, env: str):
        """The runtime (embedded or the service), its limits, and for each HTTP function its workers and statistics."""
        _manage_scope(ctx)
        await get_environment(env)
        return await platform.functions.status(env)

    @r.post(
        f"{base}/functions-runtime/restart",
        auth=MANAGE,
        tags=["functions"],
        request_model=RestartBody,
        summary="Drain function workers so the next request starts fresh ones",
    )
    async def runtime_restart(ctx: HttpContext, env: str, body: RestartBody):
        _manage_scope(ctx)
        await get_environment(env)
        result = await platform.functions.restart(env, body.function)
        await audit(ctx, "functions.restart", env=env, target=body.function or "", details=result)
        return result

    @r.get(f"{base}/http-functions/{{name}}", auth=MANAGE, tags=["functions"], summary="One HTTP function, with its statistics")
    async def http_function(ctx: HttpContext, env: str, name: str):
        _manage_scope(ctx)
        await platform.state(env)
        branch = ctx.query_params.get("branch") or None
        spec = platform.http_function(env, name, branch)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no HTTP function {name!r}")
        hours = max(1, min(int(ctx.query_params.get("hours") or 24), 24 * 30))
        since = datetime.now(UTC).timestamp() - hours * 3600
        rows = await FunctionRun.filter(
            env=env, function=name, kind="http", created_at__gte=datetime.fromtimestamp(since, UTC)
        ).values("status", "status_code", "duration_ms", "cold_start")
        durations = sorted(float(r["duration_ms"] or 0) for r in rows)

        def percentile(fraction: float) -> float | None:
            if not durations:
                return None
            return round(durations[min(len(durations) - 1, round(fraction * (len(durations) - 1)))], 2)

        codes: dict[str, int] = {}
        for row in rows:
            family = f"{(row['status_code'] or 0) // 100}xx" if row["status_code"] else "none"
            codes[family] = codes.get(family, 0) + 1
        base_url = platform.settings.public_url.rstrip("/")
        return {
            **spec.describe(),
            "url": f"{base_url}/functions/v1/{name}",
            "public_url": f"{base_url}/functions/v1/public/{env}/{name}" if spec.config.auth == "public" else None,
            "stats": {
                "hours": hours,
                "requests": len(rows),
                "failed": sum(1 for row in rows if row["status"] == "failed"),
                "cold_starts": sum(1 for row in rows if row["cold_start"]),
                "status_codes": codes,
                "latency_ms": {"p50": percentile(0.5), "p95": percentile(0.95), "p99": percentile(0.99)},
            },
        }

    @r.post(
        f"{base}/http-functions/{{name}}/request",
        auth=MANAGE,
        tags=["functions"],
        request_model=TryBody,
        summary="Send a request to an HTTP function (Try it)",
    )
    async def try_http_function(ctx: HttpContext, env: str, name: str, body: TryBody):
        """Run the function as the operator says (``as_user``, anonymous by default), bypassing its ``auth`` setting. The run is
        recorded like any other. The response is returned as text (or base64 when it is not UTF-8), up to 1 MB."""
        _manage_scope(ctx)
        state = await platform.state(env)
        spec = platform.http_function(env, name, body.branch)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"no HTTP function {name!r}")
        auth = body.as_user or dict(ANONYMOUS_POLICY_CONTEXT)
        raw = (
            base64.b64decode(body.body) if body.body_base64 and body.body else (body.body or "").encode()
        )
        path, _, query = body.path.partition("?")
        path = path if path.startswith("/") else "/" + path

        async def once():
            yield raw

        invocation = new_invocation_id()
        request = HttpRequest(
            method=body.method,
            url=f"{platform.settings.public_url.rstrip('/')}/functions/v1/{name}{'' if path == '/' else path}"
            + (f"?{query}" if query else ""),
            path=path,
            headers=[(k.lower(), v) for k, v in body.headers.items()],
            body=once() if raw and body.method not in ("GET", "HEAD") else None,
            invocation=invocation,
            request_id=ctx.headers.get("x-request-id"),
            auth=auth,
            project=str(state.project_id or ""),
            client_ip=None,
            variables_version=variables_version(dict(state.secret_values)),
        )
        grant = Grant(
            env=env,
            branch=spec.branch,
            function=name,
            invocation=invocation,
            auth=auth,
            request_id=request.request_id,
            kind="http",
            deployment_id=spec.deployment_id,
        )
        request.limits = await platform.run_limits(state, spec, kind="http")
        runtime = ApiRuntime(platform, state, auth=auth, request_id=request.request_id, branch=spec.branch)
        started = datetime.now(UTC)
        reply = await platform.functions.http(spec, request, runtime=runtime, grant=grant)
        content = bytearray()
        truncated = False
        stream = reply.body
        try:
            async for chunk in stream:
                content.extend(chunk)
                if len(content) > TRY_MAX_BYTES:
                    truncated = True
                    break
        finally:
            await stream.aclose()
        data = bytes(content[:TRY_MAX_BYTES])
        try:
            text, encoded = data.decode("utf-8"), False
        except UnicodeDecodeError:
            text, encoded = base64.b64encode(data).decode(), True
        return {
            "status": reply.status,
            "headers": [[k, v] for k, v in reply.headers],
            "body": text,
            "body_base64": encoded,
            "truncated": truncated,
            "cold_start": reply.cold_start,
            "worker": reply.worker,
            "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000, 2),
            "invocation": invocation,
            "caller": auth,
        }

    # ── resource limits (read-only: they are the project's) ─────────────

    @r.get(f"{base}/function-limits", auth=MANAGE, tags=["functions"], summary="What every function run may use")
    async def function_limits(ctx: HttpContext, env: str):
        """The resource limits every run of each function gets, from the project's limits (else the installation's defaults), and
        where each value came from. They are set when the project is created and changed by whoever operates the installation
        (Pawabase Cloud, or ``pawabase-server projects limits set``); this API only shows them."""
        _manage_scope(ctx)
        state = await platform.state(env)
        rows = []
        for kind, specs in (("call", platform.function_specs(env)), ("http", platform.http_functions(env))):
            for spec in specs:
                effective = await platform.run_limits(state, spec, kind=kind)
                running = platform.function_gates.in_use(env, spec.name)
                rows.append(
                    {
                        "function": spec.name,
                        "kind": kind,
                        "limits": effective.as_dict(),
                        "running": {"now": running[0], "limit": running[1]} if running else None,
                    }
                )
        project = {key: state.project_limits.get(key) for key in limits_module.PROJECT_KEYS.values() if state.project_limits.get(key)}
        defaults, maximums = limits_module.installation(platform.settings, kind="call")
        return {
            "data": rows,
            "project": project,
            "installation": {"defaults": defaults, "maximums": maximums},
            "enforced": {"cpu_and_memory": watchdog.supported(), "timeout": True, "concurrency": True},
        }
