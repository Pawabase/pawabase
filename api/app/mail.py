"""Mail for every environment, through Sillo's mail client.

Each environment configures its own SMTP service (``infra.mail``). Without
one, messages are suppressed (Sillo's ``suppress_send``) and still logged, so
development works with no mail server and Studio shows what would have gone out.
Stored templates render in Jinja2's sandbox: templates are written in Studio
and must not reach Python internals.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from jinja2.sandbox import SandboxedEnvironment
from sillo.mail import MailClient, MailConfig

from database.models import MailLog
from pawabase_core.telemetry import span

if TYPE_CHECKING:
    from app.platform import Platform
    from app.state import EnvironmentState

_jinja = SandboxedEnvironment(autoescape=True)


class MailManager:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._clients: dict[tuple[str, str, str], MailClient] = {}

    def _config(self, state: EnvironmentState) -> MailConfig:
        raw = {
            key: self.platform.resolve_value(state, value)
            for key, value in (state.infra.get("mail") or {}).items()
        }
        configured = bool(raw.get("host"))
        port = int(raw.get("port") or 587)
        use_ssl = bool(raw.get("use_ssl", port == 465))
        return MailConfig(
            smtp_host=raw.get("host") or "localhost",
            smtp_port=port,
            smtp_username=raw.get("username") or None,
            smtp_password=raw.get("password") or None,
            use_ssl=use_ssl,
            use_tls=bool(raw.get("use_tls", not use_ssl and port == 587)),
            default_from=raw.get("from") or "no-reply@pawabase.local",
            default_reply_to=raw.get("reply_to") or None,
            suppress_send=not configured or bool(raw.get("suppress")),
            template_directory=None,
        )

    def client(self, state: EnvironmentState) -> MailClient:
        config = self._config(state)
        key = (state.env_name, repr(sorted(vars(config).items())))
        client = self._clients.get(key)
        if client is None:
            client = self._clients[key] = MailClient(config)
        return client

    def render(
        self, state: EnvironmentState, template: str, data: Mapping[str, Any]
    ) -> tuple[str, str | None, str | None]:
        stored = state.mail_templates.get(template)
        if stored is None:
            raise KeyError(f"no mail template {template!r}")
        subject = _jinja.from_string(stored.subject or "").render(**data)
        html = _jinja.from_string(stored.html).render(**data) if stored.html else None
        text = _jinja.from_string(stored.text).render(**data) if stored.text else None
        return subject, html, text

    async def send(
        self,
        state: EnvironmentState,
        to: list[str],
        subject: str = "",
        *,
        text: str | None = None,
        html: str | None = None,
        template: str | None = None,
        data: Mapping[str, Any] | None = None,
        source: str = "api",
    ) -> dict[str, Any]:
        if not to:
            raise ValueError("a message needs at least one recipient")
        if template:
            rendered_subject, rendered_html, rendered_text = self.render(
                state, template, data or {}
            )
            subject = subject or rendered_subject
            html = html or rendered_html
            text = text or rendered_text
        with span("mail", f"send {template or subject}"[:200], recipients=len(to)):
            result = await self.client(state).send_email(
                to=to, subject=subject, body=text or "", html_body=html
            )
        suppressed = bool((result.provider_response or {}).get("suppressed"))
        await MailLog.create(
            env=state.env_name,
            to=to,
            subject=subject,
            template=template,
            status="suppressed" if suppressed else ("sent" if result.success else "failed"),
            message_id=result.message_id,
            error=result.error,
            source=source,
        )
        if not result.success:
            raise RuntimeError(result.error or "mail delivery failed")
        return {"message_id": result.message_id, "suppressed": suppressed, "to": to}

    async def close(self) -> None:
        for client in self._clients.values():
            try:
                await client.stop()
            except Exception:
                pass
        self._clients.clear()
