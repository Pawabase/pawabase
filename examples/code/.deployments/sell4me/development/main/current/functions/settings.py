"""Store settings, staff, roles, developers and the audit log.

The staff endpoints are where privilege escalation would live if it were going to, so three rules hold throughout: nobody may grant a
permission they do not themselves hold; nobody may change the owner's membership or their own role; the last owner cannot be removed or demoted.
API keys are shown once, at creation, and only a hash is stored: a key readable from the database is a key readable by anyone who reaches it.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import audit, mail_templates, q
from sell4me_kit.audit import record_from
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import endpoint
from sell4me_kit.errors import bad_request, forbidden, not_found, unprocessable
from sell4me_kit.events import emit
from sell4me_kit.money import SUPPORTED_CURRENCIES, Money
from sell4me_kit.perms import ALL_PERMISSIONS, ROLE_PERMISSIONS, ROLES, member_can, role_matrix
from sell4me_kit.profiles import ensure_profile
from sell4me_kit.services import onboarding, webhooks_out

WEBHOOK_EVENTS = ("order.created", "order.paid", "order.fulfilled", "order.cancelled", "payment.succeeded", "payment.failed", "refund.created",
                  "product.created", "product.updated", "inventory.low", "customer.created")


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _local_input(value: Any, tz_name: str) -> str:
    moment = q.parse_dt(value)
    return moment.astimezone(_zone(tz_name)).strftime("%Y-%m-%dT%H:%M") if moment else ""


def _parse_local(raw: str, tz_name: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        naive = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return (naive if naive.tzinfo else naive.replace(tzinfo=_zone(tz_name))).astimezone(UTC)


def _store_prop(store: q.Row) -> dict[str, Any]:
    return {"id": store.pk, "name": store.name, "legal_name": store.legal_name, "slug": store.slug, "currency": store.currency, "country": store.country,
            "timezone_name": store.timezone_name, "weight_unit": store.weight_unit, "email": store.email, "phone": store.phone, "support_email": store.support_email,
            "address_line1": store.address_line1, "address_line2": store.address_line2, "city": store.city, "province": store.province, "postal_code": store.postal_code,
            "status": store.status, "launched_at": store.launched_at, "maintenance_enabled": store.maintenance_enabled, "maintenance_title": store.maintenance_title or "",
            "maintenance_message": store.maintenance_message or "",
            # Wall-clock time in the store's own timezone: what a datetime-local input speaks and what the merchant means.
            "maintenance_ends_at": _local_input(store.maintenance_ends_at, store.timezone_name), "help_desk_enabled": store.help_desk_enabled,
            "help_desk_greeting": store.help_desk_greeting or ""}


# ── general ──────────────────────────────────────────────────────────────

@endpoint("settings.general", "GET", "/dash/{store}/settings", area="settings", permission="settings.read", summary="Store settings, the onboarding checklist and what is locked",
          original="GET /settings")
async def settings_general(c: Ctx):
    await c.dashboard("settings.read")
    db, store = await c.db(), c.store
    has_orders = await q.exists(db, "orders", {"store_id": store.pk})
    unsupported = store.currency not in SUPPORTED_CURRENCIES  # a store whose currency Paystack cannot settle takes no payments, so nothing to protect by locking
    settings = await c.settings()
    return {"store": _store_prop(store), "shop_url": settings.shop_url(store.slug), "currencies": list(SUPPORTED_CURRENCIES),
            # The currency is the unit every stored integer is denominated in: changing it after the first order would silently reinterpret all of them.
            "currency_locked": has_orders and not unsupported, "currency_unsupported": unsupported, "onboarding": await onboarding.progress(c, store)}


@endpoint("settings.save", "PATCH", "/dash/{store}/settings", area="settings", permission="settings.update", summary="Save store settings",
          fields=[{"name": n, "type": "string"} for n in ("name", "legal_name", "email", "phone", "support_email", "address_line1", "address_line2", "city", "province",
                                                          "postal_code", "country", "timezone_name", "weight_unit", "currency")], original="POST /settings")
async def settings_save(c: Ctx):
    await c.dashboard("settings.update")
    db, store, data = await c.db(), c.store, c.input
    before = {"name": store.name, "currency": store.currency, "country": store.country}
    changes: dict[str, Any] = {}
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise unprocessable("Your store needs a name.", "validation_failed", {"name": "Your store needs a name."})
        changes["name"] = name
    errors: dict[str, str] = {}
    for field in ("legal_name", "email", "phone", "support_email", "address_line1", "address_line2", "city", "province", "postal_code"):
        if field in data:
            changes[field] = (data.get(field) or "").strip() or None
            if field in ("email", "support_email") and changes[field] and not _EMAIL.match(changes[field]):
                errors[field] = "Enter a valid email address."
    if "country" in data:
        changes["country"] = (data.get("country") or store.country).upper()[:2]
    if "timezone_name" in data:
        changes["timezone_name"] = (data.get("timezone_name") or store.timezone_name)[:64]
        try:
            ZoneInfo(changes["timezone_name"])
        except (ZoneInfoNotFoundError, ValueError):
            errors["timezone_name"] = "Choose a timezone from the list (e.g. Africa/Lagos)."
    if errors:
        raise unprocessable("Check the highlighted fields.", "validation_failed", errors)
    if data.get("weight_unit") in ("kg", "g", "lb", "oz"):
        changes["weight_unit"] = data["weight_unit"]
    currency = (data.get("currency") or "").upper()
    if store.currency not in SUPPORTED_CURRENCIES and currency not in SUPPORTED_CURRENCIES and "currency" in data:
        raise unprocessable(f"Choose a currency Paystack can charge: {', '.join(SUPPORTED_CURRENCIES)}.", "validation_failed", {"currency": "Unsupported currency."})
    if currency and currency != store.currency:
        if store.currency in SUPPORTED_CURRENCIES and await q.exists(db, "orders", {"store_id": store.pk}):
            raise unprocessable("You can't change currency once you have orders.", "validation_failed", {"currency": "You can't change currency once you have orders."})
        if currency in SUPPORTED_CURRENCIES:
            changes["currency"] = currency
    if changes:
        await q.update(db, "stores", store.pk, changes)
    store = await q.get(db, "stores", store.pk)
    c.store = store
    await onboarding.complete_step(c, store, "store_details")
    await record_from(c, action="store.updated", resource_type="store", resource_id=store.pk, summary="Updated store settings",
                      changes=audit.changes_between(before, {"name": store.name, "currency": store.currency, "country": store.country}))
    return _store_prop(store)


@endpoint("settings.maintenance", "POST", "/dash/{store}/settings/maintenance", area="settings", permission="settings.update", summary="Turn maintenance mode on or off and set what shoppers are told",
          fields=[{"name": "maintenance_enabled", "type": "boolean"}, {"name": "maintenance_title", "type": "string"}, {"name": "maintenance_message", "type": "text"},
                  {"name": "maintenance_ends_at", "type": "string"}], original="POST /settings/maintenance")
async def settings_maintenance(c: Ctx):
    await c.dashboard("settings.update")
    db, store, data = await c.db(), c.store, c.input
    enabled = str(data.get("maintenance_enabled") or "").lower() in ("1", "true", "on", "yes")
    ends_at = _parse_local(data.get("maintenance_ends_at") or "", store.timezone_name)
    if enabled and ends_at is not None and ends_at <= datetime.now(UTC):
        raise unprocessable("Pick a time in the future, or leave it blank.", "validation_failed", {"maintenance_ends_at": "Pick a time in the future, or leave it blank."})
    was = bool(store.maintenance_enabled)
    await q.update(db, "stores", store.pk, {"maintenance_enabled": enabled, "maintenance_title": (data.get("maintenance_title") or "").strip()[:120] or None,
                                           "maintenance_message": (data.get("maintenance_message") or "").strip()[:2000] or None, "maintenance_ends_at": ends_at})
    if was != enabled:
        await record_from(c, action="store.maintenance_on" if enabled else "store.maintenance_off", resource_type="store", resource_id=store.pk,
                          summary="Turned maintenance mode on" if enabled else "Turned maintenance mode off")
    return {"store": _store_prop(await q.get(db, "stores", store.pk)),
            "message": "Maintenance mode is on — shoppers see the holding page." if enabled else "Maintenance saved. Your shop is open."}


@endpoint("settings.help_desk", "POST", "/dash/{store}/settings/help-desk", area="settings", permission="settings.update", summary="Turn the storefront's help desk widget on or off",
          fields=[{"name": "help_desk_enabled", "type": "boolean"}, {"name": "help_desk_greeting", "type": "string"}], original="POST /settings/help-desk")
async def settings_help_desk(c: Ctx):
    await c.dashboard("settings.update")
    db, data = await c.db(), c.input
    enabled = str(data.get("help_desk_enabled") or "").lower() in ("1", "true", "on", "yes")
    await q.update(db, "stores", c.store.pk, {"help_desk_enabled": enabled, "help_desk_greeting": (data.get("help_desk_greeting") or "").strip()[:200] or None})
    return {"help_desk_enabled": enabled, "message": "Help desk widget is live on your storefront." if enabled else "Help desk widget saved — it's hidden from shoppers."}


@endpoint("settings.launch", "POST", "/dash/{store}/settings/launch", area="settings", permission="settings.update", summary="Open the store to the public (refuses with what is still to do)",
          original="POST /settings/launch")
async def settings_launch(c: Ctx):
    await c.dashboard("settings.update")
    ok, message = await onboarding.launch(c, c.store)
    if ok and c.store.status == "active":
        await emit(c, "store.launched", store=c.store, actor={"id": c.user_id, "email": c.email}, ip_address=c.client_ip)
    if not ok:
        raise unprocessable(message, "cannot_launch", await onboarding.progress(c, c.store))
    return {"status": c.store.status, "message": message}


@endpoint("settings.onboarding", "GET", "/dash/{store}/onboarding", area="settings", permission="settings.read", summary="The setup checklist with each step's real state", original="shared props / dashboard")
async def settings_onboarding(c: Ctx):
    await c.dashboard("settings.read")
    return await onboarding.progress(c, c.store)


# ── team ─────────────────────────────────────────────────────────────────

def _may_grant(c: Ctx, role: str) -> bool:
    """True when every permission the role carries is one the caller already holds. An owner passes trivially."""
    return c.can("*") or all(c.can(p) for p in ROLE_PERMISSIONS.get(role, ()))


@endpoint("team.list", "GET", "/dash/{store}/team", area="settings", permission="staff.read", summary="Members, open invitations and the roles on offer", original="GET /settings/team")
async def team_list(c: Ctx):
    await c.dashboard("staff.read")
    db, store = await c.db(), c.store
    members = await db.fetch("SELECT m.*, p.full_name AS full_name, p.email AS email FROM store_members m LEFT JOIN profiles p ON p.user_id = m.user_id "
                             "WHERE m.store_id = ? AND m.deleted_at IS NULL ORDER BY m.id", [store.pk])
    invites = await q.find(db, "invitations", {"store_id": store.pk, "accepted_at": None, "revoked_at": None}, order="id DESC")
    return {"members": [{"id": m["id"], "user_id": m["user_id"], "name": m["full_name"] or (m["email"] or "").split("@")[0], "email": m["email"], "role": m["role"], "status": m["status"],
                         "extra_permissions": m["extra_permissions"] or [], "denied_permissions": m["denied_permissions"] or [], "is_you": m["id"] == c.member.pk,
                         "last_seen_at": m["last_seen_at"], "created_at": m["created_at"]} for m in members],
            "invitations": [{"id": i.pk, "email": i.email, "role": i.role, "expires_at": i.expires_at, "accept_url": f"/invitations/{i.token}", "token": i.token} for i in invites],
            "roles": [{"key": r, "label": r.title(), "permissions": ["*"] if r == "owner" else list(ROLE_PERMISSIONS[r])} for r in ROLES], "can_manage": c.can("staff.manage")}


@endpoint("team.invite", "POST", "/dash/{store}/team/invitations", area="settings", permission="staff.manage", summary="Invite someone to the store (cannot invite above your own reach)",
          fields=[{"name": "email", "type": "string", "required": True}, {"name": "role", "type": "string"}], original="POST /settings/team/invite")
async def team_invite(c: Ctx):
    """A member cannot invite at a role carrying permissions they do not have: otherwise a support agent with ``staff.manage`` could invite an admin and sign in as them."""
    await c.dashboard("staff.manage")
    db, store, data = await c.db(), c.store, c.input
    email = (data.get("email") or "").strip().lower()
    role = data.get("role") if data.get("role") in ROLES else "support"
    if not email or "@" not in email:
        raise unprocessable("Enter a valid email address.", "validation_failed", {"email": "Enter a valid email address."})
    if role == "owner":
        raise unprocessable("A store has one owner. Transfer ownership instead.", "validation_failed", {"role": "A store has one owner."})
    if not _may_grant(c, role):
        raise unprocessable("You can't invite someone at a role with more access than yours.", "validation_failed", {"role": "More access than yours."})
    profile = await q.first(db, "profiles", {"email": email})
    if profile and await q.exists(db, "store_members", {"store_id": store.pk, "user_id": profile.user_id}):
        raise unprocessable("They're already on this store.", "validation_failed", {"email": "They're already on this store."})
    invitation = await q.insert(db, "invitations", {"store_id": store.pk, "email": email, "role": role, "token": secrets.token_urlsafe(32), "invited_by_id": c.user_id,
                                                    "expires_at": datetime.now(UTC) + timedelta(days=7)})
    me = await q.first(db, "profiles", {"user_id": c.user_id})
    await emit(c, "staff.invited", store=store, invitation=invitation, actor={"id": c.user_id, "email": c.email}, invited_by_name=me.full_name if me else None, ip_address=c.client_ip)
    return {"id": invitation.pk, "email": email, "role": role, "expires_at": invitation.expires_at, "accept_url": f"/invitations/{invitation.token}", "token": invitation.token,
            "message": f"Invitation created for {email}."}


@endpoint("team.revoke_invitation", "DELETE", "/dash/{store}/team/invitations/{invitation_id}", area="settings", permission="staff.manage", summary="Revoke an open invitation",
          original="(added: the original could not revoke an invitation)")
async def team_revoke_invitation(c: Ctx):
    await c.dashboard("staff.manage")
    db = await c.db()
    invitation = await q.first(db, "invitations", {"id": c.int_arg("invitation_id", required=True), "store_id": c.store.pk, "accepted_at": None, "revoked_at": None})
    if invitation is None:
        raise not_found("That invitation")
    await q.update(db, "invitations", invitation.pk, {"revoked_at": datetime.now(UTC)})
    return {"revoked": True}


@endpoint("account.accept_invitation", "POST", "/account/invitations/accept", area="account", summary="Accept a staff invitation with its token (the signed-in user joins the store)",
          fields=[{"name": "token", "type": "string", "required": True}], original="(added: the original emailed /invitations/{token} but had no route that accepted it)")
async def accept_invitation(c: Ctx):
    """The invitation is keyed by email and may be for someone with no account yet, so it must be accepted by the signed-in person it was sent to."""
    user_id = c.require_user()
    db = await c.db()
    await ensure_profile(c)  # a member who has never been through onboarding still needs a name and email on the team list
    invitation = await q.first(db, "invitations", {"token": str(c.input.get("token") or "")})
    if invitation is None or not invitation.is_open:
        raise unprocessable("That invitation has expired or was already used.", "invitation_closed")
    if (c.email or "").lower() != invitation.email.lower():
        raise forbidden("This invitation was sent to a different email address.", "wrong_account")
    store = await q.get(db, "stores", invitation.store_id)
    existing = await q.first(db, "store_members", {"store_id": store.pk, "user_id": user_id})
    if existing is None:
        await q.insert(db, "store_members", {"store_id": store.pk, "user_id": user_id, "role": invitation.role, "status": "active", "extra_permissions": [], "denied_permissions": []})
    elif existing.status != "active":
        await q.update(db, "store_members", existing.pk, {"status": "active", "role": invitation.role})
    await q.update(db, "invitations", invitation.pk, {"accepted_at": datetime.now(UTC)})
    c.store = store
    await emit(c, "staff.joined", store=store, invitation=invitation, email=c.email, actor={"id": user_id, "email": c.email}, ip_address=c.client_ip)
    return {"store": {"id": store.pk, "slug": store.slug, "name": store.name}, "role": invitation.role}


@endpoint("team.update_member", "PATCH", "/dash/{store}/team/{member_id}", area="settings", permission="staff.manage", summary="Change a member's role, permission overrides or status",
          fields=[{"name": "role", "type": "string"}, {"name": "extra_permissions", "type": "json"}, {"name": "denied_permissions", "type": "json"}, {"name": "status", "type": "string"}],
          original="POST /settings/team/{id}")
async def team_update_member(c: Ctx):
    """Three refusals, each closing a real hole: changing your own role (privilege escalation), changing the owner's (locking them out), granting a permission you do not hold."""
    await c.dashboard("staff.manage")
    db, data = await c.db(), c.input
    member = await q.first(db, "store_members", {"id": c.int_arg("member_id", required=True), "store_id": c.store.pk})
    if member is None:
        raise not_found("That member")
    if member.pk == c.member.pk:
        raise unprocessable("You can't change your own role.", "self_change")
    if member.role == "owner":
        raise unprocessable("The owner's access can't be changed here.", "owner_protected")
    changes: dict[str, Any] = {}
    role = data.get("role")
    if role and role in ROLES and role != "owner":
        if not _may_grant(c, role):
            raise unprocessable("You can't grant a role with more access than yours.", "escalation")
        changes["role"] = role
    known = {key for group in ALL_PERMISSIONS.values() for key, _ in group}
    for field in ("extra_permissions", "denied_permissions"):
        values = data.get(field)
        if isinstance(values, list):
            cleaned = [p for p in values if p in known]
            if field == "extra_permissions":
                cleaned = [p for p in cleaned if c.can(p)]  # a member cannot hand out what they do not hold
            changes[field] = cleaned
    if data.get("status") in ("active", "suspended"):
        changes["status"] = data["status"]
    if changes:
        await q.update(db, "store_members", member.pk, changes)
    member = await q.get(db, "store_members", member.pk)
    await record_from(c, action="staff.updated", resource_type="store_member", resource_id=member.pk, summary=f"Updated access for member #{member.pk} ({member.role})")
    return {"id": member.pk, "role": member.role, "status": member.status, "extra_permissions": member.extra_permissions or [], "denied_permissions": member.denied_permissions or []}


@endpoint("team.remove_member", "DELETE", "/dash/{store}/team/{member_id}", area="settings", permission="staff.manage", summary="Remove a member (never yourself, never the last owner)",
          original="POST /settings/team/{id}/remove")
async def team_remove_member(c: Ctx):
    await c.dashboard("staff.manage")
    db = await c.db()
    member = await q.first(db, "store_members", {"id": c.int_arg("member_id", required=True), "store_id": c.store.pk})
    if member is None:
        raise not_found("That member")
    if member.pk == c.member.pk:
        raise unprocessable("You can't remove yourself.", "self_change")
    if member.role == "owner" and await q.count(db, "store_members", {"store_id": c.store.pk, "role": "owner"}) <= 1:
        raise unprocessable("A store needs an owner.", "last_owner")
    await q.soft_delete(db, "store_members", member.pk)
    await record_from(c, action="staff.removed", resource_type="store_member", resource_id=member.pk, summary=f"Removed member #{member.pk}")
    return {"removed": True}


@endpoint("team.roles", "GET", "/dash/{store}/roles", area="settings", permission="staff.read", summary="The roles-and-permissions grid, built from the table the guard reads",
          original="GET /settings/roles")
async def team_roles(c: Ctx):
    await c.dashboard("staff.read")
    return role_matrix()


# ── developers: API keys and webhooks ────────────────────────────────────

@endpoint("developers.overview", "GET", "/dash/{store}/developers", area="developers", permission="developers.read", summary="API keys (prefix only), webhooks, and what can be subscribed to",
          original="GET /developers")
async def developers_overview(c: Ctx):
    await c.dashboard("developers.read")
    db, store = await c.db(), c.store
    keys = await db.fetch("SELECT k.*, p.full_name AS creator FROM api_keys k LEFT JOIN profiles p ON p.user_id = k.created_by_id WHERE k.store_id = ? AND k.deleted_at IS NULL ORDER BY k.id DESC", [store.pk])
    hooks = await q.find(db, "webhooks", {"store_id": store.pk}, order="id DESC")
    return {"api_keys": [{"id": k["id"], "name": k["name"], "prefix": k["prefix"], "scopes": k["scopes"] or [], "last_used_at": k["last_used_at"], "last_used_ip": k["last_used_ip"],
                          "request_count": k["request_count"], "is_active": k["revoked_at"] is None, "created_by": k["creator"], "created_at": k["created_at"], "revoked_at": k["revoked_at"]} for k in keys],
            "webhooks": [{"id": h.pk, "url": h.url, "events": h.events or [], "description": h.description, "is_active": h.is_active, "consecutive_failures": h.consecutive_failures,
                          "last_status_code": h.last_status_code, "last_delivery_at": h.last_delivery_at, "disabled_at": h.disabled_at} for h in hooks],
            "available_events": list(WEBHOOK_EVENTS),
            "permission_groups": [{"name": g, "permissions": [{"key": k, "label": label} for k, label in entries]} for g, entries in ALL_PERMISSIONS.items()],
            "webhook_signature_header": "X-Commerce-Signature"}


@endpoint("developers.create_key", "POST", "/dash/{store}/developers/keys", area="developers", permission="developers.manage", summary="Mint an API key (the plaintext is returned exactly once)",
          fields=[{"name": "name", "type": "string", "required": True}, {"name": "scopes", "type": "json", "required": True}, {"name": "expires_days", "type": "integer"}],
          original="POST /developers/keys")
async def developers_create_key(c: Ctx):
    """Only a hash is stored. Scopes are intersected with what the creating member holds: a key must never be able to do more than the person who made it."""
    await c.dashboard("developers.manage")
    db, store, data = await c.db(), c.store, c.input
    name = (data.get("name") or "").strip()
    if not name:
        raise unprocessable("Give the key a name.", "validation_failed", {"name": "Give the key a name."})
    known = {key for group in ALL_PERMISSIONS.values() for key, _ in group}
    scopes = [s for s in (data.get("scopes") or []) if s in known and c.can(s)]
    if not scopes:
        raise unprocessable("Choose at least one permission you hold.", "validation_failed", {"scopes": "Choose at least one permission you hold."})
    secret = secrets.token_urlsafe(32)
    prefix = f"sk_{'live' if store.status == 'active' else 'test'}_{secret[:6]}"
    plaintext = f"{prefix}.{secret}"
    key = await q.insert(db, "api_keys", {"store_id": store.pk, "name": name, "prefix": prefix[:16], "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(), "scopes": scopes,
                                          "created_by_id": c.user_id, "request_count": 0,
                                          "expires_at": datetime.now(UTC) + timedelta(days=int(data["expires_days"])) if str(data.get("expires_days") or "").isdigit() else None})
    await record_from(c, action="api_key.created", resource_type="api_key", resource_id=prefix, summary=f"Created the API key {name}", changes={"scopes": {"from": None, "to": scopes}})
    return {"id": key.pk, "name": name, "prefix": key.prefix, "scopes": scopes, "key": plaintext, "message": "Key created. Copy it now — it won't be shown again."}


@endpoint("developers.revoke_key", "POST", "/dash/{store}/developers/keys/{key_id}/revoke", area="developers", permission="developers.manage", summary="Revoke an API key",
          original="POST /developers/keys/{id}/revoke")
async def developers_revoke_key(c: Ctx):
    await c.dashboard("developers.manage")
    db = await c.db()
    key = await q.first(db, "api_keys", {"id": c.int_arg("key_id", required=True), "store_id": c.store.pk})
    if key is None:
        raise not_found("That API key")
    await q.update(db, "api_keys", key.pk, {"revoked_at": datetime.now(UTC)})
    await record_from(c, action="api_key.revoked", resource_type="api_key", resource_id=key.prefix, summary=f"Revoked the API key {key.name}")
    return {"id": key.pk, "revoked": True}


@endpoint("developers.save_webhook", "POST", "/dash/{store}/developers/webhooks", area="developers", permission="developers.manage",
          summary="Create or update a webhook endpoint (the signing secret is returned once, on creation)",
          fields=[{"name": "id", "type": "integer"}, {"name": "url", "type": "string", "required": True}, {"name": "events", "type": "json", "required": True},
                  {"name": "description", "type": "string"}, {"name": "is_active", "type": "boolean"}], original="POST /developers/webhooks")
async def developers_save_webhook(c: Ctx):
    await c.dashboard("developers.manage")
    db, store, data = await c.db(), c.store, c.input
    url = (data.get("url") or "").strip()
    settings = await c.settings()
    if not url.startswith("https://") and not (url.startswith("http://") and (store.status != "active" or settings.webhooks_allow_private)):
        raise unprocessable("Use an https:// endpoint. Plain http is only allowed before launch.", "validation_failed", {"url": "Use an https:// endpoint."})
    try:
        await webhooks_out.check_destination(url, allow_private=settings.webhooks_allow_private)
    except webhooks_out.DestinationNotAllowed as error:
        raise unprocessable(str(error), "validation_failed", {"url": str(error)}) from None
    events = [e for e in (data.get("events") or []) if e in WEBHOOK_EVENTS]
    if not events:
        raise unprocessable("Choose at least one event.", "validation_failed", {"events": "Choose at least one event."})
    hook = await q.first(db, "webhooks", {"id": int(data["id"]), "store_id": store.pk}) if data.get("id") else None
    description = (data.get("description") or "").strip() or None
    if hook is None:
        hook = await q.insert(db, "webhooks", {"store_id": store.pk, "url": url, "events": events, "description": description, "secret": secrets.token_urlsafe(32),
                                               "is_active": True, "consecutive_failures": 0})
        return {"id": hook.pk, "url": hook.url, "events": events, "secret": hook.secret, "message": "Webhook created. Copy the signing secret now."}
    changes: dict[str, Any] = {"url": url, "events": events, "description": description, "is_active": bool(data.get("is_active", True))}
    if changes["is_active"]:  # re-enabling a dead endpoint resets its failure count, or it would be disabled again on the next single failure
        changes.update(consecutive_failures=0, disabled_at=None)
    await q.update(db, "webhooks", hook.pk, changes)
    return {"id": hook.pk, "url": url, "events": events, "message": "Webhook saved."}


@endpoint("developers.test_webhook", "POST", "/dash/{store}/developers/webhooks/{webhook_id}/test", area="developers", permission="developers.manage",
          summary="Send a synthetic event through the real delivery path", original="POST /developers/webhooks/{id}/test")
async def developers_test_webhook(c: Ctx):
    await c.dashboard("developers.manage")
    hook = await q.first(await c.db(), "webhooks", {"id": c.int_arg("webhook_id", required=True), "store_id": c.store.pk})
    if hook is None:
        raise not_found("That webhook")
    delivery = await webhooks_out.test_webhook(c, hook)
    ok = delivery.status == "delivered"
    return {"delivered": ok, "status_code": delivery.status_code, "error": delivery.error, "message": f"Endpoint answered {delivery.status_code}." if ok else (delivery.error or f"Endpoint answered {delivery.status_code}.")}


@endpoint("developers.delete_webhook", "DELETE", "/dash/{store}/developers/webhooks/{webhook_id}", area="developers", permission="developers.manage", summary="Remove a webhook endpoint",
          original="POST /developers/webhooks/{id}/delete")
async def developers_delete_webhook(c: Ctx):
    await c.dashboard("developers.manage")
    db = await c.db()
    hook = await q.first(db, "webhooks", {"id": c.int_arg("webhook_id", required=True), "store_id": c.store.pk})
    if hook is None:
        raise not_found("That webhook")
    await q.soft_delete(db, "webhooks", hook.pk)
    return {"removed": True}


@endpoint("developers.webhook_logs", "GET", "/dash/{store}/developers/logs", area="developers", permission="developers.read",
          summary="Every delivery attempt, so a merchant can debug their own endpoint ('delivered on the fourth try, 90 seconds late' is a different fact from 'delivered')",
          original="GET /developers/logs")
async def developers_webhook_logs(c: Ctx):
    await c.dashboard("developers.read")
    db, store = await c.db(), c.store
    status = c.arg("status", "all")
    where, params = ["d.store_id = ?", "d.deleted_at IS NULL"], [store.pk]
    if status != "all":
        where.append("d.status = ?")
        params.append(status)
    rows = await db.fetch(f"SELECT d.*, w.url AS url FROM webhook_deliveries d LEFT JOIN webhooks w ON w.id = d.webhook_id WHERE {' AND '.join(where)} ORDER BY d.id DESC LIMIT 200", params)
    return {"data": [{"id": d["id"], "webhook_id": d["webhook_id"], "url": d["url"], "event": d["event"], "event_id": d["event_id"], "status": d["status"], "attempt": d["attempt"],
                      "status_code": d["status_code"], "duration_ms": d["duration_ms"], "error": d["error"], "response_body": (d["response_body"] or "")[:500] or None,
                      "payload": d["payload"], "next_attempt_at": d["next_attempt_at"], "created_at": d["created_at"]} for d in rows], "status": status,
            "counts": {k: await q.count(db, "webhook_deliveries", {"store_id": store.pk} | ({} if k == "all" else {"status": k})) for k in ("all", "pending", "delivered", "failed")}}


# ── audit log and mail preview ───────────────────────────────────────────

@endpoint("audit.list", "GET", "/dash/{store}/audit", area="settings", permission="settings.read",
          summary="Who did what: read-only (there is no endpoint that edits or deletes a row, which is the log's entire value)", original="GET /settings/audit")
async def audit_list(c: Ctx):
    await c.dashboard("settings.read")
    db, store = await c.db(), c.store
    page, per_page = c.page_params(50, 100)
    where: dict[str, Any] = {"store_id": store.pk}
    if c.arg("action"):
        where["action"] = c.arg("action")
    if c.arg("resource"):
        where["resource_type"] = c.arg("resource")
    total = await q.count(db, "audit_logs", where)
    rows = await q.find(db, "audit_logs", where, order="id DESC", limit=per_page, offset=(page - 1) * per_page)
    kinds = await db.fetch("SELECT action, COUNT(*) AS n FROM audit_logs WHERE store_id = ? AND deleted_at IS NULL GROUP BY action", [store.pk])
    return {"data": [{"id": e.pk, "actor": e.actor_label or "System", "action": e.action, "resource_type": e.resource_type, "resource_id": e.resource_id, "summary": e.summary,
                      "changes": e.changes or {}, "ip_address": e.ip_address, "created_at": e.created_at} for e in rows],
            "actions": sorted({k["action"] for k in kinds}), "filters": {"action": c.arg("action"), "resource": c.arg("resource")},
            "pagination": {"page": page, "per_page": per_page, "total": total, "pages": max(1, -(-total // per_page))}}


def _sample_mail_context(template: str, store: q.Row) -> dict[str, Any]:
    """A believable context for one template, using this store's real identity (so a merchant sees their shop, and would notice a missing support address)."""
    identity = {"name": store.name, "slug": store.slug, "support_email": store.support_email or store.email}
    money = lambda minor: Money(minor, store.currency).format()  # noqa: E731
    items = [{"title": "Field Notebook", "variant": "A5 · Ochre", "quantity": 2, "total": money(3998)}, {"title": "Fountain Pen", "variant": None, "quantity": 1, "total": money(6500)}]
    samples = {
        "order_receipt": {"store": identity, "order": {"number": 1042, "status_url": "/orders/1042/sample-token", "subtotal": money(10498), "discount": money(1000), "shipping": money(495),
                                                       "total": money(9993), "has_discount": True}, "items": items,
                          "address": {"name": "Ada Buyer", "line1": "12 Long Road", "line2": None, "city": "Leeds", "postal_code": "LS1 1AA", "country": "GB"}},
        "order_shipped": {"store": identity, "order": {"number": 1042, "tracking_number": "TRK-9F2C-11A", "tracking_url": "https://example.com/track/TRK-9F2C-11A", "status_url": "/orders/1042/sample-token"}},
        "order_refunded": {"store": identity, "order": {"number": 1042}, "refund": {"amount": money(9993), "reason": "Arrived damaged"}},
        "abandoned_cart": {"store": identity, "total": money(10498), "value": money(10498), "recover_url": "/cart/recover/sample-token", "items": items},
        "staff_invitation": {"store": identity, "role": "manager", "accept_url": "/invitations/sample-token", "invited_by": "Ada Owner"},
        "store_welcome": {"store": identity, "name": "Ada Owner"},
        "password_reset": {"name": "Ada Owner", "reset_url": "/reset-password/sample-token"},
        "help_reply": {"name": "Ada Buyer", "reply": "Thanks for asking — it ships tomorrow.", "question": "When will my order ship?", "ticket_url": "/help/sample-token", "store": identity},
    }
    return samples[template]


@endpoint("mail.preview", "GET", "/dash/{store}/developers/mail", area="developers", permission="settings.read",
          summary="Render any email template against a sample context, without placing an order to trigger one", original="GET /developers/mail")
async def mail_preview(c: Ctx):
    await c.dashboard("settings.read")
    which = c.arg("template") or "order_receipt"
    if which not in mail_templates.TEMPLATES:
        which = "order_receipt"
    settings = await c.settings()
    html, text, subject = mail_templates.render(which, _sample_mail_context(which, c.store), settings)
    return {"templates": [{"key": k, "label": k.replace("_", " ").title()} for k in sorted(mail_templates.TEMPLATES)], "active": which, "subject": subject, "html": html, "text": text,
            "from_address": f"{settings.mail_from_name} <{settings.mail_from}>"}
