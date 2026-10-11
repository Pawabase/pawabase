"""What Studio's route modules share: the server it manages, and small helpers for reading requests and reporting failures."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sillo import HttpContext
from sillo.responses import JSONResponse
from sillo_inertia import Inertia

from pawabase_server.core.clients import ServiceClient, ServiceError
from studio.config import StudioSettings

MAX_JSON_BODY = 25 * 1024 * 1024


@dataclass
class Deps:
    """Everything a route module needs, passed once instead of closed over by one enormous function."""

    settings: StudioSettings
    #: The server Studio manages, reached in-process with a service token. Studio holds no credential of its own.
    core: ServiceClient
    inertia: Inertia
    frontend: Path

    async def call(self, ctx: HttpContext, method: str, path: str, **kwargs: Any) -> Any:
        """Call the API's management endpoints.

        Inertia pages are rendered on the server, unlike follow-up browser requests made through the Studio bridge. The checked-out
        branch is carried here too, so the first render of an editor cannot show main while its saves go to a feature branch.
        """
        branch = ctx.query_params.get("branch")
        if branch and path.startswith("/envs/"):
            separator = "&" if "?" in path else "?"
            path = f"{path}{separator}branch={quote(branch, safe='')}"
        return await self.core.request(method, "/platform/v1" + path, **kwargs)

    async def env_props(self, ctx: HttpContext) -> dict[str, Any]:
        runtime = await self.call(ctx, "GET", "/runtime")
        return {"runtime": runtime, "envs": runtime["environments"]}


def detail_of(exc: ServiceError) -> str:
    return str(exc.body.get("detail") if isinstance(exc.body, dict) else exc.body)


def error_response(exc: ServiceError) -> JSONResponse:
    """A service's error, as the browser should see it: the service's own body and status."""
    body = exc.body if isinstance(exc.body, dict) else {"detail": exc.body}
    return JSONResponse(body, status_code=exc.status)


def failure_response(exc: BaseException, service: str) -> JSONResponse:
    """A failure that is not a clean error from the service: a timeout is a 504, anything else that went wrong on the way is a 502."""
    name = type(exc).__name__
    status = 504 if "Timeout" in name else 502
    what = "did not answer in time" if status == 504 else "could not be reached"
    return JSONResponse({"detail": f"the {service} service {what}"}, status_code=status)


async def json_body(ctx: HttpContext) -> dict[str, Any]:
    """A request's body as a mapping: JSON, or a form. Anything unreadable is an empty mapping, never an exception."""
    content_type = ctx.headers.get("content-type", "")
    if "application/json" in content_type:
        raw = await ctx.body
        if len(raw or b"") > MAX_JSON_BODY:
            return {}
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    form = await ctx.form()
    return {k: form.get(k) for k in form}


def recently_seen(worker: dict[str, Any], seconds: float = 600) -> bool:
    """Seen within *seconds*, or an in-process worker (which has no heartbeat)."""
    seen = worker.get("last_seen")
    if not seen:
        return bool(worker.get("alive"))
    try:
        return time.time() - datetime.fromisoformat(seen).timestamp() < seconds
    except ValueError:
        return False
