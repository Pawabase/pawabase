"""The dashboard side of the help desk: the inbox staff answer from.

``support.read`` sees the list and a ticket's thread; ``support.manage`` is the one permission that can write to it: reply, or close. Someone can
be given visibility without being handed the ability to speak for the store.

Live updates ride Pawabase realtime. The original's staff WebSocket authenticated per connection; here the client asks for its channel, and only
a member holding ``support.read`` is given the per-store secret in the channel name.
"""

from __future__ import annotations

from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import not_found, unprocessable
from sell4me_kit.services import helpdesk


async def _ticket(c: Ctx) -> q.Row:
    ticket = await q.first(await c.db(), "help_tickets", {"id": c.int_arg("ticket_id", required=True), "store_id": c.store.pk})
    if ticket is None:
        raise not_found("That ticket")
    return ticket


@endpoint("support.list", "GET", "/dash/{store}/support", area="support", permission="support.read", summary="The inbox: tickets by status, with counts", original="GET /help-desk")
async def tickets(c: Ctx):
    await c.dashboard("support.read")
    db, store = await c.db(), c.store
    status = c.arg("status") or "open"
    where: dict[str, Any] = {"store_id": store.pk}
    if status in ("open", "answered", "closed"):
        where["status"] = status
    rows = await q.find(db, "help_tickets", where, order="last_message_at DESC", limit=100)
    return {"data": [helpdesk.serialize_ticket(t) for t in rows], "status": status,
            "counts": {s: await q.count(db, "help_tickets", {"store_id": store.pk, "status": s}) for s in ("open", "answered", "closed")},
            "store": {"help_desk_enabled": store.help_desk_enabled}}


@endpoint("support.channel", "GET", "/dash/{store}/support/channel", area="support", permission="support.read",
          summary="The realtime channel for this store's inbox. The channel name embeds a per-store secret that only a member with support.read is ever given",
          original="WS /help-desk/ws (staff_connection)")
async def channel(c: Ctx):
    await c.dashboard("support.read")
    db, store = await c.db(), c.store
    await helpdesk.ensure_store_secret(db, store)
    return {"channel": helpdesk.store_channel(await q.get(db, "stores", store.pk)), "events": ["ticket.created", "message", "ticket.updated"]}


@endpoint("support.show", "GET", "/dash/{store}/support/{ticket_id}", area="support", permission="support.read", summary="A ticket and its whole thread", original="GET /help-desk/{id}")
async def ticket_show(c: Ctx):
    await c.dashboard("support.read")
    db = await c.db()
    ticket = await _ticket(c)
    rows = await db.fetch("SELECT m.*, p.full_name AS author_name FROM help_messages m LEFT JOIN profiles p ON p.user_id = m.author_id WHERE m.ticket_id = ? ORDER BY m.id", [ticket.pk])
    return {"ticket": helpdesk.serialize_ticket(ticket), "messages": [{**helpdesk.serialize_message(q.Row(m)), "author_name": m["author_name"]} for m in rows]}


@endpoint("support.reply", "POST", "/dash/{store}/support/{ticket_id}/reply", area="support", permission="support.manage", summary="Answer a ticket (emails the shopper if they opted in)",
          fields=[{"name": "message", "type": "text", "required": True}], original="POST /help-desk/{id}/reply")
async def ticket_reply(c: Ctx):
    await c.dashboard("support.manage")
    ticket = await _ticket(c)
    body = (c.input.get("message") or "").strip()
    if not body:
        raise unprocessable("Write a reply first.", "validation_failed", {"message": "Write a reply first."})
    message = await helpdesk.add_staff_reply(c, c.store, ticket, author_id=c.user_id, body=body[:4000])
    return {"message": helpdesk.serialize_message(message)}


@endpoint("support.close", "POST", "/dash/{store}/support/{ticket_id}/close", area="support", permission="support.manage", summary="Close a ticket (a shopper's follow-up reopens it)",
          original="POST /help-desk/{id}/close")
async def ticket_close(c: Ctx):
    await c.dashboard("support.manage")
    ticket = await _ticket(c)
    await helpdesk.close(c, c.store, ticket)
    return {"status": "closed"}
