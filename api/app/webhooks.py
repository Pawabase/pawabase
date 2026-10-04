"""Outbound webhook delivery and inbound webhook verification.

Outbound deliveries are signed the way most providers sign theirs::

    Pawabase-Signature: t=1700000000,v1=<hex HMAC-SHA256 of "t.body">

so receivers can check both authenticity and freshness. Each delivery is a
queued job (Sillo work), retried with backoff, with every outcome recorded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from app.platform import Platform
from app.state import EnvironmentState
from database.models import WebhookDelivery

SIGNATURE_HEADER = "Pawabase-Signature"
TOLERANCE_SECONDS = 300


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> str:
    timestamp = int(timestamp or time.time())
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(
    secret: str, body: bytes, header: str, *, tolerance: int = TOLERANCE_SECONDS
) -> bool:
    """Check a ``t=…,v1=…`` signature (the format Pawabase sends)."""
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(time.time() - timestamp) > tolerance:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, parts.get("v1", ""))


def verify_plain_hmac(secret: str, body: bytes, signature: str, *, algorithm: str = "sha256") -> bool:
    """Check a bare hex (optionally algorithm-prefixed) HMAC of the body.

    Providers commonly use either HMAC-SHA256 (GitHub and Shopify-style
    callbacks) or HMAC-SHA512 (Paystack). The caller selects the algorithm
    from the persisted inbound-hook verification mode; it is never inferred
    from an untrusted request header.
    """
    if algorithm not in {"sha256", "sha512"}:
        raise ValueError(f"unsupported HMAC algorithm {algorithm!r}")
    prefix = f"{algorithm}="
    signature = signature[len(prefix):] if signature.startswith(prefix) else signature
    expected = hmac.new(secret.encode(), body, getattr(hashlib, algorithm)).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def endpoint_matches(events: list[str], name: str) -> bool:
    import fnmatch

    return any(fnmatch.fnmatchcase(name, pattern) for pattern in events or ["*"])


async def queue_deliveries(
    platform: Platform, state: EnvironmentState, *, event_id: str, event: str, payload: Any
) -> int:
    """Queue a delivery to every enabled endpoint subscribed to *event*."""
    from app.jobs.webhooks import DeliverWebhookJob

    queued = 0
    for endpoint in state.webhooks:
        if not endpoint.enabled or not endpoint_matches(endpoint.events, event):
            continue
        delivery = await WebhookDelivery.create(
            endpoint=endpoint, event_id=event_id, event=event, payload=payload, status="pending"
        )
        await platform.dispatch(
            DeliverWebhookJob,
            env=state.env_name,
            target=endpoint.name,
            source="webhook",
            delivery_id=delivery.id,
            release_id=state.release_id,
            api_version=state.api_version,
        )
        queued += 1
    return queued


def delivery_body(event_id: str, event: str, payload: Any, env: str) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "event": event,
            "env": env,
            "data": payload,
            "sent_at": int(time.time()),
        },
        default=str,
        separators=(",", ":"),
    ).encode()
