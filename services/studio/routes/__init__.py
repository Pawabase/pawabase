"""Studio's pages and its JSON bridge to the other services.

Pages are Inertia responses: the server resolves who is signed in and the data
a page opens with, React renders it. Everything a page does afterwards goes
through ``/studio/api/<service>/...``, which forwards to the management
endpoints of the API, Akountz or Angula with a service token naming the
operator. The browser never talks to those services and never holds a token
they would accept.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Any

import httpx
import websockets
from sillo import HttpContext, SilloApp, WebSocketContext, html
from sillo.openapi.ui import ATLAS_JS
from sillo.responses import JSONResponse
from sillo.static import StaticFiles
from sillo_inertia import Inertia, back, redirect, render, set_errors

from app import operators
from app.config import StudioSettings
from pawabase_kit.clients import ServiceClient, ServiceError
from pawabase_kit.context import CONTEXT_HEADER, PlatformContext
from pawabase_kit.tokens import TokenInvalid, issue_context_token, verify_context_token

OPERATOR_SCOPE = "studio.operator"

#: Studio section → (Inertia component, extra props). Definition sections share
#: one generic editor, driven by the kind.
DEFINITION_KINDS = (
    "schemas",
    "transformers",
    "policies",
    "resources",
    "routes",
    "mail-templates",
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
    "keys": "Env/Keys",
    "secrets": "Env/Secrets",
    "jobs": "Env/Jobs",
    "events": "Env/Events",
    "realtime": "Env/Realtime",
    "observability": "Env/Observability",
    "settings": "Env/Settings",
}

#: Bridge targets: which service, and the path prefix calls are confined to.
BRIDGE: dict[str, tuple[str, str]] = {
    "platform": ("api", "/platform/v1/"),
    "auth": ("akountz", "/admin/v1/"),
    "realtime": ("angula", "/internal/v1/realtime/"),
    "telemetry": ("api", "/internal/v1/telemetry/"),
}


def register_routes(
    app: SilloApp,
    settings: StudioSettings,
    clients: dict[str, ServiceClient],
    inertia: Inertia,
    frontend: Path,
) -> None:
    api, akountz = clients["api"], clients["akountz"]
    master = settings.jwt_master_secret

    inertia.share(
        operator=lambda ctx: (
            operators.public(ctx.scope[OPERATOR_SCOPE]) if ctx.scope.get(OPERATOR_SCOPE) else None
        ),
        gateway_url=settings.public_gateway_url,
    )

    async def signed_in(ctx: HttpContext) -> dict[str, Any] | None:
        operator = await operators.current_operator(ctx, akountz, master)
        if operator is not None:
            ctx.scope[OPERATOR_SCOPE] = operator
        return operator

    async def call(ctx: HttpContext, method: str, path: str, **kwargs: Any) -> Any:
        return await api.request(
            method, "/platform/v1" + path, operator=ctx.scope[OPERATOR_SCOPE], **kwargs
        )

    # ── sign-in ──────────────────────────────────────────────────────────

    @app.get("/login", exclude_from_schema=True)
    async def login_page(ctx: HttpContext):
        if await signed_in(ctx):
            return redirect("/")
        return await render("Auth/Login", {"mfa_token": ctx.query_params.get("mfa_token")})

    @app.post("/login", exclude_from_schema=True)
    async def login(ctx: HttpContext):
        body = await _body(ctx)
        payload: dict[str, Any]
        if body.get("mfa_token"):
            payload = {
                "grant_type": "mfa",
                "mfa_token": body["mfa_token"],
                "code": str(body.get("code") or ""),
            }
        else:
            payload = {
                "grant_type": "password",
                "email": str(body.get("email") or ""),
                "password": str(body.get("password") or ""),
            }
        try:
            await operators.sign_in(ctx, akountz, master, payload)
        except operators.SignInFailed as exc:
            if exc.mfa_token:
                return redirect(f"/login?mfa_token={exc.mfa_token}")
            set_errors(ctx, {"code" if payload["grant_type"] == "mfa" else "email": exc.message})
            return back(fallback="/login")
        return redirect("/")

    @app.post("/logout", exclude_from_schema=True)
    async def logout(ctx: HttpContext):
        await operators.revoke(ctx, akountz)
        return redirect("/login")

    # ── pages ────────────────────────────────────────────────────────────

    async def page(ctx: HttpContext, component: str, loader) -> Any:
        if not await signed_in(ctx):
            return redirect("/login")
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

    @app.get("/", exclude_from_schema=True)
    async def home(ctx: HttpContext):
        async def load():
            projects = await call(ctx, "GET", "/projects")
            overview = await call(ctx, "GET", "/overview")
            return {"projects": projects.get("data", projects), "overview": overview}

        return await page(ctx, "Projects/Index", load)

    @app.get("/audit", exclude_from_schema=True)
    async def audit(ctx: HttpContext):
        async def load():
            return {
                "entries": (await call(ctx, "GET", "/audit", params={"limit": 200})).get("data", [])
            }

        return await page(ctx, "Audit", load)

    async def project_props(ctx: HttpContext, ref: str) -> dict[str, Any]:
        project = await call(ctx, "GET", f"/projects/{ref}")
        envs = await call(ctx, "GET", f"/projects/{ref}/envs")
        return {"project": project, "envs": envs.get("data", envs)}

    @app.get("/projects/{ref}", exclude_from_schema=True)
    async def project_page(ctx: HttpContext, ref: str):
        return await page(ctx, "Projects/Show", lambda: project_props(ctx, ref))

    @app.get("/projects/{ref}/{env}", exclude_from_schema=True)
    async def env_page(ctx: HttpContext, ref: str, env: str):
        async def load():
            props = await project_props(ctx, ref)
            overview = await call(ctx, "GET", f"/projects/{ref}/envs/{env}/overview")
            return {**props, "env": env, "section": "overview", "overview": overview}

        return await page(ctx, "Env/Overview", load)

    @app.get("/projects/{ref}/{env}/flows/{name}", exclude_from_schema=True)
    async def flow_editor(ctx: HttpContext, ref: str, env: str, name: str):
        async def load():
            props = await project_props(ctx, ref)
            blocks = await call(ctx, "GET", "/blocks")
            flow = None
            if name != "new":
                flow = await call(ctx, "GET", f"/projects/{ref}/envs/{env}/flows/{name}")
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
    # The public page at <gateway>/docs/v1/<ref>/<env> exists only while the
    # environment sets ``public_docs``. Operators get the same document here,
    # signed in, whatever that setting says, with "try it" aimed at the gateway.

    async def environment_openapi(ctx: HttpContext, ref: str, env: str) -> dict[str, Any]:
        document = await call(ctx, "GET", f"/projects/{ref}/envs/{env}/openapi")
        document["servers"] = [{"url": settings.public_gateway_url, "description": f"Gateway · {ref} · {env}"}]
        return document

    @app.get("/projects/{ref}/{env}/api-docs/openapi.json", exclude_from_schema=True)
    async def api_docs_spec(ctx: HttpContext, ref: str, env: str):
        if await signed_in(ctx) is None:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        try:
            return JSONResponse(await environment_openapi(ctx, ref, env))
        except ServiceError as exc:
            return JSONResponse({"detail": str(exc.body)}, status_code=exc.status)

    @app.get("/projects/{ref}/{env}/api-docs", exclude_from_schema=True)
    async def api_docs(ctx: HttpContext, ref: str, env: str):
        if await signed_in(ctx) is None:
            return redirect("/login")
        try:
            document = await environment_openapi(ctx, ref, env)
        except ServiceError as exc:
            return JSONResponse({"detail": str(exc.body)}, status_code=exc.status)
        # The spec is embedded rather than fetched by URL: Atlas offers the
        # page's own origin ("This server", i.e. Studio) as the default target
        # whenever the spec came from that origin, which would aim every
        # "try it" request at Studio instead of the gateway.
        spec = json.dumps(document).replace("</", "<\\/")
        title = html_escape(f"{document.get('info', {}).get('title', ref)} · {env}")
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

    @app.get("/projects/{ref}/{env}/{section}", exclude_from_schema=True)
    async def section_page(ctx: HttpContext, ref: str, env: str, section: str):
        component = SECTIONS.get(section)

        async def load():
            if component is None:
                raise ServiceError(404, {"detail": f"no section {section!r}"}, service="studio")
            props = await project_props(ctx, ref)
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
        operator = await signed_in(ctx)
        if operator is None:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
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
    # environment — an operator's tool, the same way an Ably control-plane
    # key can see every channel.
    #
    # Sillo's session middleware only runs on HTTP scopes, so a WebSocket
    # handshake carries no session. The browser instead fetches a short-lived,
    # signed ticket over a normal (session-checked) HTTP call first, then
    # presents that ticket as the socket opens; the ticket alone proves an
    # operator asked for this project and environment a few seconds ago.

    angula_ws_base = (
        settings.angula_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1).rstrip("/")
        + "/realtime/v1/socket"
    )

    @app.get("/projects/{ref}/{env}/realtime/ticket", exclude_from_schema=True)
    async def realtime_ticket(ctx: HttpContext, ref: str, env: str):
        if await signed_in(ctx) is None:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        context = PlatformContext(project=ref, env=env, role="operator", key_id="studio-console")
        return JSONResponse({"ticket": issue_context_token(settings.internal_secret, context, ttl=20)})

    @app.ws_route("/studio/ws/realtime")
    async def realtime_console(ws: WebSocketContext):
        try:
            context = verify_context_token(ws.query_params.get("ticket") or "", settings.internal_secret)
        except TokenInvalid:
            await ws.close(code=4001, reason="sign in first")
            return
        if context.role != "operator":
            await ws.close(code=4001, reason="sign in first")
            return
        await ws.accept()
        service_context = PlatformContext(
            project=context.project, env=context.env, role="service", key_id="studio-console"
        )
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

    async def bridge(ctx: HttpContext, target: str, path: str):
        if target not in BRIDGE:
            return JSONResponse({"detail": "unknown service"}, status_code=404)
        operator = await signed_in(ctx)
        if operator is None:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
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
        call: dict[str, Any] = {"json": body, "params": params or None, "operator": operator}
        segments = clean.split("/")
        if (
            target == "realtime"
            and ctx.method == "POST"
            and len(segments) == 3
            and segments[2] == "publish"
        ):
            # Broadcasting acts inside one environment, which Angula reads from
            # the platform context rather than from the path.
            prefix, clean = "/internal/v1/publish", ""
            call["context"] = PlatformContext(
                project=segments[0], env=segments[1], role="service", key_id="studio"
            )
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
