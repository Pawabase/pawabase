"""Studio's pages: each is an Inertia response whose data the server loads, and React renders."""

from __future__ import annotations

from typing import Any

from sillo import HttpContext, SilloApp
from sillo.static import StaticFiles
from sillo_inertia import redirect, render

from pawabase_server.core.clients import ServiceError
from studio.support import Deps, detail_of

#: Definition sections share one generic editor, driven by the kind.
DEFINITION_KINDS = (
    "schemas",
    "transformers",
    "policies",
    "routes",
    "subscriptions",
    "webhooks",
    "inbound-hooks",
    "schedules",
)
#: Studio section → Inertia component.
SECTIONS: dict[str, str] = {
    **{kind: "Env/Definitions" for kind in DEFINITION_KINDS},
    "database": "Env/Database",
    "types": "Env/Types",
    "tables": "Env/Tables",
    "topology": "Env/Topology",
    "flows": "Env/Flows",
    "functions": "Env/Functions",
    "storage": "Env/Storage",
    "users": "Env/Users",
    "mail": "Env/Mail",
    "status": "Env/Status",
    "networking": "Env/Networking",
    "keys": "Env/Keys",
    "secrets": "Env/Secrets",
    "backups": "Env/Backups",
    "jobs": "Env/Jobs",
    "events": "Env/Events",
    "webhook-deliveries": "Env/WebhookDeliveries",
    "realtime": "Env/Realtime",
    "dashboards": "Env/Dashboards",
    "observability": "Env/Observability",
    "activity": "Env/Observability",
    "usage": "Env/Usage",
    "explorer": "Env/Explorer",
    "settings": "Env/Settings",
}
ASSET_CACHE = "public, max-age=31536000, immutable"


async def unavailable(exc: ServiceError) -> Any:
    """The page shown when a service Studio depends on cannot answer."""
    return await render(
        "Errors/Unavailable", {"message": detail_of(exc), "service": exc.service}, status_code=502
    )


async def page(component: str, loader) -> Any:
    """Render *component* with what *loader* fetches; a missing thing is a 404 page, a failing service a 502 page."""
    try:
        props = await loader()
    except ServiceError as exc:
        if exc.status == 404:
            return await render("Errors/NotFound", {"message": detail_of(exc)}, status_code=404)
        return await unavailable(exc)
    return await render(component, props)


def default_environment(environments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The environment Studio opens into: the one marked default, else the first, else none."""
    return next((e for e in environments if e.get("is_default")), None) or (
        environments[0] if environments else None
    )


def register(app: SilloApp, deps: Deps) -> None:
    deps.inertia.share(
        runtime_name=deps.settings.project_name, gateway_url=deps.settings.public_gateway_url
    )

    @app.get("/", exclude_from_schema=True)
    async def home(ctx: HttpContext):
        """Open straight into the runtime's default environment."""
        try:
            runtime = await deps.runtime(ctx)
        except ServiceError as exc:
            return await unavailable(exc)
        chosen = default_environment(runtime["environments"])
        return redirect(f"/envs/{chosen['name']}" if chosen else "/environments")

    @app.get("/environments", exclude_from_schema=True)
    async def environments_page(ctx: HttpContext):
        return await page("Environments", lambda: deps.env_props(ctx))

    @app.get("/audit", exclude_from_schema=True)
    async def audit(ctx: HttpContext):
        async def load():
            entries = await deps.call(ctx, "GET", "/audit", params={"limit": 200})
            return {**await deps.env_props(ctx), "entries": entries.get("data", [])}

        return await page("Audit", load)

    @app.get("/envs/{env}", exclude_from_schema=True)
    async def env_page(ctx: HttpContext, env: str):
        async def load():
            overview = await deps.call(ctx, "GET", f"/envs/{env}/overview")
            return {
                **await deps.env_props(ctx),
                "env": env,
                "section": "overview",
                "overview": overview,
            }

        return await page("Env/Overview", load)

    @app.get("/envs/{env}/flows/{name}", exclude_from_schema=True)
    async def flow_editor(ctx: HttpContext, env: str, name: str):
        async def load():
            props = await deps.env_props(ctx)
            blocks = await deps.call(ctx, "GET", "/blocks")
            flow = (
                None if name == "new" else await deps.call(ctx, "GET", f"/envs/{env}/flows/{name}")
            )
            return {
                **props,
                "env": env,
                "section": "flows",
                "flow": flow,
                "blocks": blocks.get("data", blocks),
            }

        return await page("Flows/Editor", load)

    @app.get("/envs/{env}/{section}", exclude_from_schema=True)
    async def section_page(ctx: HttpContext, env: str, section: str):
        component = SECTIONS.get(section)

        async def load():
            if component is None:
                raise ServiceError(404, {"detail": f"no section {section!r}"}, service="studio")
            props = await deps.env_props(ctx)
            return {
                **props,
                "env": env,
                "section": section,
                "kind": section if section in DEFINITION_KINDS else None,
            }

        return await page(component or "Errors/NotFound", load)

    static = StaticFiles(directory=deps.frontend / "dist", cache_control=ASSET_CACHE)

    @app.get("/assets/{path:path}", exclude_from_schema=True)
    async def assets(ctx: HttpContext, path: str):
        return await static._handle(ctx)
