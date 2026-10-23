"""The messages Accounts sends. Each environment may override subject and text.

Overrides live in the environment's auth settings::

    "emails": {"verify": {"subject": "Confirm your {project} account", "text": "... {link} ..."}}

or hand a message to one of the environment's published mail templates, which then reads
``project``, ``email``, ``link`` (and the kind's other values) and ``brand``
(``name``, ``logo_url``, ``primary``)::

    "emails": {"verify": {"template": "welcome"}}

Placeholders are ``{project}``, ``{link}``, ``{email}`` and, for invitations,
``{organization}`` and ``{role}``. Delivery goes through the API's mail
service, so it uses the environment's own SMTP settings.
"""

from __future__ import annotations

import html as html_lib
from typing import Any

from pawabase_server.identity.accounts import Accounts
from pawabase_server.identity.environment import AuthConfig

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
    "mfa_code": {
        "subject": "Your {project} verification code",
        "text": "Your {project} verification code is {code}.\n\nIt works once and expires in {minutes} minutes. If you did not just try to sign in, change your password.",
    },
    "password_changed": {
        "subject": "Your {project} password was changed",
        "text": "The password for {email} on {project} was just changed.\n\nIf this was you, there is nothing to do. If it was not, reset your password now and contact support.",
    },
    "new_device": {
        "subject": "New sign-in to {project}",
        "text": "Your {project} account ({email}) was just signed in to from a new device:\n\n{device}\n\nIf this was you, there is nothing to do. If it was not, change your password and sign out everywhere.",
    },
    "email_change": {
        "subject": "Confirm your new email for {project}",
        "text": "Confirm that this is your new email address for {project}:\n\n{link}",
    },
}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _brand(config: AuthConfig) -> dict[str, str]:
    """What a stored template can read as ``brand``: the name, logo and colour chosen in Studio."""
    branding = config.branding or {}
    return {
        "name": branding.get("name") or config.project_name or "Pawabase",
        "logo_url": branding.get("logo_url") or "",
        "primary": branding.get("primary") or "#6d5bd0",
    }


async def _stored_template(accounts: Accounts, config: AuthConfig, kind: str) -> str | None:
    """The name of the published mail template chosen for this message, or nothing when it should use the default."""
    name = (config.emails.get(kind) or {}).get("template")
    if not name:
        return None
    try:
        state = await accounts.platform.state(config.env)
    except Exception:
        return None
    stored = state.mail_templates.get(name)
    if stored is None or (
        getattr(stored, "status", "published") == "draft" and not (stored.html or stored.text)
    ):
        return None
    return name


async def send(accounts: Accounts, config: AuthConfig, kind: str, to: str, **values: Any) -> None:
    chosen = await _stored_template(accounts, config, kind)
    if chosen:
        data = {"project": config.project_name or "Pawabase", "email": to, "brand": _brand(config)}
        data.update(values)
        await accounts.send_mail(
            config.env, to=to, subject="", text="", html=None, template=chosen, data=data
        )
        return
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
    await accounts.send_mail(
        config.env, to=to, subject=subject, text=text, html=f"<p>{escaped}</p>"
    )
