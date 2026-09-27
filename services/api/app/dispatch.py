"""Handing ``/rest/v1/*`` to the calling environment's compiled application.

This ASGI middleware sits inside the platform-context and telemetry
middleware, so by the time a request reaches it the gateway's context is
verified and a request id is assigned. It looks up the environment's compiled
Sillo application and calls it with ``/rest/v1`` moved into ``root_path``, which
is how Sillo expects a mounted application to be called. The compiled app's own
routes, auth backends, validation and docs then take over.
"""

from __future__ import annotations

import json
from typing import Any

from pawabase_kit.context import SCOPE_KEY

from app.compiler.build import REST_PREFIX

FORWARDED_KEYS = ("user", "auth", "auth_scheme", "route", "pawabase.policy", "pawabase.plan")


async def _send_json(send, status: int, body: dict[str, Any]) -> None:
    payload = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload})


class DataPlaneDispatcher:
    def __init__(self, platform: Any) -> None:
        self.platform = platform
        self.app: Any = None

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == REST_PREFIX or path.startswith(REST_PREFIX + "/")):
            await self.app(scope, receive, send)
            return
        context = scope.get(SCOPE_KEY)
        if context is None:
            await _send_json(send, 401, {"error": "missing_api_key", "message": "Send a project API key in the apikey header."})
            return
        from sillo.exceptions import HTTPException

        try:
            state = await self.platform.state_for(context)
        except HTTPException as exc:
            await _send_json(send, exc.status_code, {"error": "unknown_environment", "message": str(exc.detail)})
            return
        compiled = await state.compiled()
        inner = dict(scope)
        rest = path[len(REST_PREFIX) :] or "/"
        inner["path"] = rest
        inner["raw_path"] = rest.encode()
        inner["root_path"] = scope.get("root_path", "") + REST_PREFIX
        try:
            await compiled(inner, receive, send)
        finally:
            for key in FORWARDED_KEYS:
                if key in inner:
                    scope[key] = inner[key]
