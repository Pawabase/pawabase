"""Types for the data API: TypeScript and Python, generated from the environment's resources."""

from __future__ import annotations

from sillo import HttpContext, Router
from sillo.core.http.response import BaseResponse
from sillo.exceptions import HTTPException

from pawabase_server.compiler import typegen
from pawabase_server.platform import Platform
from pawabase_server.routes.common import MANAGE, get_environment

FILENAMES = {"typescript": "pawabase.types.ts", "python": "pawabase_types.py"}


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    @r.get(
        f"{base}/types",
        auth=MANAGE,
        tags=["resources"],
        summary="Types for the environment's resources, to paste or save into a project",
    )
    async def types(ctx: HttpContext, env: str):
        """``?lang=typescript`` (default) or ``python``; ``?name=`` the root interface (TypeScript); ``?download=1`` as a file.

        Reachable with the environment's secret key through the gateway, like the rest of the management plane, so CI can
        refresh a project's types: ``curl -H "apikey: $KEY" "$URL/platform/v1/envs/$ENV/types?lang=python"``.
        """
        await get_environment(env)
        lang = ctx.query_params.get("lang", "typescript")
        if lang not in typegen.LANGUAGES:
            raise HTTPException(
                status_code=400, detail=f"lang is one of {', '.join(typegen.LANGUAGES)}"
            )
        state = await platform.state(env)
        source = f"environment {env}"
        if lang == "python":
            text = typegen.python(state, source=source)
        else:
            text = typegen.typescript(
                state, name=ctx.query_params.get("name") or "Database", source=source
            )
        headers = {}
        if ctx.query_params.get("download"):
            headers["Content-Disposition"] = f'attachment; filename="{FILENAMES[lang]}"'
        return BaseResponse(
            body=text.encode(), content_type="text/plain; charset=utf-8", headers=headers
        )
