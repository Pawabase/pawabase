"""Inbound webhooks (``/hooks/v1/{project}/{env}/{slug}``) and public docs."""

from __future__ import annotations

import hmac
import json
from datetime import datetime, timezone
from typing import Any

from sillo import HttpContext, Router, accepted, html
from sillo.exceptions import HTTPException
from sillo.openapi import Atlas
from sillo.openapi.ui import DocsContext

from app.compiler.build import REST_PREFIX
from app.platform import Platform
from app.webhooks import verify_plain_hmac, verify_signature
from database.models import InboundHook

MAX_HOOK_BYTES = 1024 * 1024


def register(app: Any, platform: Platform) -> None:
    r = Router(prefix="/hooks/v1", tags=["webhooks"])

    @r.post("/{project}/{env}/{slug}", summary="Receive an inbound webhook")
    async def receive(ctx: HttpContext, project: str, env: str, slug: str):
        state = await platform.state(project, env)
        hook = state.inbound_hooks.get(slug)
        if hook is None or not hook.enabled:
            raise HTTPException(status_code=404, detail="Not found")
        body = await ctx.body
        if len(body) > MAX_HOOK_BYTES:
            raise HTTPException(status_code=413, detail="payload too large")
        if hook.verification != "none":
            secret = platform.box.open(hook.secret_ciphertext or "")
            provided = ctx.headers.get(hook.signature_header.lower(), "")
            if hook.verification == "hmac-sha256":
                valid = bool(provided) and verify_plain_hmac(secret, body, provided)
            elif hook.verification == "pawabase":
                valid = bool(provided) and verify_signature(secret, body, provided)
            else:
                valid = bool(provided) and hmac.compare_digest(provided, secret)
            if not valid:
                raise HTTPException(status_code=401, detail="invalid signature")
        try:
            payload: Any = json.loads(body) if body else None
        except ValueError:
            payload = {"raw": body.decode("utf-8", "replace")}
        await InboundHook.filter(id=hook.id).update(received=hook.received + 1, last_received_at=datetime.now(timezone.utc))
        headers = {k: v for k, v in ctx.headers.items() if k.lower().startswith(("x-", "user-agent", "content-type")) and k.lower() != hook.signature_header.lower()}
        envelope = {"hook": slug, "headers": headers, "body": payload}
        if hook.target_type == "flow":
            from app.jobs.flows import RunFlowJob

            job_id = await platform.dispatch(RunFlowJob, project=project, env=env, target=hook.target, source="webhook",
                                             flow=hook.target, input=envelope, trigger="webhook", auth={"authenticated": False, "kind": "webhook"})
            return accepted({"accepted": True, "job_id": job_id})
        event_id = await platform.emit(state, hook.target, envelope, actor=f"hook:{slug}")
        return accepted({"accepted": True, "event_id": event_id})

    app.mount_router(r)

    d = Router(prefix="/docs/v1", tags=["docs"], exclude_from_schema=True)

    async def public_state(project: str, env: str):
        state = await platform.state(project, env)
        if not state.settings.get("public_docs"):
            raise HTTPException(status_code=404, detail="Not found")
        return state

    @d.get("/{project}/{env}/openapi.json")
    async def public_openapi(ctx: HttpContext, project: str, env: str):
        from sillo.core.http.response import BaseResponse

        state = await public_state(project, env)
        compiled = await state.compiled()
        return BaseResponse(body=compiled.build_openapi(REST_PREFIX), content_type="application/json")

    @d.get("/{project}/{env}")
    async def public_docs(ctx: HttpContext, project: str, env: str):
        state = await public_state(project, env)
        compiled = await state.compiled()
        document = json.loads(compiled.build_openapi(REST_PREFIX))
        info = document.get("info", {})
        page = Atlas(title=f"{state.project_name} API").render(
            DocsContext(
                openapi_url=f"/docs/v1/{project}/{env}/openapi.json",
                title=info.get("title", "API"),
                version=info.get("version", ""),
                description=info.get("description", ""),
                config=compiled.openapi_config if hasattr(compiled, "openapi_config") else None,
            )
        )
        return html(page)

    app.mount_router(d)
