"""Devices: which browsers and apps a person signs in from, which are new, and which may skip the second factor.

A device is named by an id the client keeps: the ``pb_device`` cookie on the hosted pages, or the ``x-device-id`` header from an app.
A client that sends neither is still *recorded*, under a fingerprint of its user agent, but never trusted: only a random id the
client holds can stand in for "this is the same device as before".
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.db.models import AuthUser, Device
from pawabase_server.identity import emails as auth_emails
from pawabase_server.identity.accounts import Accounts
from pawabase_server.identity.clientinfo import client_info
from pawabase_server.identity.environment import AuthConfig

COOKIE = "pb_device"
HEADER = "x-device-id"
VALID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def new_id() -> str:
    return secrets.token_urlsafe(24)


def identify(ctx: Any) -> tuple[str, bool]:
    """The device a request comes from, and whether that is a durable id the client chose (as opposed to a user-agent fingerprint)."""
    supplied = ctx.headers.get(HEADER) or ""
    if VALID.match(supplied):
        return supplied, True
    cookie = (ctx.cookies or {}).get(COOKIE, "") if hasattr(ctx, "cookies") else ""
    if VALID.match(cookie):
        return cookie, True
    agent = ctx.headers.get("user-agent") or ""
    return "ua:" + hashlib.sha256(agent.encode()).hexdigest()[:24], False


def describe(agent: str) -> str:
    """A readable name for a user agent: "Chrome on macOS"."""
    browser = next(
        (
            name
            for pattern, name in (
                (r"Edg/", "Edge"),
                (r"Firefox/", "Firefox"),
                (r"Chrome/", "Chrome"),
                (r"Safari/", "Safari"),
                (r"curl/", "curl"),
                (r"python|httpx|requests", "Script"),
            )
            if re.search(pattern, agent or "", re.I)
        ),
        None,
    )
    system = next(
        (
            name
            for pattern, name in (
                (r"Windows", "Windows"),
                (r"Android", "Android"),
                (r"iPhone|iPad", "iOS"),
                (r"Mac OS X", "macOS"),
                (r"Linux", "Linux"),
            )
            if re.search(pattern, agent or "")
        ),
        None,
    )
    return " on ".join(part for part in (browser, system) if part) or "Unknown device"


async def record(
    accounts: Accounts,
    config: AuthConfig,
    user: AuthUser,
    ctx: Any,
    *,
    key: str | None = None,
) -> tuple[Device | None, bool]:
    """Note that *user* signed in from this device. Returns ``(device, is_new)``; a new device may send the person an email."""
    policy = config.policy("devices")
    if not policy["track"]:
        return None, False
    ip, agent = client_info(ctx) if ctx is not None else (None, None)
    if key is None:
        key, _ = identify(ctx) if ctx is not None else (new_id(), True)
    now = datetime.now(UTC)
    device = await Device.filter(user=user, key=key).first()
    if device is not None and device.revoked_at is None:
        device.last_seen_at, device.last_ip, device.sign_ins = now, ip, device.sign_ins + 1
        await device.save(update_fields=["last_seen_at", "last_ip", "sign_ins"])
        return device, False
    known_before = await Device.filter(user=user, revoked_at=None).exists()
    if device is not None:  # a device the person removed, signing in again
        device.revoked_at, device.trusted_until = None, None
        device.last_seen_at, device.last_ip, device.sign_ins = now, ip, device.sign_ins + 1
        await device.save()
    else:
        device = await Device.create(
            user=user,
            env=config.env,
            key=key,
            label=describe(agent or ""),
            user_agent=agent,
            first_ip=ip,
            last_ip=ip,
            last_seen_at=now,
        )
    limit = policy["max_per_user"]
    if limit:
        extra = (
            await Device.filter(user=user, revoked_at=None).order_by("-last_seen_at").offset(limit)
        )
        for old in extra:
            old.revoked_at, old.trusted_until = now, None
            await old.save(update_fields=["revoked_at", "trusted_until"])
    if policy["notify_new"] and known_before:
        await auth_emails.send(
            accounts,
            config,
            "new_device",
            user.email,
            device=f"{device.label} ({ip or 'unknown address'})",
        )
    return device, True


async def is_trusted(config: AuthConfig, user: AuthUser, ctx: Any) -> bool:
    """Whether this device was remembered, so a second factor is not asked for again yet."""
    if not config.policy("devices")["remember_days"] or ctx is None:
        return False
    key, durable = identify(ctx)
    if not durable:
        return False
    return await Device.filter(
        user=user, key=key, revoked_at=None, trusted_until__gt=datetime.now(UTC)
    ).exists()


async def trust(config: AuthConfig, user: AuthUser, ctx: Any, *, key: str | None = None) -> bool:
    """Remember this device for the directory's number of days. Returns whether it was (a fingerprint-only device cannot be)."""
    days = config.policy("devices")["remember_days"]
    durable = True
    if key is None:
        key, durable = identify(ctx)
    if not days or not durable or key.startswith("ua:"):
        return False
    updated = await Device.filter(user=user, key=key, revoked_at=None).update(
        trusted_until=datetime.now(UTC) + timedelta(days=days)
    )
    return bool(updated)


def view(device: Device, *, current: str | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "id": str(device.id),
        "label": device.label,
        "user_agent": device.user_agent,
        "first_ip": device.first_ip,
        "last_ip": device.last_ip,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "first_seen_at": device.created_at.isoformat() if device.created_at else None,
        "sign_ins": device.sign_ins,
        "trusted": bool(device.trusted_until and device.trusted_until > now),
        "trusted_until": device.trusted_until.isoformat() if device.trusted_until else None,
        "current": current is not None and device.key == current,
    }
