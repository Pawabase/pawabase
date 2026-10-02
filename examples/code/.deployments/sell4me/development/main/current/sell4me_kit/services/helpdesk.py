"""The help desk: a shopper's ticket, and a staff member's reply.

A shopper has no account: their identity is what they typed into the widget and their proof of ownership is knowing the
ticket's ``token`` (a shared link, not a login). A staff reply comes from a real signed-in member holding ``support.manage``.

Live updates ride Pawabase realtime. A ticket's thread is broadcast on ``help:ticket:<token>`` (the unguessable token is the
capability, the same as the link) and a store's inbox on ``help:staff:<secret>`` (a per-store secret that only a member holding
``support.read`` is ever given, by ``GET /dash/{store}/support/channel``).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from .. import q


def _token() -> str:
    return secrets.token_urlsafe(24)


def ticket_channel(ticket: q.Row) -> str:
    return f"help:ticket:{ticket.token}"


def store_channel(store: q.Row) -> str:
    return f"help:staff:{store.help_secret}"


async def ensure_store_secret(db: Any, store: q.Row) -> str:
    if not store.help_secret:
        secret = secrets.token_urlsafe(24)
        await q.update(db, "stores", store.pk, {"help_secret": secret})
        store["help_secret"] = secret
    return store.help_secret


async def _broadcast(c: Any, store: q.Row, ticket: q.Row, *, kind: str, message: q.Row | None = None) -> None:
    payload: dict[str, Any] = {"type": kind, "ticket": serialize_ticket(ticket)}
    if message is not None:
        payload["message"] = serialize_message(message)
    db = await c.db()
    await ensure_store_secret(db, store)
    for channel in (ticket_channel(ticket), store_channel(store)):
        try:
            await c.runtime.publish(channel, kind, payload)
        except Exception:  # noqa: BLE001 — a missed live update must not fail the reply
            pass


async def open_ticket(c: Any, *, store: q.Row, name: str, email: str, message: str, opt_in_email: bool) -> q.Row:
    """Start a new ticket from the storefront widget, with its first message."""
    db = await c.db()
    subject = message.strip().splitlines()[0][:200] if message.strip() else "New question"
    ticket = await q.insert(db, "help_tickets", {"store_id": store.pk, "token": _token(), "customer_name": name.strip()[:120] or "A visitor",
                                                 "customer_email": email.strip().lower(), "opt_in_email": opt_in_email, "subject": subject,
                                                 "status": "open", "last_message_at": datetime.now(UTC)})
    first = await q.insert(db, "help_messages", {"ticket_id": ticket.pk, "from_staff": False, "body": message.strip()})
    await _notify_staff(db, ticket)
    await _broadcast(c, store, ticket, kind="ticket.created", message=first)
    return ticket


async def add_customer_reply(c: Any, store: q.Row, ticket: q.Row, body: str) -> q.Row:
    """A shopper following up always reopens the ticket: it means staff's last answer was not the end of it."""
    db = await c.db()
    message = await q.insert(db, "help_messages", {"ticket_id": ticket.pk, "from_staff": False, "body": body.strip()})
    await q.update(db, "help_tickets", ticket.pk, {"status": "open", "last_message_at": datetime.now(UTC)})
    ticket = await q.get(db, "help_tickets", ticket.pk)
    await _notify_staff(db, ticket)
    await _broadcast(c, store, ticket, kind="message", message=message)
    return message


async def add_staff_reply(c: Any, store: q.Row, ticket: q.Row, *, author_id: str, body: str) -> q.Row:
    """A staff member's answer. Emails the shopper if they opted in."""
    db = await c.db()
    message = await q.insert(db, "help_messages", {"ticket_id": ticket.pk, "from_staff": True, "author_id": author_id, "body": body.strip()})
    await q.update(db, "help_tickets", ticket.pk, {"status": "answered", "last_message_at": datetime.now(UTC)})
    ticket = await q.get(db, "help_tickets", ticket.pk)
    if ticket.opt_in_email:
        await _email_customer(c, store, ticket, body)
    await _broadcast(c, store, ticket, kind="message", message=message)
    return message


async def close(c: Any, store: q.Row, ticket: q.Row) -> None:
    db = await c.db()
    await q.update(db, "help_tickets", ticket.pk, {"status": "closed", "closed_at": datetime.now(UTC)})
    await _broadcast(c, store, await q.get(db, "help_tickets", ticket.pk), kind="ticket.updated")


async def _notify_staff(db: Any, ticket: q.Row) -> None:
    await q.insert(db, "notifications", {"store_id": ticket.store_id, "kind": "help.ticket", "title": f"Help desk: {ticket.subject}",
                                         "body": f"{ticket.customer_name} · {ticket.customer_email}", "url": f"/help-desk/{ticket.pk}", "level": "info",
                                         "required_permission": "support.read"})


async def _email_customer(c: Any, store: q.Row, ticket: q.Row, reply: str) -> None:
    db = await c.db()
    first = await q.first(db, "help_messages", {"ticket_id": ticket.pk, "from_staff": False}, order="id")
    await c.dispatch("mail.send", {"template": "help_reply", "to": ticket.customer_email, "subject": "", "context": {
        "name": ticket.customer_name, "reply": reply, "question": first.body if first else None, "ticket_url": f"/help/{ticket.token}",
        "store": {"name": store.name, "slug": store.slug, "support_email": store.support_email or store.email}}})


def serialize_ticket(ticket: q.Row) -> dict[str, Any]:
    return {"id": ticket.pk, "token": ticket.token, "subject": ticket.subject, "status": ticket.status, "customer_name": ticket.customer_name,
            "customer_email": ticket.customer_email, "opt_in_email": ticket.opt_in_email, "created_at": ticket.created_at,
            "last_message_at": ticket.last_message_at}


def serialize_message(message: q.Row) -> dict[str, Any]:
    return {"id": message.pk, "from_staff": message.from_staff, "body": message.body, "created_at": message.created_at}
