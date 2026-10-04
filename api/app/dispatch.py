"""Handing ``/rest/<version>/*`` to an immutable released application.

This ASGI middleware sits inside the platform-context and telemetry
middleware, so by the time a request reaches it the gateway's context is
verified and a request id is assigned. It looks up the environment's compiled
Sillo application and calls it with ``/rest/v1`` moved into ``root_path``, which
is how Sillo expects a mounted application to be called. The compiled app's own
routes, auth backends, validation and docs then take over.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from pawabase_core.context import SCOPE_KEY
from pawabase_core.telemetry import note

FORWARDED_KEYS = ("user", "auth", "auth_scheme", "route", "pawabase.policy", "pawabase.plan")
REST_PATH = re.compile(r"^/rest/(?P<version>v[1-9][0-9]*)(?:/|$)")
CONVERTER = re.compile(r"\{(\w+)(?::[^}]*)?\}")


def route_template(compiled: Any, scope: dict[str, Any], prefix: str) -> str | None:
    """The matched route's path template (``/rest/v1/orders/{id}``), for telemetry.

    Sillo's router does not record which route served a request, and without
    it every request to ``/orders/17`` and ``/orders/18`` would be a different
    row in route statistics. The compiled app's routes are flat, so the
    template is the first whose pattern fully matches the request.
    """
    probe = dict(scope)
    for route in getattr(getattr(compiled, "router", None), "routes", ()):
        try:
            status, _ = route.match(probe)
        except Exception:
            continue
        if getattr(status, "name", "") == "FULL":
            return prefix + CONVERTER.sub(r"{\1}", route.raw_path)
    return None


async def _send_json(send, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class DataPlaneDispatcher:
    def __init__(self, platform: Any) -> None:
        self.platform = platform
        self.app: Any = None

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        match = REST_PATH.match(path) if scope["type"] == "http" else None
        if match is None:
            await self.app(scope, receive, send)
            return
        context = scope.get(SCOPE_KEY)
        if context is None:
            await _send_json(
                send,
                401,
                {
                    "error": "missing_api_key",
                    "message": "Send a project API key in the apikey header.",
                },
            )
            return
        from sillo.exceptions import HTTPException

        try:
            version = match.group("version")
            state = await self.platform.state_for_version(context, version)
        except HTTPException as exc:
            await _send_json(
                send,
                exc.status_code,
                {"error": "api_version_unavailable", "message": str(exc.detail)},
            )
            return
        prefix = f"/rest/{version}"
        note("api_version", version)
        if state.release_id:
            note("release_id", state.release_id)
            note("revision_id", state.revision_id)
        compiled = await state.compiled()
        inner = dict(scope)
        rest = path[len(prefix) :] or "/"
        inner["path"] = rest
        inner["raw_path"] = rest.encode()
        inner["root_path"] = scope.get("root_path", "") + prefix
        try:
            await compiled(inner, receive, send)
        finally:
            for key in FORWARDED_KEYS:
                if key in inner:
                    scope[key] = inner[key]
            if scope.get("route") is None:
                template = route_template(compiled, inner, prefix)
                if template:
                    scope["route"] = SimpleNamespace(path=template)
