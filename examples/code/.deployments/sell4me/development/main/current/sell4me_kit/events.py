"""The domain event bus.

Every meaningful thing that happens is announced here, and what should follow from it is a
listener (``listeners.py``) instead of more code inside the service that caused it.
``emit`` awaits its listeners, never raises, and logs a failing listener: the state change
that caused the event is already committed and must not be undone by a mailer being down.
Slow work (mail, webhooks, payouts, image processing) is queued as a job by the listener.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("sell4me.events")

#: Every event, and what it means. A closed catalogue: it is what the webhook subscription
#: screen renders and what a reviewer reads to understand the system.
EVENTS: dict[str, str] = {
    "product.created": "A product was created.",
    "product.updated": "A product's details changed.",
    "product.published": "A product became visible on the storefront.",
    "product.archived": "A product was archived.",
    "image.uploaded": "An image was uploaded and needs its derivatives.",
    "inventory.adjusted": "Stock changed, for any reason.",
    "inventory.low": "A variant crossed its low-stock threshold.",
    "inventory.out": "A variant reached zero available.",
    "cart.item_added": "Something was added to a basket.",
    "checkout.started": "A shopper began checkout and stock was reserved.",
    "cart.abandoned": "A checkout was given up on and its stock released.",
    "order.created": "An order was placed but not yet paid.",
    "order.paid": "An order's payment settled.",
    "order.fulfilled": "An order shipped.",
    "order.delivered": "An order arrived.",
    "order.cancelled": "An order was cancelled and its stock returned.",
    "payment.succeeded": "A charge settled with the provider.",
    "payment.failed": "A charge was declined or errored.",
    "refund.created": "Money was returned to a customer.",
    "payout.paid": "A provider settled funds to the merchant.",
    "customer.created": "A customer record was created.",
    "staff.invited": "Someone was invited to the store.",
    "staff.joined": "An invitation was accepted.",
    "store.created": "A store was created.",
    "store.launched": "A store opened to the public.",
    "provider.connected": "A payment provider was verified.",
    "domain.verified": "A custom domain passed its DNS check.",
}


@dataclass(slots=True)
class DomainEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __getattr__(self, key: str) -> Any:
        try:
            return self.payload[key]
        except KeyError:
            return None

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)

    @property
    def store(self) -> Any:
        return self.payload.get("store")


Listener = Callable[[Any, DomainEvent], Awaitable[None]]
_REGISTRY: dict[str, list[Listener]] = {}


def listener(*names: str) -> Callable[[Listener], Listener]:
    """Register ``async def(c, event)`` for *names*. A trailing ``*`` hears a whole family."""

    def register(fn: Listener) -> Listener:
        for name in names:
            _REGISTRY.setdefault(name, []).append(fn)
        return fn

    return register


def listeners_for(name: str) -> list[Listener]:
    found = list(_REGISTRY.get(name, ()))
    found.extend(_REGISTRY.get(f"{name.split('.', 1)[0]}.*", ()))
    found.extend(_REGISTRY.get("*", ()))
    return found


async def emit(c: Any, name: str, **payload: Any) -> DomainEvent:
    if name not in EVENTS:
        log.warning("emitted unknown event %r: add it to EVENTS", name)
    event = DomainEvent(name=name, payload=payload)
    for fn in listeners_for(name):
        try:
            await fn(c, event)
        except Exception:  # noqa: BLE001
            log.exception("listener %s failed on %s", getattr(fn, "__qualname__", fn), name)
    return event
