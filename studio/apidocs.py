"""An environment's API reference, aimed at the gateway.

The public page at ``<gateway>/docs/v1/<env>`` exists only while the environment sets ``public_docs``. Studio shows the same document
whatever that setting says, with "try it" aimed at the gateway.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

from sillo import HttpContext, SilloApp, html
from sillo.openapi.ui import ATLAS_JS
from sillo.responses import JSONResponse

from pawabase_server.core.clients import ServiceError
from studio.support import Deps, error_response

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>html, body {{ margin: 0; padding: 0; height: 100%; }}</style>
</head>
<body>
    <div id="app"></div>
    <script src="{script}"></script>
    <script>Atlas.createApiReference('#app', {{ theme: 'auto', spec: {spec} }});</script>
</body>
</html>"""


def embed(document: dict[str, Any]) -> str:
    """The spec as a JavaScript literal that cannot close its own ``<script>`` element or break out of it."""
    return (
        json.dumps(document)
        .replace("</", "<\\/")
        .replace("<!--", "<\\!--")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def register(app: SilloApp, deps: Deps) -> None:
    async def document_for(ctx: HttpContext, env: str) -> dict[str, Any]:
        document = await deps.call(ctx, "GET", f"/envs/{env}/openapi")
        document["servers"] = [
            {"url": deps.settings.public_gateway_url, "description": f"Gateway · {env}"}
        ]
        return document

    @app.get("/envs/{env}/api-docs/openapi.json", exclude_from_schema=True)
    async def spec(ctx: HttpContext, env: str):
        try:
            return JSONResponse(await document_for(ctx, env))
        except ServiceError as exc:
            return error_response(exc)

    @app.get("/envs/{env}/api-docs", exclude_from_schema=True)
    async def reference(ctx: HttpContext, env: str):
        try:
            document = await document_for(ctx, env)
        except ServiceError as exc:
            return error_response(exc)
        # The spec is embedded rather than fetched by URL: Atlas offers the page's own origin ("This server", i.e. Studio) as the default
        # target whenever the spec came from that origin, which would aim every "try it" request at Studio instead of the gateway.
        title = escape(f"{document.get('info', {}).get('title', 'API')} · {env}")
        return html(PAGE.format(title=title, script=ATLAS_JS, spec=embed(document)))
