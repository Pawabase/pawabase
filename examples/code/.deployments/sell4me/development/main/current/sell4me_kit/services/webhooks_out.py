"""Outbound webhooks: telling a merchant's systems what happened.

``dispatch`` runs inside request handling, so it is cheap and never fails the thing that triggered it: it writes a
``webhook_deliveries`` row per subscriber and returns; a job does the HTTP. Deliveries are signed the way providers sign
theirs (HMAC-SHA256 over ``timestamp.body``, sent as ``X-Commerce-Signature``) so a receiver verifies us with code they
already have, and the signature covers the timestamp so a captured delivery cannot be replayed a week later. Retries are
exponential and scheduled by writing ``next_attempt_at`` rather than sleeping, so a restart loses nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import q

class DestinationNotAllowed(Exception):
    """A webhook URL that points inside the platform's own network."""


async def check_destination(url: str, *, allow_private: bool = False) -> None:
    """Refuse a URL whose host is a private, loopback, link-local or otherwise non-public address.

    A merchant chooses where deliveries go, and the platform's workers sit inside a network the merchant cannot reach: without this a webhook pointed at
    ``https://169.254.169.254/`` or an internal service turns every delivery into a request *from* that network (and its response body is stored and
    shown back to them). Checked when the endpoint is saved *and* again on every attempt, because DNS can change between the two.
    ``allow_private`` is for development and tests only.
    """
    if allow_private:
        return
    host = urlparse(url).hostname
    if not host:
        raise DestinationNotAllowed("That URL has no host.")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise DestinationNotAllowed(f"Could not resolve {host}.") from None
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise DestinationNotAllowed("That address is not on the public internet.")


BACKOFF_SECONDS = (0, 30, 120, 600, 1800, 3600)  # six attempts span about two hours
MAX_ATTEMPTS = len(BACKOFF_SECONDS)
FAILURE_LIMIT = 10  # consecutive failed *events* before an endpoint is presumed gone
TIMEOUT_SECONDS = 10.0


def sign(secret: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


async def dispatch(c: Any, store: q.Row, event: str, payload: dict[str, Any]) -> int:
    """Queue this event to every active subscriber. Swallows its own failures: it is called from the middle of ``mark_paid``."""
    try:
        db = await c.db()
        hooks = await q.find(db, "webhooks", {"store_id": store.pk, "is_active": True})
    except Exception:  # noqa: BLE001
        return 0
    event_id, queued = f"evt_{secrets.token_urlsafe(16)}", 0
    for hook in hooks:
        if event not in (hook.events or []):
            continue
        try:
            await q.insert(db, "webhook_deliveries", {"webhook_id": hook.pk, "store_id": store.pk, "event": event, "event_id": event_id,
                                                      "payload": payload, "status": "pending", "attempt": 1, "next_attempt_at": datetime.now(UTC)})
            queued += 1
        except Exception:  # noqa: BLE001
            continue
    return queued


async def deliver_pending(c: Any, limit: int = 50) -> int:
    """Send every delivery whose time has come. Claims by id (marked ``sending`` before the HTTP call) so two workers take disjoint sets."""
    db = await c.db()
    due = await q.find(db, "webhook_deliveries", {"status": "pending", "next_attempt_at": q.lte(datetime.now(UTC))}, order="id", limit=limit)
    sent = 0
    for delivery in due:
        if not await db.execute("UPDATE webhook_deliveries SET status = 'sending' WHERE id = ? AND status = 'pending'", [delivery.pk]):
            continue  # another worker took it
        await attempt(c, delivery)
        sent += 1
    return sent


async def attempt(c: Any, delivery: q.Row) -> None:
    db = await c.db()
    hook = await q.get(db, "webhooks", delivery.webhook_id)
    if hook is None:
        await q.update(db, "webhook_deliveries", delivery.pk, {"status": "failed", "error": "webhook was deleted"})
        return
    body = json.dumps({"id": delivery.event_id, "event": delivery.event, "created_at": datetime.now(UTC).isoformat(), "data": delivery.payload},
                      separators=(",", ":"), default=str).encode()
    timestamp = int(datetime.now(UTC).timestamp())
    headers = {"Content-Type": "application/json", "User-Agent": "SELL4ME-Webhooks/1.0", "X-Commerce-Event": delivery.event,
               "X-Commerce-Event-Id": delivery.event_id,  # stable across retries, so a receiver can deduplicate on it
               "X-Commerce-Signature": sign(hook.secret, timestamp, body), "X-Commerce-Attempt": str(delivery.attempt)}
    started = datetime.now(UTC)
    status_code, error, response_body = None, None, ""
    try:
        await check_destination(hook.url, allow_private=(await c.settings()).webhooks_allow_private)
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.post(hook.url, content=body, headers=headers)
        status_code, response_body = response.status_code, response.text[:2000]
    except DestinationNotAllowed as exc:
        error = str(exc)
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    update: dict[str, Any] = {"status_code": status_code, "response_body": response_body or None, "error": error, "duration_ms": duration_ms}
    if status_code is not None and 200 <= status_code < 300:
        now = datetime.now(UTC)
        await q.update(db, "webhook_deliveries", delivery.pk, {**update, "status": "delivered", "delivered_at": now, "next_attempt_at": None})
        await q.update(db, "webhooks", hook.pk, {"consecutive_failures": 0, "last_delivery_at": now, "last_status_code": status_code})
        return
    if delivery.attempt >= MAX_ATTEMPTS:
        await q.update(db, "webhook_deliveries", delivery.pk, {**update, "status": "failed", "next_attempt_at": None})
        await _record_failure(db, hook, status_code)
        return
    nxt = delivery.attempt + 1
    await q.update(db, "webhook_deliveries", delivery.pk, {**update, "status": "pending", "attempt": nxt,
                   "next_attempt_at": datetime.now(UTC) + timedelta(seconds=BACKOFF_SECONDS[min(nxt - 1, MAX_ATTEMPTS - 1)])})


async def _record_failure(db: Any, hook: q.Row, status_code: int | None) -> None:
    """Count a permanently failed event and disable a dead endpoint, loudly: a webhook that quietly stopped firing is worse than one that visibly failed."""
    failures = (hook.consecutive_failures or 0) + 1
    updates: dict[str, Any] = {"consecutive_failures": failures, "last_status_code": status_code, "last_delivery_at": datetime.now(UTC)}
    if failures >= FAILURE_LIMIT:
        updates.update(is_active=False, disabled_at=datetime.now(UTC))
        await q.insert(db, "notifications", {"store_id": hook.store_id, "kind": "webhook.disabled", "title": "A webhook endpoint was disabled",
                       "body": f"{hook.url} failed {failures} times in a row.", "url": "/developers/webhooks", "level": "critical",
                       "required_permission": "developers.read"})
    await q.update(db, "webhooks", hook.pk, updates)


async def test_webhook(c: Any, hook: q.Row) -> q.Row:
    """Send a synthetic event through the same delivery path as a real one (same signature, same headers)."""
    db = await c.db()
    delivery = await q.insert(db, "webhook_deliveries", {"webhook_id": hook.pk, "store_id": hook.store_id, "event": "webhook.test",
                              "event_id": f"evt_test_{secrets.token_urlsafe(10)}", "payload": {"message": "This is a test delivery from your commerce dashboard."},
                              "status": "sending", "attempt": 1})
    await attempt(c, delivery)
    return await q.get(db, "webhook_deliveries", delivery.pk)
