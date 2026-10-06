"""Studio's pages and its JSON bridge to the other services.

Studio manages the one runtime it is deployed with, so there is nothing to
sign in to and no project to pick: opening it shows the runtime's default
environment. Pages are Inertia responses (the server loads the data a page
opens with, React renders it). Everything a page does afterwards goes through
``/studio/api/<service>/...``, which forwards to the management endpoints of
the API, Akountz or Angula with a service token. The browser never talks to
those services and never holds a token they would accept.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import websockets
from sillo import HttpContext, SilloApp, WebSocketContext, html
from sillo.openapi.ui import ATLAS_JS
from sillo.responses import JSONResponse
from sillo.static import StaticFiles
from sillo_inertia import Inertia, redirect, render

from app.config import StudioSettings
from pawabase_core.clients import ServiceClient, ServiceError
from pawabase_core.context import CONTEXT_HEADER, PlatformContext
from pawabase_core.tokens import TokenInvalid, issue_context_token, verify_context_token

#: Studio section → (Inertia component, extra props). Definition sections share
#: one generic editor, driven by the kind.
DEFINITION_KINDS = (
    "schemas",
    "transformers",
    "policies",
    "resources",
    "routes",
    "subscriptions",
    "webhooks",
    "inbound-hooks",
    "schedules",
)
SECTIONS: dict[str, str] = {
    **{kind: "Env/Definitions" for kind in DEFINITION_KINDS},
    "database": "Env/Database",
    "flows": "Env/Flows",
    "functions": "Env/Functions",
    "storage": "Env/Storage",
    "users": "Env/Users",
    "mail": "Env/Mail",
    "keys": "Env/Keys",
    "secrets": "Env/Secrets",
    "backups": "Env/Backups",
    "jobs": "Env/Jobs",
    "events": "Env/Events",
    "realtime": "Env/Realtime",
    "observability": "Env/Observability",
    "usage": "Env/Usage",
    "explorer": "Env/Explorer",
    "settings": "Env/Settings",
}

#: Bridge targets: which service, and the path prefix calls are confined to.
BRIDGE: dict[str, tuple[str, str]] = {
    "platform": ("api", "/platform/v1/"),
    "auth": ("akountz", "/admin/v1/"),
    "realtime": ("angula", "/internal/v1/realtime/"),
    "telemetry": ("api", "/internal/v1/telemetry/"),
    "gateway": ("gateway", "/internal/v1/gateway/"),
}


def register_routes(
    app: SilloApp,
    settings: StudioSettings,
    clients: dict[str, ServiceClient],
    inertia: Inertia,
    frontend: Path,
) -> None:
    api, akountz = clients["api"], clients["akountz"]

    inertia.share(
        runtime_name=settings.project_name,
        gateway_url=settings.public_gateway_url,
    )

    async def call(ctx: HttpContext, method: str, path: str, **kwargs: Any) -> Any:
        # Inertia pages are rendered on the server, unlike follow-up browser
        # requests made through the Studio bridge. Carry the checked-out branch
        # here too so the first render of an editor cannot accidentally show
        # main while its saves go to a feature branch.
        branch = ctx.query_params.get("branch")
        if branch and path.startswith("/envs/"):
            separator = "&" if "?" in path else "?"
            path = f"{path}{separator}branch={quote(branch, safe='')}"
        return await api.request(method, "/platform/v1" + path, **kwargs)

    # ── pages ────────────────────────────────────────────────────────────

    async def page(ctx: HttpContext, component: str, loader) -> Any:
        try:
            props = await loader()
        except ServiceError as exc:
            if exc.status == 404:
                return await render("Errors/NotFound", {"message": _detail(exc)}, status_code=404)
            return await render(
                "Errors/Unavailable",
                {"message": _detail(exc), "service": exc.service},
                status_code=502,
            )
        return await render(component, props)

    async def env_props(ctx: HttpContext) -> dict[str, Any]:
        runtime = await call(ctx, "GET", "/runtime")
        return {"runtime": runtime, "envs": runtime["environments"]}

    @app.get("/", exclude_from_schema=True)
    async def home(ctx: HttpContext):
        """Open straight into the runtime's default environment."""
        try:
            runtime = await call(ctx, "GET", "/runtime")
        except ServiceError as exc:
            return await render(
                "Errors/Unavailable",
                {"message": _detail(exc), "service": exc.service},
                status_code=502,
            )
        environments = runtime["environments"]
        chosen = next((e for e in environments if e.get("is_default")), None) or (
            environments[0] if environments else None
        )
        return redirect(f"/envs/{chosen['name']}" if chosen else "/environments")

    @app.get("/environments", exclude_from_schema=True)
    async def environments_page(ctx: HttpContext):
        return await page(ctx, "Environments", lambda: env_props(ctx))

    @app.get("/audit", exclude_from_schema=True)
    async def audit(ctx: HttpContext):
        async def load():
            entries = await call(ctx, "GET", "/audit", params={"limit": 200})
            return {**await env_props(ctx), "entries": entries.get("data", [])}

        return await page(ctx, "Audit", load)

    @app.get("/envs/{env}", exclude_from_schema=True)
    async def env_page(ctx: HttpContext, env: str):
        async def load():
            props = await env_props(ctx)
            overview = await call(ctx, "GET", f"/envs/{env}/overview")
            return {**props, "env": env, "section": "overview", "overview": overview}

        return await page(ctx, "Env/Overview", load)

    @app.get("/envs/{env}/flows/{name}", exclude_from_schema=True)
    async def flow_editor(ctx: HttpContext, env: str, name: str):
        async def load():
            props = await env_props(ctx)
            blocks = await call(ctx, "GET", "/blocks")
            flow = None
            if name != "new":
                flow = await call(ctx, "GET", f"/envs/{env}/flows/{name}")
            return {
                **props,
                "env": env,
                "section": "flows",
                "flow": flow,
                "blocks": blocks.get("data", blocks),
            }

        return await page(ctx, "Flows/Editor", load)

    # ── API docs ─────────────────────────────────────────────────────────
    #
    # The public page at <gateway>/docs/v1/<env> exists only while the
    # environment sets ``public_docs``. Studio shows the same document whatever
    # that setting says, with "try it" aimed at the gateway.

    async def environment_openapi(ctx: HttpContext, env: str) -> dict[str, Any]:
        document = await call(ctx, "GET", f"/envs/{env}/openapi")
        document["servers"] = [{"url": settings.public_gateway_url, "description": f"Gateway · {env}"}]
        return document

    @app.get("/envs/{env}/api-docs/openapi.json", exclude_from_schema=True)
    async def api_docs_spec(ctx: HttpContext, env: str):
        try:
            return JSONResponse(await environment_openapi(ctx, env))
        except ServiceError as exc:
            return JSONResponse({"detail": str(exc.body)}, status_code=exc.status)

    @app.get("/envs/{env}/api-docs", exclude_from_schema=True)
    async def api_docs(ctx: HttpContext, env: str):
        try:
            document = await environment_openapi(ctx, env)
        except ServiceError as exc:
            return JSONResponse({"detail": str(exc.body)}, status_code=exc.status)
        # The spec is embedded rather than fetched by URL: Atlas offers the
        # page's own origin ("This server", i.e. Studio) as the default target
        # whenever the spec came from that origin, which would aim every
        # "try it" request at Studio instead of the gateway.
        spec = json.dumps(document).replace("</", "<\\/")
        title = html_escape(f"{document.get('info', {}).get('title', 'API')} · {env}")
        return html(
            f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>html, body {{ margin: 0; padding: 0; height: 100%; }}</style>
</head>
<body>
    <div id="app"></div>
    <script src="{ATLAS_JS}"></script>
    <script>Atlas.createApiReference('#app', {{ theme: 'auto', spec: {spec} }});</script>
</body>
</html>"""
        )

    @app.get("/envs/{env}/{section}", exclude_from_schema=True)
    async def section_page(ctx: HttpContext, env: str, section: str):
        component = SECTIONS.get(section)

        async def load():
            if component is None:
                raise ServiceError(404, {"detail": f"no section {section!r}"}, service="studio")
            props = await env_props(ctx)
            return {
                **props,
                "env": env,
                "section": section,
                "kind": section if section in DEFINITION_KINDS else None,
            }

        return await page(ctx, component or "Errors/NotFound", load)

    # ── service status ───────────────────────────────────────────────────

    async def probe(label: str, check) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(check(), timeout=3.0)
            status, detail = "up", None
        except Exception as exc:
            status, detail = "down", f"{type(exc).__name__}: {exc}"[:200]
        return {
            "name": label,
            "status": status,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "detail": detail,
        }

    async def gateway_health() -> None:
        async with httpx.AsyncClient(timeout=3.0) as client:
            (await client.get(settings.gateway_url.rstrip("/") + "/health")).raise_for_status()

    @app.get("/studio/status", exclude_from_schema=True)
    async def status(ctx: HttpContext):
        """Every service's health, for the status lights in Studio's top bar."""
        services = list(
            await asyncio.gather(
                probe("Gateway", gateway_health),
                probe("API", lambda: clients["api"].get("/health")),
                probe("Auth", lambda: clients["akountz"].get("/health")),
                probe("Realtime", lambda: clients["angula"].get("/health")),
            )
        )
        try:
            workers = (await call(ctx, "GET", "/workers")).get("data", [])
        except Exception:
            workers = None
        for kind, label in (("worker", "Worker"), ("scheduler", "Scheduler")):
            if workers is None:
                services.append({"name": label, "status": "unknown", "detail": "the API is unreachable"})
                continue
            # Heartbeats of processes that exited long ago stay in the table;
            # only processes seen recently say anything about health now.
            mine = [w for w in workers if w.get("kind") == kind and _recent(w)]
            alive = [w for w in mine if w.get("alive")]
            services.append(
                {
                    "name": label,
                    "status": "up" if alive else ("down" if mine else "unknown"),
                    "detail": f"{len(alive)} running" if alive else ("stopped" if mine else "not running"),
                }
            )
        return JSONResponse({"services": services, "checked_at": time.time()})

    # ── realtime console ─────────────────────────────────────────────────
    #
    # Studio's Realtime page is a genuine client of Angula's own socket
    # protocol, not a polling dashboard: the browser opens one WebSocket here
    # and this handler relays it to Angula's ``/realtime/v1/socket``, signed
    # with a *service* platform context. A service credential bypasses every
    # channel policy (see ``Realtime.authorize``), so the console can watch,
    # subscribe to presence on, and publish to any channel in the
    # environment: a developer's tool, the same way an Ably control-plane
    # key can see every channel.
    #
    # A WebSocket handshake cannot carry Studio's CSRF header, so the browser
    # first fetches a short-lived, signed ticket over a normal HTTP call, then
    # presents it as the socket opens; the ticket alone proves Studio issued it
    # for this environment a few seconds ago.

    angula_ws_base = (
        settings.angula_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1).rstrip("/")
        + "/realtime/v1/socket"
    )

    TICKET = "studio-console-ticket"

    @app.get("/envs/{env}/realtime/ticket", exclude_from_schema=True)
    async def realtime_ticket(ctx: HttpContext, env: str):
        context = PlatformContext(env=env, role="anon", key_id=TICKET)
        return JSONResponse({"ticket": issue_context_token(settings.internal_secret, context, ttl=20)})

    @app.ws_route("/studio/ws/realtime")
    async def realtime_console(ws: WebSocketContext):
        try:
            context = verify_context_token(ws.query_params.get("ticket") or "", settings.internal_secret)
        except TokenInvalid:
            await ws.close(code=4001, reason="invalid ticket")
            return
        if context.key_id != TICKET:
            await ws.close(code=4001, reason="invalid ticket")
            return
        await ws.accept()
        service_context = PlatformContext(env=context.env, role="service", key_id="studio-console")
        header = issue_context_token(settings.internal_secret, service_context, ttl=60)
        try:
            remote = await websockets.connect(
                angula_ws_base, additional_headers={CONTEXT_HEADER: header}, open_timeout=10, max_size=2**20
            )
        except Exception:
            await ws.close(code=1011, reason="could not reach the realtime service")
            return

        async def browser_to_remote() -> None:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    await remote.close()
                    return
                if message.get("text") is not None:
                    await remote.send(message["text"])
                elif message.get("bytes") is not None:
                    await remote.send(message["bytes"])

        async def remote_to_browser() -> None:
            try:
                async for data in remote:
                    if isinstance(data, bytes):
                        await ws.send_bytes(data)
                    else:
                        await ws.send_text(data)
            finally:
                await ws.close()

        tasks = [asyncio.create_task(browser_to_remote()), asyncio.create_task(remote_to_browser())]
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        try:
            await remote.close()
        except Exception:
            pass

    # ── the bridge ───────────────────────────────────────────────────────

    @app.post("/studio/api/explorer/sign-in", exclude_from_schema=True)
    async def explorer_sign_in(ctx: HttpContext):
        """Obtain an application-user token for Explorer without an API key."""
        body = await _body(ctx)
        env = str(body.get("env") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", env):
            return JSONResponse({"detail": "invalid environment"}, status_code=400)
        if body.get("mfa_token"):
            payload = {
                "grant_type": "mfa",
                "mfa_token": str(body["mfa_token"]),
                "code": str(body.get("code") or ""),
            }
        else:
            payload = {
                "grant_type": "password",
                "email": str(body.get("email") or ""),
                "password": str(body.get("password") or ""),
            }
        try:
            result = await akountz.request(
                "POST",
                "/auth/v1/token",
                json=payload,
                context=PlatformContext(env=env, role="anon", key_id="studio-explorer"),
            )
        except ServiceError as exc:
            return JSONResponse(
                exc.body if isinstance(exc.body, dict) else {"detail": exc.body},
                status_code=exc.status,
            )
        visible = {
            key: result[key]
            for key in ("access_token", "token_type", "expires_in", "mfa_required", "mfa_token")
            if key in result
        }
        return JSONResponse(visible)

    @app.post("/studio/api/explorer/request", exclude_from_schema=True)
    async def explorer_request(ctx: HttpContext):
        """Run one data-plane request with a signed anonymous context.

        Studio deliberately uses an ``anon`` context for the explored request.
        This keeps policy behavior honest: public endpoints work immediately and
        authenticated endpoints only work when an application-user token is
        supplied.
        """
        body = await _body(ctx)
        env = str(body.get("env") or "")
        version = str(body.get("version") or "v1")
        method = str(body.get("method") or "GET").upper()
        requested_path = str(body.get("path") or "/")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", env):
            return JSONResponse({"detail": "invalid environment"}, status_code=400)
        if not re.fullmatch(r"v[1-9][0-9]*", version):
            return JSONResponse({"detail": "invalid API version"}, status_code=400)
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return JSONResponse({"detail": "unsupported method"}, status_code=400)
        expected_prefix = f"/rest/{version}"
        if requested_path == expected_prefix:
            requested_path = "/"
        elif requested_path.startswith(expected_prefix + "/"):
            requested_path = requested_path[len(expected_prefix) :]
        if not requested_path.startswith("/"):
            requested_path = "/" + requested_path
        if ".." in requested_path.split("/") or requested_path.startswith("/rest/"):
            return JSONResponse({"detail": "invalid endpoint path"}, status_code=400)

        supplied_headers = body.get("headers") if isinstance(body.get("headers"), dict) else {}
        blocked = {
            "apikey",
            "authorization",
            "cookie",
            "host",
            "x-pawabase-context",
            "x-pawabase-service",
        }
        forwarded_headers = {
            str(key): str(value)
            for key, value in supplied_headers.items()
            if str(key).lower() not in blocked and value not in (None, "")
        }
        access_token = str(body.get("access_token") or "").strip()
        if access_token.lower().startswith("bearer "):
            access_token = access_token[7:].strip()
        if access_token:
            forwarded_headers["Authorization"] = f"Bearer {access_token}"
        query = body.get("query") if isinstance(body.get("query"), dict) else None
        payload = body.get("body") if method in {"POST", "PUT", "PATCH", "DELETE"} else None
        started = time.perf_counter()
        try:
            result = await api.request_raw(
                method,
                expected_prefix + requested_path,
                json=payload,
                params=query,
                context=PlatformContext(env=env, role="anon", key_id="studio-explorer"),
                headers=forwarded_headers,
            )
        except Exception as exc:
            return JSONResponse(
                {
                    "detail": f"the API could not be reached: {type(exc).__name__}",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                status_code=502,
            )
        visible_headers = {
            key: value
            for key, value in result.get("headers", {}).items()
            if key.lower()
            in {
                "cache-control",
                "content-length",
                "content-type",
                "deprecation",
                "etag",
                "location",
                "retry-after",
                "sunset",
                "x-request-id",
            }
        }
        return JSONResponse(
            {
                "status": result["status"],
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "headers": visible_headers,
                "body": result.get("body"),
            }
        )

    async def bridge(ctx: HttpContext, target: str, path: str):
        if target not in BRIDGE:
            return JSONResponse({"detail": "unknown service"}, status_code=404)
        service, prefix = BRIDGE[target]
        clean = path.lstrip("/")
        if ".." in clean.split("/"):
            return JSONResponse({"detail": "bad path"}, status_code=400)
        body = None
        if ctx.method in ("POST", "PUT", "PATCH", "DELETE"):
            raw = await ctx.body
            if raw:
                try:
                    body = json.loads(raw)
                except ValueError:
                    return JSONResponse({"detail": "send JSON"}, status_code=400)
        params = dict(ctx.query_params)
        call: dict[str, Any] = {"json": body, "params": params or None}
        segments = clean.split("/")
        if (
            target == "realtime"
            and ctx.method == "POST"
            and len(segments) == 2
            and segments[1] == "publish"
        ):
            # Broadcasting acts inside one environment, which Angula reads from
            # the platform context rather than from the path.
            prefix, clean = "/internal/v1/publish", ""
            call["context"] = PlatformContext(env=segments[0], role="service", key_id="studio")
        try:
            result = await clients[service].request(ctx.method, prefix + clean, **call)
        except ServiceError as exc:
            return JSONResponse(
                exc.body if isinstance(exc.body, dict) else {"detail": exc.body},
                status_code=exc.status,
            )
        if result in ({}, None, "") and ctx.method == "DELETE":
            return JSONResponse(None, status_code=204)
        return JSONResponse(result)

    app.route(
        "/studio/api/{target}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        handler=bridge,
        name="bridge",
        exclude_from_schema=True,
    )

    # ── the built front end ──────────────────────────────────────────────

    static = StaticFiles(
        directory=frontend / "dist", cache_control="public, max-age=31536000, immutable"
    )

    @app.get("/assets/{path:path}", exclude_from_schema=True)
    async def assets(ctx: HttpContext, path: str):
        return await static._handle(ctx)


async def _body(ctx: HttpContext) -> dict[str, Any]:
    content_type = ctx.headers.get("content-type", "")
    if "application/json" in content_type:
        raw = await ctx.body
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    form = await ctx.form()
    return {k: form.get(k) for k in form}


def _recent(worker: dict[str, Any], seconds: float = 600) -> bool:
    """Seen within *seconds*, or an in-process worker (which has no heartbeat)."""
    seen = worker.get("last_seen")
    if not seen:
        return bool(worker.get("alive"))
    try:
        return time.time() - datetime.fromisoformat(seen).timestamp() < seconds
    except ValueError:
        return False


def _detail(exc: ServiceError) -> str:
    return str(exc.body.get("detail") if isinstance(exc.body, dict) else exc.body)
