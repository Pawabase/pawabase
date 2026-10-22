"""Mail in Studio: setup, sent mail, composing, previews, template publishing and the shared brand kit.

Templates themselves are definitions (``/envs/{env}/mail-templates``, see ``definitions.py``), so branches, releases and
blueprints carry them like everything else. This module adds what a template's life needs on top: publishing a draft
as a numbered version, restoring an older version, renaming, and where each template is used.
"""

from __future__ import annotations

import base64
import copy
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from jinja2 import TemplateError
from pydantic import BaseModel, Field
from sillo import HttpContext, Router
from sillo.exceptions import HTTPException

from pawabase_server.db.models import Flow, MailKit, MailLog, MailTemplate, MailTemplateVersion
from pawabase_server.platform import Platform
from pawabase_server.routes.common import (
    MANAGE,
    NAME_PATTERN,
    audit,
    changed,
    dump,
    get_environment,
    page_params,
)

#: The heavy columns a list of sent mail leaves out; one message's detail includes them.
HEAVY = ("html", "text", "attachments", "data")
#: Attachments a message may carry, in total, before base64.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class Attachment(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = ""
    #: base64 of the file's bytes.
    content: str


class SendBody(BaseModel):
    to: list[str] = Field(min_length=1)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    html: str | None = None
    text: str | None = None
    template: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    from_email: str | None = None
    reply_to: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    #: ISO time to send at; omitted (or in the past) sends now.
    scheduled_at: datetime | None = None


class MailTestBody(BaseModel):
    to: list[str]
    subject: str = "Pawabase test message"
    text: str = "This is a test message sent from Pawabase Studio."
    html: str | None = None
    template: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    from_email: str | None = None


class RenderBody(BaseModel):
    subject: str = ""
    html: str = ""
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ConnectionBody(BaseModel):
    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    #: A saved provider's password: the name of the secret holding it.
    password_secret: str | None = None
    use_ssl: bool | None = None
    use_tls: bool | None = None


class PublishBody(BaseModel):
    note: str = Field(default="", max_length=255)


class RenameBody(BaseModel):
    to: str = Field(pattern=NAME_PATTERN)


class KitBody(BaseModel):
    brand: dict[str, Any] = Field(default_factory=dict)
    palettes: list[dict[str, Any]] = Field(default_factory=list)
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    folders: list[str] = Field(default_factory=list)


def _summary(row: MailLog) -> dict[str, Any]:
    data = dump(row, exclude=HEAVY)
    data["attachments"] = [
        {
            "filename": a.get("filename"),
            "content_type": a.get("content_type"),
            "size": a.get("size"),
        }
        for a in row.attachments or []
    ]
    return data


def _mentions(value: Any, name: str) -> bool:
    """Whether a flow definition sends the template *name* (a node whose ``template`` is it)."""
    if isinstance(value, dict):
        if value.get("template") == name:
            return True
        return any(_mentions(item, name) for item in value.values())
    if isinstance(value, list):
        return any(_mentions(item, name) for item in value)
    return False


def _attachments(items: list[Attachment]) -> list[dict[str, Any]]:
    total = 0
    out = []
    for item in items:
        try:
            raw = base64.b64decode(item.content, validate=True)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"attachment {item.filename!r} is not base64"
            ) from exc
        total += len(raw)
        out.append(
            {
                "filename": item.filename,
                "content_type": item.content_type or "application/octet-stream",
                "size": len(raw),
                "content": item.content,
            }
        )
    if total > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="attachments may total at most 10 MB")
    return out


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    # ── setup ────────────────────────────────────────────────────────────

    @r.get(
        f"{base}/mail/config",
        auth=MANAGE,
        tags=["mail"],
        summary="The mail setup this environment has, and which variable each part comes from",
    )
    async def mail_config(ctx: HttpContext, env: str):
        await get_environment(env)
        return platform.mail.describe(await platform.state(env))

    @r.post(
        f"{base}/mail/connection",
        auth=MANAGE,
        tags=["mail"],
        request_model=ConnectionBody,
        summary="Connect and sign in to an SMTP server without sending",
    )
    async def mail_connection(ctx: HttpContext, env: str, body: ConnectionBody):
        await get_environment(env)
        try:
            return await platform.mail.test_connection(
                await platform.state(env), body.model_dump(exclude_none=True)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ── sent mail ────────────────────────────────────────────────────────

    @r.get(
        f"{base}/mail/log",
        auth=MANAGE,
        tags=["mail"],
        summary="Messages sent, scheduled or suppressed",
    )
    async def mail_log(ctx: HttpContext, env: str):
        limit, offset = page_params(ctx, default=100)
        query = MailLog.filter(env=env)
        params = ctx.query_params
        if params.get("status") and params["status"] != "all":
            query = query.filter(status=params["status"])
        if params.get("template"):
            query = query.filter(template=params["template"])
        if params.get("q"):
            needle = params["q"]
            # Recipients are a JSON list: search its text form along with the subject.
            rows = await query.order_by("-id").limit(2000)
            needle_lower = needle.lower()
            rows = [
                row
                for row in rows
                if needle_lower in (row.subject or "").lower()
                or any(needle_lower in str(addr).lower() for addr in row.to or [])
            ]
            return {"data": [_summary(row) for row in rows[offset : offset + limit]]}
        rows = await query.order_by("-id").offset(offset).limit(limit)
        return {"data": [_summary(row) for row in rows]}

    @r.get(
        f"{base}/mail/log/{{id}}",
        auth=MANAGE,
        tags=["mail"],
        summary="One message, with what it carried",
    )
    async def mail_message(ctx: HttpContext, env: str, id: str):
        row = await MailLog.get_or_none(env=env, id=id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such message")
        data = dump(row)
        data["attachments"] = [
            {k: v for k, v in a.items() if k != "content"} for a in row.attachments or []
        ]
        data["retries"] = [
            _summary(r) for r in await MailLog.filter(env=env, retry_of=row.id).order_by("id")
        ]
        return data

    @r.post(
        f"{base}/mail/log/{{id}}/retry",
        auth=MANAGE,
        tags=["mail"],
        summary="Send a logged message again",
    )
    async def mail_retry(ctx: HttpContext, env: str, id: str):
        row = await MailLog.get_or_none(env=env, id=id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such message")
        if row.html is None and row.text is None:
            raise HTTPException(
                status_code=409,
                detail="this message was logged before Studio kept message content, so it cannot be resent",
            )
        try:
            return await platform.mail.retry(await platform.state(env), row)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    @r.post(
        f"{base}/mail/log/{{id}}/cancel",
        auth=MANAGE,
        tags=["mail"],
        summary="Cancel a scheduled message",
    )
    async def mail_cancel(ctx: HttpContext, env: str, id: str):
        if not await MailLog.filter(env=env, id=id, status="scheduled").update(status="canceled"):
            raise HTTPException(
                status_code=409, detail="only a message still waiting to be sent can be canceled"
            )
        return {"canceled": True}

    @r.get(
        f"{base}/mail/stats",
        auth=MANAGE,
        tags=["mail"],
        summary="Totals, a daily series and recent errors",
    )
    async def mail_stats(ctx: HttpContext, env: str):
        await get_environment(env)
        try:
            days = max(1, min(int(ctx.query_params.get("days", 30)), 365))
        except ValueError:
            days = 30
        since = datetime.now(UTC) - timedelta(days=days)
        rows = await MailLog.filter(env=env, created_at__gte=since).only(
            "id", "status", "template", "created_at", "error", "subject", "to"
        )
        totals = Counter(row.status for row in rows)
        series: dict[str, Counter] = {}
        for offset in range(days - 1, -1, -1):
            series[(datetime.now(UTC) - timedelta(days=offset)).date().isoformat()] = Counter()
        for row in rows:
            key = row.created_at.date().isoformat()
            if key in series:
                series[key][row.status] += 1
        templates = Counter(row.template for row in rows if row.template)
        errors = sorted(
            (row for row in rows if row.status == "failed"), key=lambda row: row.id, reverse=True
        )[:8]
        return {
            "days": days,
            "total": len(rows),
            "by_status": dict(totals),
            "series": [
                {
                    "day": day,
                    "sent": c["sent"],
                    "failed": c["failed"],
                    "suppressed": c["suppressed"],
                }
                for day, c in series.items()
            ],
            "top_templates": [
                {"template": name, "count": count} for name, count in templates.most_common(6)
            ],
            "errors": [
                {
                    "id": row.id,
                    "subject": row.subject,
                    "to": row.to,
                    "error": row.error,
                    "created_at": row.created_at,
                }
                for row in errors
            ],
            "scheduled": await MailLog.filter(env=env, status="scheduled").count(),
        }

    # ── sending ──────────────────────────────────────────────────────────

    async def _send(env: str, body: SendBody | MailTestBody, source: str, **extra):
        state = await platform.state(env)
        try:
            return await platform.mail.send(
                state,
                body.to,
                body.subject,
                text=body.text,
                html=body.html,
                template=body.template,
                data=body.data,
                source=source,
                from_email=body.from_email,
                **extra,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except TemplateError as exc:
            raise HTTPException(status_code=422, detail=f"template: {exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    @r.post(
        f"{base}/mail/test",
        auth=MANAGE,
        tags=["mail"],
        request_model=MailTestBody,
        summary="Send a message now",
    )
    async def mail_test(ctx: HttpContext, env: str, body: MailTestBody):
        return await _send(env, body, "studio")

    @r.post(
        f"{base}/mail/send",
        auth=MANAGE,
        tags=["mail"],
        request_model=SendBody,
        summary="Compose and send a message, now or at a set time",
    )
    async def mail_send(ctx: HttpContext, env: str, body: SendBody):
        scheduled = body.scheduled_at
        if scheduled is not None and scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=UTC)
        return await _send(
            env,
            body,
            "composer",
            reply_to=body.reply_to,
            cc=body.cc,
            bcc=body.bcc,
            attachments=_attachments(body.attachments),
            scheduled_at=scheduled,
        )

    @r.post(
        f"{base}/mail/render",
        auth=MANAGE,
        tags=["mail"],
        request_model=RenderBody,
        summary="Render a subject and bodies with sample data, as sending would",
    )
    async def mail_render(ctx: HttpContext, env: str, body: RenderBody):
        await get_environment(env)
        try:
            subject, html, text = platform.mail.render_source(
                body.subject, body.html, body.text, body.data
            )
        except TemplateError as exc:
            raise HTTPException(status_code=422, detail=f"{type(exc).__name__}: {exc}") from exc
        return {"subject": subject, "html": html or "", "text": text or ""}

    # ── templates: publishing, versions, renaming, usage ────────────────

    async def _template(env: str, name: str) -> tuple[Any, MailTemplate]:
        environment = await get_environment(env)
        item = await MailTemplate.get_or_none(environment=environment, name=name)
        if item is None:
            raise HTTPException(status_code=404, detail=f"no mail template {name!r}")
        return environment, item

    @r.post(
        f"{base}/mail-templates/{{name}}/publish",
        auth=MANAGE,
        tags=["mail"],
        request_model=PublishBody,
        summary="Publish a template's draft as a new version",
    )
    async def publish(ctx: HttpContext, env: str, name: str, body: PublishBody):
        environment, item = await _template(env, name)
        draft = item.draft or {}
        if draft:
            for field in ("subject", "preheader", "html", "text", "design"):
                if field in draft:
                    setattr(item, field, draft[field])
        try:
            platform.mail.render_source(item.subject, item.html, item.text, {})
        except TemplateError:
            pass  # undefined values are fine without data; syntax was checked when the draft was saved
        if not (item.html or item.text):
            raise HTTPException(
                status_code=422, detail="a template needs a body before it can be published"
            )
        last = await MailTemplateVersion.filter(env=env, template=name).order_by("-number").first()
        number = (last.number if last else 0) + 1
        await MailTemplateVersion.create(
            env=env,
            template=name,
            number=number,
            subject=item.subject,
            preheader=item.preheader,
            html=item.html,
            text=item.text,
            design=copy.deepcopy(item.design),
            note=body.note,
        )
        item.draft = None
        item.status = "published"
        item.published_at = datetime.now(UTC)
        await item.save()
        await changed(ctx, environment, "mail-templates.published", name, {"version": number})
        return {**dump(item), "version": number}

    @r.get(
        f"{base}/mail-templates/{{name}}/versions",
        auth=MANAGE,
        tags=["mail"],
        summary="Published versions of a template, newest first",
    )
    async def versions(ctx: HttpContext, env: str, name: str):
        await _template(env, name)
        rows = await MailTemplateVersion.filter(env=env, template=name).order_by("-number")
        return {
            "data": [
                dump(row, exclude=("design",)) | {"has_design": row.design is not None}
                for row in rows
            ]
        }

    @r.get(
        f"{base}/mail-templates/{{name}}/versions/{{number}}",
        auth=MANAGE,
        tags=["mail"],
        summary="One published version",
    )
    async def version(ctx: HttpContext, env: str, name: str, number: int):
        row = await MailTemplateVersion.get_or_none(env=env, template=name, number=number)
        if row is None:
            raise HTTPException(status_code=404, detail="no such version")
        return dump(row)

    @r.post(
        f"{base}/mail-templates/{{name}}/versions/{{number}}/restore",
        auth=MANAGE,
        tags=["mail"],
        summary="Put an older version back as the draft",
    )
    async def restore(ctx: HttpContext, env: str, name: str, number: int):
        environment, item = await _template(env, name)
        row = await MailTemplateVersion.get_or_none(env=env, template=name, number=number)
        if row is None:
            raise HTTPException(status_code=404, detail="no such version")
        item.draft = {
            "subject": row.subject,
            "preheader": row.preheader,
            "html": row.html,
            "text": row.text,
            "design": copy.deepcopy(row.design),
            "saved_at": datetime.now(UTC).isoformat(),
            "restored_from": number,
        }
        await item.save()
        await changed(ctx, environment, "mail-templates.restored", name, {"version": number})
        return dump(item)

    @r.post(
        f"{base}/mail-templates/{{name}}/rename",
        auth=MANAGE,
        tags=["mail"],
        request_model=RenameBody,
        summary="Rename a template, keeping its versions",
    )
    async def rename(ctx: HttpContext, env: str, name: str, body: RenameBody):
        environment, item = await _template(env, name)
        if body.to == name:
            return dump(item)
        if await MailTemplate.filter(environment=environment, name=body.to).exists():
            raise HTTPException(
                status_code=409, detail=f"a template named {body.to!r} already exists"
            )
        item.name = body.to
        await item.save()
        await MailTemplateVersion.filter(env=env, template=name).update(template=body.to)
        await changed(ctx, environment, "mail-templates.renamed", body.to, {"from": name})
        flows = [
            flow.name
            for flow in await Flow.filter(environment=environment)
            if _mentions(flow.definition, name)
        ]
        return {**dump(item), "flows_to_update": flows}

    @r.get(
        f"{base}/mail/templates/usage",
        auth=MANAGE,
        tags=["mail"],
        summary="How often each template was sent, and which flows send it",
    )
    async def usage(ctx: HttpContext, env: str):
        environment = await get_environment(env)
        templates = await MailTemplate.filter(environment=environment).only("id", "name")
        flows = await Flow.filter(environment=environment).only("id", "name", "definition")
        since = datetime.now(UTC) - timedelta(days=30)
        rows = (
            await MailLog.filter(env=env, template__not_isnull=True)
            .only("id", "template", "status", "created_at")
            .order_by("-id")
            .limit(20000)
        )
        out: dict[str, dict[str, Any]] = {
            t.name: {"sent": 0, "failed": 0, "last_30_days": 0, "last_sent_at": None, "flows": []}
            for t in templates
        }
        for row in rows:
            entry = out.get(row.template)
            if entry is None:
                continue
            if row.status in ("sent", "suppressed"):
                entry["sent"] += 1
            elif row.status == "failed":
                entry["failed"] += 1
            if row.created_at >= since:
                entry["last_30_days"] += 1
            if entry["last_sent_at"] is None:
                entry["last_sent_at"] = row.created_at
        for flow in flows:
            for name, entry in out.items():
                if _mentions(flow.definition, name):
                    entry["flows"].append(flow.name)
        return {"data": out}

    # ── brand kit ────────────────────────────────────────────────────────

    @r.get(
        f"{base}/mail/kit",
        auth=MANAGE,
        tags=["mail"],
        summary="Brand styles, palettes, reusable blocks and folders",
    )
    async def kit(ctx: HttpContext, env: str):
        await get_environment(env)
        row = await MailKit.get_or_none(env=env)
        return (
            dump(row)
            if row
            else {"env": env, "brand": {}, "palettes": [], "blocks": [], "folders": []}
        )

    @r.put(
        f"{base}/mail/kit",
        auth=MANAGE,
        tags=["mail"],
        request_model=KitBody,
        summary="Save brand styles, palettes, reusable blocks and folders",
    )
    async def save_kit(ctx: HttpContext, env: str, body: KitBody):
        environment = await get_environment(env)
        row = await MailKit.get_or_none(env=env)
        if row is None:
            row = await MailKit.create(env=env, **body.model_dump())
        else:
            for field, value in body.model_dump().items():
                setattr(row, field, value)
            await row.save()
        await audit(ctx, "mail.kit.updated", env=environment.name, target=env)
        return dump(row)
