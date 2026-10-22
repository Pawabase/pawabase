"""Mail for every environment, through Sillo's mail client.

Each environment brings its own provider: the SMTP service saved in Studio (``infra.mail``). An environment that has not saved one falls
back to ``<ENV>_MAIL_*`` or ``PAWABASE_MAIL_*`` variables, for deployments that prefer them. Without a host, messages are suppressed (Sillo's ``suppress_send``) and still logged, so
development works with no mail server and Studio shows what would have gone out.
Stored templates render in Jinja2's sandbox: templates are written in Studio
and must not reach Python internals.
"""

from __future__ import annotations

import base64
import smtplib
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jinja2.sandbox import SandboxedEnvironment
from sillo.helpers.concurrency import run_in_threadpool
from sillo.mail import MailClient, MailConfig

from pawabase_server.core import envvars
from pawabase_server.core.telemetry import span
from pawabase_server.db.models import MailLog

if TYPE_CHECKING:
    from pawabase_server.environments.state import EnvironmentState
    from pawabase_server.platform import Platform

_jinja = SandboxedEnvironment(autoescape=True)


class MailManager:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self._clients: dict[tuple[str, str, str], MailClient] = {}

    def _text(self, state: EnvironmentState, key: str, fallback: str) -> str:
        """``<ENV>_<KEY>`` from the process environment (secret references resolved), else *fallback*."""
        return str(self.platform.env_setting(state, key, None) or fallback or "")

    def _stored(self, state: EnvironmentState) -> dict[str, Any]:
        """The provider saved on the environment in Studio, with secret references resolved; empty when none."""
        raw = {
            key: self.platform.resolve_value(state, value)
            for key, value in (state.infra.get("mail") or {}).items()
        }
        return raw if raw.get("host") else {}

    def _config(self, state: EnvironmentState) -> MailConfig:
        """The environment's SMTP service: the provider saved in Studio, else variables.

        A host saved on the environment wins. Without one, ``<ENV>_MAIL_*`` beats ``PAWABASE_MAIL_*``.
        """
        stored = self._stored(state)
        if stored:
            port = int(stored.get("port") or 587)
            use_ssl = bool(stored.get("use_ssl", port == 465))
            return MailConfig(
                smtp_host=stored["host"],
                smtp_port=port,
                smtp_username=stored.get("username") or None,
                smtp_password=stored.get("password") or None,
                use_ssl=use_ssl,
                use_tls=bool(stored.get("use_tls", not use_ssl and port == 587)),
                default_from=stored.get("from") or "no-reply@pawabase.local",
                default_reply_to=stored.get("reply_to") or None,
                suppress_send=bool(stored.get("suppress")),
                template_directory=None,
            )
        settings = self.platform.settings
        name = state.env_name
        host = self._text(state, "MAIL_HOST", settings.mail_host)
        port = envvars.integer(name, "MAIL_PORT", settings.mail_port or 587)
        ssl_default = settings.mail_use_ssl if settings.mail_use_ssl is not None else port == 465
        use_ssl = envvars.boolean(name, "MAIL_USE_SSL", ssl_default)
        tls_default = (
            settings.mail_use_tls
            if settings.mail_use_tls is not None
            else not use_ssl and port == 587
        )
        return MailConfig(
            smtp_host=host or "localhost",
            smtp_port=port,
            smtp_username=self._text(state, "MAIL_USERNAME", settings.mail_username) or None,
            smtp_password=self._text(state, "MAIL_PASSWORD", settings.mail_password) or None,
            use_ssl=use_ssl,
            use_tls=envvars.boolean(name, "MAIL_USE_TLS", tls_default),
            default_from=self._text(state, "MAIL_FROM", settings.mail_from)
            or "no-reply@pawabase.local",
            default_reply_to=self._text(state, "MAIL_REPLY_TO", settings.mail_reply_to) or None,
            suppress_send=not host
            or envvars.boolean(name, "MAIL_SUPPRESS", settings.mail_suppress),
            template_directory=None,
        )

    #: Setting, its variable key, and the settings attribute that holds the deployment-wide value.
    FIELDS = (
        ("host", "MAIL_HOST", "mail_host"),
        ("port", "MAIL_PORT", "mail_port"),
        ("username", "MAIL_USERNAME", "mail_username"),
        ("password", "MAIL_PASSWORD", "mail_password"),
        ("use_ssl", "MAIL_USE_SSL", "mail_use_ssl"),
        ("use_tls", "MAIL_USE_TLS", "mail_use_tls"),
        ("from", "MAIL_FROM", "mail_from"),
        ("reply_to", "MAIL_REPLY_TO", "mail_reply_to"),
        ("suppress", "MAIL_SUPPRESS", "mail_suppress"),
    )

    def describe(self, state: EnvironmentState) -> dict[str, Any]:
        """The mail setup an environment actually has, and which variable each part comes from.

        Never contains the password: only whether one is set.
        """
        config = self._config(state)
        settings = self.platform.settings
        prefix = envvars.prefix_for(state.env_name)
        stored = self._stored(state)
        host = stored.get("host") or self._text(state, "MAIL_HOST", settings.mail_host)
        effective = {
            "host": host,
            "port": config.smtp_port,
            "username": config.smtp_username or "",
            "password": bool(config.smtp_password),
            "use_ssl": config.use_ssl,
            "use_tls": config.use_tls,
            "from": config.default_from,
            "reply_to": config.default_reply_to or "",
            "suppress": config.suppress_send and bool(stored.get("suppress"))
            if stored
            else envvars.boolean(state.env_name, "MAIL_SUPPRESS", settings.mail_suppress),
        }
        rows = []
        for name, key, attribute in self.FIELDS:
            default = type(settings).model_fields[attribute].default
            if stored:
                source = "saved"
            elif envvars.get(state.env_name, key) is not None:
                source = "environment"
            elif getattr(settings, attribute) != default:
                source = "deployment"
            else:
                source = "default"
            rows.append(
                {
                    "setting": name,
                    "value": effective[name],
                    "source": source,
                    "variable": f"{prefix}_{key}",
                    "global_variable": f"PAWABASE_{key}",
                }
            )
        return {
            "configured": bool(effective["host"]),
            "suppressed": config.suppress_send,
            "settings": rows,
            "source": "saved" if stored else ("variables" if host else "none"),
            "environment_prefix": f"{prefix}_MAIL_",
        }

    def client(self, state: EnvironmentState) -> MailClient:
        config = self._config(state)
        key = (state.env_name, repr(sorted(vars(config).items())))
        client = self._clients.get(key)
        if client is None:
            client = self._clients[key] = MailClient(config)
        return client

    @staticmethod
    def render_source(
        subject: str, html: str | None, text: str | None, data: Mapping[str, Any]
    ) -> tuple[str, str | None, str | None]:
        """Render a subject and bodies in the sandbox, for a stored template or a draft being previewed."""
        return (
            _jinja.from_string(subject or "").render(**data),
            _jinja.from_string(html).render(**data) if html else None,
            _jinja.from_string(text).render(**data) if text else None,
        )

    def render(
        self, state: EnvironmentState, template: str, data: Mapping[str, Any]
    ) -> tuple[str, str | None, str | None]:
        stored = state.mail_templates.get(template)
        if stored is None:
            raise KeyError(f"no mail template {template!r}")
        if getattr(stored, "status", "published") == "draft" and not (stored.html or stored.text):
            raise KeyError(f"mail template {template!r} has not been published yet")
        return self.render_source(stored.subject, stored.html, stored.text, data)

    async def _deliver(
        self,
        state: EnvironmentState,
        *,
        to: list[str],
        subject: str,
        html: str | None,
        text: str | None,
        from_email: str | None = None,
        reply_to: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        label: str = "",
    ):
        files = [
            {
                "filename": item["filename"],
                "content": base64.b64decode(item.get("content") or ""),
                "content_type": item.get("content_type") or None,
            }
            for item in attachments or []
        ]
        with span("mail", f"send {label or subject}"[:200], recipients=len(to)):
            return await self.client(state).send_email(
                to=to,
                subject=subject,
                body=text or "",
                html_body=html,
                from_email=from_email or None,
                reply_to=reply_to or None,
                cc=cc or None,
                bcc=bcc or None,
                attachments=files or None,
            )

    @staticmethod
    def _status(result) -> str:
        if bool((result.provider_response or {}).get("suppressed")):
            return "suppressed"
        return "sent" if result.success else "failed"

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
        from_email: str | None = None,
        reply_to: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
        scheduled_at: datetime | None = None,
        retry_of: str | None = None,
    ) -> dict[str, Any]:
        """Send a message now, or store it to send at *scheduled_at*.

        A template is rendered here, so a scheduled message goes out exactly as it was rendered when it was scheduled. Every
        message is logged with what it carried, so Studio can show it again, retry it or cancel it before it is sent.
        """
        if not to:
            raise ValueError("a message needs at least one recipient")
        if template:
            rendered_subject, rendered_html, rendered_text = self.render(
                state, template, data or {}
            )
            subject = subject or rendered_subject
            html = html or rendered_html
            text = text or rendered_text
        stored = {
            "env": state.env_name,
            "to": to,
            "subject": subject,
            "template": template,
            "source": source,
            "from_email": from_email or None,
            "reply_to": reply_to or None,
            "cc": cc or None,
            "bcc": bcc or None,
            "html": html,
            "text": text,
            "data": dict(data) if data else None,
            "attachments": list(attachments) if attachments else None,
            "retry_of": retry_of,
        }
        if scheduled_at is not None and scheduled_at > datetime.now(UTC):
            row = await MailLog.create(
                **stored, status="scheduled", scheduled_at=scheduled_at, attempts=0
            )
            return {
                "id": row.id,
                "scheduled": True,
                "scheduled_at": scheduled_at.isoformat(),
                "to": to,
            }
        result = await self._deliver(
            state,
            to=to,
            subject=subject,
            html=html,
            text=text,
            from_email=from_email,
            reply_to=reply_to,
            cc=cc,
            bcc=bcc,
            attachments=attachments,
            label=template or subject,
        )
        status = self._status(result)
        row = await MailLog.create(
            **stored,
            status=status,
            message_id=result.message_id,
            error=result.error,
            provider_response=result.provider_response,
            sent_at=datetime.now(UTC) if result.success else None,
        )
        if not result.success:
            raise RuntimeError(result.error or "mail delivery failed")
        return {
            "id": row.id,
            "message_id": result.message_id,
            "suppressed": status == "suppressed",
            "to": to,
        }

    async def retry(self, state: EnvironmentState, row: MailLog) -> dict[str, Any]:
        """Send a logged message again, exactly as it was, as a new log entry pointing back at it."""
        return await self.send(
            state,
            list(row.to or []),
            row.subject,
            html=row.html,
            text=row.text,
            source=row.source,
            from_email=row.from_email,
            reply_to=row.reply_to,
            cc=row.cc,
            bcc=row.bcc,
            attachments=row.attachments,
            data=row.data,
            retry_of=row.id,
        ) | {"template": row.template}

    async def send_due(self, limit: int = 50) -> int:
        """Send the scheduled messages whose time has come. Each is claimed first, so two schedulers never send one twice."""
        sent = 0
        due = (
            await MailLog.filter(status="scheduled", scheduled_at__lte=datetime.now(UTC))
            .order_by("scheduled_at")
            .limit(limit)
        )
        for row in due:
            if not await MailLog.filter(id=row.id, status="scheduled").update(status="sending"):
                continue
            try:
                state = await self.platform.state(row.env)
                result = await self._deliver(
                    state,
                    to=list(row.to or []),
                    subject=row.subject,
                    html=row.html,
                    text=row.text,
                    from_email=row.from_email,
                    reply_to=row.reply_to,
                    cc=row.cc,
                    bcc=row.bcc,
                    attachments=row.attachments,
                    label=row.template or row.subject,
                )
                row.status = self._status(result)
                row.message_id = result.message_id
                row.error = result.error
                row.provider_response = result.provider_response
                row.sent_at = datetime.now(UTC) if result.success else None
            except Exception as exc:  # the environment is gone, or delivery broke before a result
                row.status = "failed"
                row.error = f"{type(exc).__name__}: {exc}"
            row.attempts = (row.attempts or 0) + 1
            await row.save()
            sent += 1
        return sent

    async def test_connection(
        self, state: EnvironmentState, settings: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Connect and sign in to an SMTP server without sending anything. A missing password uses the saved one."""
        stored = self._stored(state)
        host = str(settings.get("host") or "").strip()
        if not host:
            raise ValueError("a host is required")
        port = int(settings.get("port") or 587)
        use_ssl = bool(settings.get("use_ssl", port == 465))
        use_tls = bool(settings.get("use_tls", not use_ssl and port == 587))
        username = settings.get("username") or None
        secret = settings.get("password_secret")
        password = (
            settings.get("password")
            or (state.secret_values.get(secret) if secret else None)
            or (stored.get("password") if stored.get("host") == host else None)
        )

        def probe() -> list[str]:
            steps = []
            started = time.monotonic()
            smtp = (
                smtplib.SMTP_SSL(host, port, timeout=10)
                if use_ssl
                else smtplib.SMTP(host, port, timeout=10)
            )
            steps.append(f"connected to {host}:{port}{' over SSL' if use_ssl else ''}")
            try:
                smtp.ehlo()
                if use_tls and not use_ssl:
                    smtp.starttls()
                    smtp.ehlo()
                    steps.append("started TLS")
                if username and password:
                    smtp.login(username, password)
                    steps.append(f"signed in as {username}")
                elif username:
                    steps.append("no password saved: sign-in not checked")
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
            steps.append(f"took {round((time.monotonic() - started) * 1000)} ms")
            return steps

        try:
            return {"ok": True, "steps": await run_in_threadpool(probe)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    async def close(self) -> None:
        for client in self._clients.values():
            try:
                await client.stop()
            except Exception:
                pass
        self._clients.clear()
