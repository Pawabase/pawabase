"""Studio's pages and its JSON bridge to the other services.

Pages are Inertia responses: the server resolves who is signed in and the data
a page opens with, React renders it. Everything a page does afterwards goes
through ``/studio/api/<service>/...``, which forwards to the management
endpoints of the API, Akountz or Angula with a service token naming the
operator. The browser never talks to those services and never holds a token
they would accept.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sillo import HttpContext, SilloApp
from sillo.responses import JSONResponse
from sillo.static import StaticFiles
from sillo_inertia import Inertia, back, redirect, render, set_errors

from app import operators
from app.config import StudioSettings
from pawabase_kit.clients import ServiceClient, ServiceError

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
        try:
            result = await clients[service].request(
                ctx.method, prefix + clean, json=body, params=params or None, operator=operator
            )
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


def _detail(exc: ServiceError) -> str:
    return str(exc.body.get("detail") if isinstance(exc.body, dict) else exc.body)
