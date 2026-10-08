"""The messages Akountz sends. Each environment may override subject and text.

Overrides live in the environment's auth settings::

    "emails": {"verify": {"subject": "Confirm your {project} account", "text": "... {link} ..."}}

Placeholders are ``{project}``, ``{link}``, ``{email}`` and, for invitations,
``{organization}`` and ``{role}``. Delivery goes through the API's mail
service, so it uses the environment's own SMTP settings.
"""

from __future__ import annotations

import html as html_lib
from typing import Any

from app.environment import AuthConfig
from app.platform import Akountz

DEFAULTS: dict[str, dict[str, str]] = {
    "verify": {
        "subject": "Confirm your email for {project}",
        "text": "Welcome to {project}.\n\nConfirm your email address by opening this link:\n\n{link}\n\nIf you did not sign up, you can ignore this message.",
    },
    "recovery": {
        "subject": "Reset your {project} password",
        "text": "Someone asked to reset the password for {email}.\n\nChoose a new password here (the link works once, for an hour):\n\n{link}\n\nIf this was not you, ignore this message; your password is unchanged.",
    },
    "magic": {
        "subject": "Your {project} sign-in link",
        "text": "Sign in to {project} with this link. It works once, for 15 minutes:\n\n{link}",
    },
    "invite": {
        "subject": "You are invited to {organization} on {project}",
        "text": "You have been invited to join {organization} as {role}.\n\nAccept the invitation here:\n\n{link}",
    },
    "email_change": {
        "subject": "Confirm your new email for {project}",
        "text": "Confirm that this is your new email address for {project}:\n\n{link}",
    },
}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


async def send(akountz: Akountz, config: AuthConfig, kind: str, to: str, **values: Any) -> None:
    template = {**DEFAULTS[kind], **(config.emails.get(kind) or {})}
    values = _Safe(project=config.project_name or "Pawabase", email=to, **values)
    subject = template["subject"].format_map(values)
    text = template["text"].format_map(values)
    escaped = html_lib.escape(text).replace("\n", "<br>")
    link = values.get("link")
    if link and isinstance(link, str):
        escaped = escaped.replace(
            html_lib.escape(link),
            f'<a href="{html_lib.escape(link, quote=True)}">{html_lib.escape(link)}</a>',
        )
    await akountz.send_mail(config.env, to=to, subject=subject, text=text, html=f"<p>{escaped}</p>")
