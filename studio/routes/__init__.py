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
    "releases": "Env/Releases",
    "explorer": "Env/Explorer",
    "settings": "Env/Settings",
}

#: How long a "this operator may use that project" answer is trusted.
ACCESS_TTL = 15.0

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

    # ── organizations ────────────────────────────────────────────────────

    access: dict[tuple[str, str], float] = {}

    async def may_use_project(ctx: HttpContext, ref: str) -> bool:
        """Whether the operator's organizations include the project.

        The API enforces this on every management call; Studio asks too for the
        paths it forwards without the API (realtime, telemetry, identities, the
        Explorer), so those cannot reach another organization's project.
        """
        key = (str(ctx.scope[OPERATOR_SCOPE]["sub"]), ref)
        if access.get(key, 0) > time.monotonic():
            return True
        try:
            await call(ctx, "GET", f"/projects/{ref}")
        except ServiceError:
            return False
        access[key] = time.monotonic() + ACCESS_TTL
        return True

    async def my_orgs(ctx: HttpContext) -> list[dict[str, Any]]:
        return (await call(ctx, "GET", "/orgs")).get("data", [])

    def remember_org(ctx: HttpContext, slug: str | None) -> None:
        session = ctx.scope.get("session")
        if session is not None and slug:
            session.set("org", slug)

    def safe_next(target: str | None) -> str:
        """A same-site path to return to after signing in."""
        if target and target.startswith("/") and not target.startswith("//"):
            return target
        return "/"

    # ── sign-in ──────────────────────────────────────────────────────────

    @app.get("/login", exclude_from_schema=True)
    async def login_page(ctx: HttpContext):
        if await signed_in(ctx):
            return redirect(safe_next(ctx.query_params.get("next")))
        return await render(
            "Auth/Login",
            {"mfa_token": ctx.query_params.get("mfa_token"), "next": ctx.query_params.get("next")},
        )

    @app.post("/login", exclude_from_schema=True)
    async def login(ctx: HttpContext):
        body = await _body(ctx)
        after = safe_next(str(body.get("next") or "") or None)
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
                suffix = f"&next={quote(after)}" if after != "/" else ""
                return redirect(f"/login?mfa_token={exc.mfa_token}{suffix}")
            set_errors(ctx, {"code" if payload["grant_type"] == "mfa" else "email": exc.message})
            return back(fallback="/login")
        return redirect(after)

    @app.post("/logout", exclude_from_schema=True)
    async def logout(ctx: HttpContext):
        await operators.revoke(ctx, akountz)
        return redirect("/login")

    # ── pages ────────────────────────────────────────────────────────────

    async def page(ctx: HttpContext, component: str, loader, *, org_required: bool = True) -> Any:
        if not await signed_in(ctx):
            return redirect("/login")
        try:
            orgs = await my_orgs(ctx)
            if not orgs and org_required:
                # Nothing exists outside an organization: make one first.
                return redirect("/setup")
            props = await loader()
        except ServiceError as exc:
            if exc.status == 404:
                return await render("Errors/NotFound", {"message": _detail(exc)}, status_code=404)
            return await render(
                "Errors/Unavailable",
                {"message": _detail(exc), "service": exc.service},
                status_code=502,
            )
        slug = (props.get("project") or {}).get("org") or (props.get("org") or {}).get("slug")
        remember_org(ctx, slug)
        return await render(component, {"orgs": orgs, **props})

    def role_of(orgs: list[dict[str, Any]], slug: str) -> str | None:
        return next((o["role"] for o in orgs if o["slug"] == slug), None)

    async def landing(ctx: HttpContext, suffix: str = "") -> Any:
        """Send the operator to their organization (the last one they used)."""
        if not await signed_in(ctx):
            return redirect("/login")
        try:
            orgs = await my_orgs(ctx)
        except ServiceError as exc:
            return await render(
                "Errors/Unavailable",
                {"message": _detail(exc), "service": exc.service},
                status_code=502,
            )
        if not orgs:
            return redirect("/setup")
        session = ctx.scope.get("session")
        last = session.get("org") if session is not None else None
        slug = last if any(o["slug"] == last for o in orgs) else orgs[0]["slug"]
        return redirect(f"/orgs/{slug}{suffix}")

    @app.get("/", exclude_from_schema=True)
    async def home(ctx: HttpContext):
        return await landing(ctx)

    @app.get("/setup", exclude_from_schema=True)
    async def setup(ctx: HttpContext):
        """The first thing an operator does: create the organization projects live in."""

        async def load():
            return {"first": True}

        if await signed_in(ctx) and await my_orgs(ctx):
            return redirect("/")
        return await page(ctx, "Org/Create", load, org_required=False)

    @app.get("/orgs/new", exclude_from_schema=True)
    async def new_org(ctx: HttpContext):
        async def load():
            return {"first": False}

        return await page(ctx, "Org/Create", load, org_required=False)

    async def org_props(ctx: HttpContext, slug: str) -> dict[str, Any]:
        return {"org": await call(ctx, "GET", f"/orgs/{slug}")}

    @app.get("/orgs/{slug}", exclude_from_schema=True)
    async def org_home(ctx: HttpContext, slug: str):
        async def load():
            projects = await call(ctx, "GET", "/projects", params={"org": slug})
            overview = await call(ctx, "GET", "/overview", params={"org": slug})
            return {
                **await org_props(ctx, slug),
                "projects": projects.get("data", projects),
                "overview": overview,
            }

        return await page(ctx, "Projects/Index", load)

    @app.get("/orgs/{slug}/team", exclude_from_schema=True)
    async def org_team(ctx: HttpContext, slug: str):
        async def load():
            props = await org_props(ctx, slug)
            members = await call(ctx, "GET", f"/orgs/{slug}/members")
            invitations = []
            if props["org"]["role"] in ("admin", "owner"):
                invitations = (await call(ctx, "GET", f"/orgs/{slug}/invitations")).get("data", [])
            return {
                **props,
                "members": members.get("data", []),
                "invitations": invitations,
                "me": ctx.scope[OPERATOR_SCOPE]["sub"],
            }

        return await page(ctx, "Org/Team", load)

    @app.get("/orgs/{slug}/settings", exclude_from_schema=True)
    async def org_settings(ctx: HttpContext, slug: str):
        return await page(ctx, "Org/Settings", lambda: org_props(ctx, slug))

    @app.get("/orgs/{slug}/audit", exclude_from_schema=True)
    async def org_audit(ctx: HttpContext, slug: str):
        async def load():
            entries = await call(ctx, "GET", "/audit", params={"limit": 200, "org": slug})
            return {**await org_props(ctx, slug), "entries": entries.get("data", [])}

        return await page(ctx, "Audit", load)

    @app.get("/audit", exclude_from_schema=True)
    async def audit(ctx: HttpContext):
        return await landing(ctx, "/audit")

    # ── invitations ──────────────────────────────────────────────────────
    #
    # An invitation link works before sign-in, so its page is public: the
    # token is the credential, and it only ever offers the one address it was
    # sent to. Accepting signs the invitee in, creating their operator account
    # first when they have none.

    async def invitation_page(ctx: HttpContext, token: str, **extra: Any):
        try:
            invitation = await api.request("GET", f"/platform/v1/invitations/{quote(token)}")
        except ServiceError as exc:
            if exc.status != 404:
                raise
            return await render("Auth/Invite", {"token": token, "invitation": None})
        operator = await signed_in(ctx)
        return await render(
            "Auth/Invite",
            {
                "token": token,
                "invitation": invitation,
                "mismatch": bool(operator and operator["email"].lower() != invitation["email"]),
                **extra,
            },
        )

    @app.get("/invite/{token}", exclude_from_schema=True)
    async def invite_page(ctx: HttpContext, token: str):
        return await invitation_page(ctx, token)

    @app.post("/invite/{token}", exclude_from_schema=True)
    async def accept_invite(ctx: HttpContext, token: str):
        body = await _body(ctx)
        try:
            invitation = await api.request("GET", f"/platform/v1/invitations/{quote(token)}")
        except ServiceError:
            return redirect(f"/invite/{token}")
        operator = await signed_in(ctx)
        if operator is None:
            password = str(body.get("password") or "")
            try:
                await akountz.request(
                    "POST",
                    "/admin/v1/projects/_platform/envs/main/users",
                    json={
                        "email": invitation["email"],
                        "password": password,
                        "name": str(body.get("name") or ""),
                        "email_verified": True,
                    },
                )
            except ServiceError as exc:
                if exc.status == 409:
                    # They already have an account: sign in, then come back.
                    return redirect(f"/login?next={quote(f'/invite/{token}')}")
                set_errors(ctx, {"password": _detail(exc)})
                return back(fallback=f"/invite/{token}")
            try:
                await operators.sign_in(
                    ctx,
                    akountz,
                    master,
                    {"grant_type": "password", "email": invitation["email"], "password": password},
                )
            except operators.SignInFailed as exc:
                set_errors(ctx, {"password": exc.message})
                return back(fallback=f"/invite/{token}")
            operator = await signed_in(ctx)
        try:
            joined = await call(ctx, "POST", f"/invitations/{quote(token)}/accept")
        except ServiceError as exc:
            set_errors(ctx, {"invitation": _detail(exc)})
            return back(fallback=f"/invite/{token}")
        return redirect(f"/orgs/{joined['slug']}")

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
        if not await may_use_project(ctx, ref):
            return JSONResponse({"detail": f"no project {ref!r}"}, status_code=404)
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

    @app.post("/studio/api/explorer/sign-in", exclude_from_schema=True)
    async def explorer_sign_in(ctx: HttpContext):
        """Obtain a project-user token for Explorer without a project API key."""
        if await signed_in(ctx) is None:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        body = await _body(ctx)
        project = str(body.get("project") or "")
        env = str(body.get("env") or "")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", project) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,62}", env
        ):
            return JSONResponse({"detail": "invalid project or environment"}, status_code=400)
        if not await may_use_project(ctx, project):
            return JSONResponse({"detail": f"no project {project!r}"}, status_code=404)
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
                context=PlatformContext(
                    project=project, env=env, role="anon", key_id="studio-explorer"
                ),
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

        Studio proves the caller is an operator, but deliberately uses an
        ``anon`` project context for the explored request. This keeps policy
        behavior honest: public endpoints work immediately and authenticated
        endpoints only work when the operator supplies a project-user token.
        """
        if await signed_in(ctx) is None:
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        body = await _body(ctx)
        project = str(body.get("project") or "")
        env = str(body.get("env") or "")
        version = str(body.get("version") or "v1")
        method = str(body.get("method") or "GET").upper()
        requested_path = str(body.get("path") or "/")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", project):
            return JSONResponse({"detail": "invalid project"}, status_code=400)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", env):
            return JSONResponse({"detail": "invalid environment"}, status_code=400)
        if not re.fullmatch(r"v[1-9][0-9]*", version):
            return JSONResponse({"detail": "invalid API version"}, status_code=400)
        if not await may_use_project(ctx, project):
            return JSONResponse({"detail": f"no project {project!r}"}, status_code=404)
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
                context=PlatformContext(
                    project=project, env=env, role="anon", key_id="studio-explorer"
                ),
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
        # The API checks organization access itself. These services do not
        # know organizations, so Studio confirms the project is the operator's.
        project = _bridge_project(target, segments, params)
        if target != "platform" and (project is None or project.startswith("_")):
            return JSONResponse({"detail": "name a project of yours"}, status_code=404)
        if project is not None and target != "platform" and not await may_use_project(ctx, project):
            return JSONResponse({"detail": f"no project {project!r}"}, status_code=404)
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


def _bridge_project(target: str, segments: list[str], params: dict[str, Any]) -> str | None:
    """The project a bridged call to a project-agnostic service is about."""
    if target == "auth":
        # /admin/v1/projects/<project>/envs/<env>/...
        return segments[1] if len(segments) > 3 and segments[0] == "projects" else None
    if target == "realtime":
        # /internal/v1/realtime/<project>/<env>/...
        return segments[0] if segments and segments[0] else None
    if target == "telemetry":
        value = params.get("project")
        return str(value) if value else None
    return None


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
