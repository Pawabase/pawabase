"""Stock, and the movements that explain it.

``product_variants.stock`` is never assigned outside this module. Every change goes through
:func:`adjust`, which writes an ``inventory_movements`` row and updates the running total in
one transaction, so the history and the balance cannot disagree.

Reservations are the other half. Between "checkout started" and "payment confirmed" the
stock is neither sold nor available; modelling that as a separate ``reserved`` column means
an abandoned checkout restores availability without inventing a compensating sale.

    available = stock - reserved

The window closes with :func:`commit_reservation` (paid) or :func:`release_reservation`
(abandoned or failed). The conditional ``UPDATE`` in :func:`reserve` is the whole safety
property: the ``WHERE`` re-checks availability inside the statement, so two checkouts racing
for the last unit produce one success and one :class:`InsufficientStock`.
"""

from __future__ import annotations

from typing import Any

from .. import q
from ..events import emit


class InsufficientStock(Exception):
    """Not enough of something. Carries what *was* available so the shop can say "only 2 left"."""

    def __init__(self, variant: q.Row | dict, requested: int, available: int) -> None:
        variant = q.Row(variant)
        super().__init__(f"Only {available} of {variant.sku or variant.title} available ({requested} requested).")
        self.variant = variant
        self.requested = requested
        self.available = available


async def adjust(
    c: Any,
    *,
    store: q.Row,
    variant: q.Row,
    delta: int,
    reason: str,
    note: str | None = None,
    reference_type: str | None = None,
    reference_id: Any = None,
    actor_id: str | None = None,
    allow_negative: bool = False,
    db: Any = None,
) -> q.Row:
    """Change one variant's stock by *delta*, and record why. Atomic."""
    async with c.scope(db) as session:
        fresh = await q.get(session, "product_variants", variant.pk)
        if fresh is None:
            raise InsufficientStock(variant, abs(delta), 0)
        movement: dict[str, Any] = {
            "store_id": store.pk,
            "variant_id": fresh.pk,
            "delta": delta,
            "reason": reason,
            "note": note,
            "reference_type": reference_type,
            "reference_id": str(reference_id) if reference_id is not None else None,
            "actor_id": actor_id,
        }
        if not fresh.track_inventory:
            moved = await q.insert(session, "inventory_movements", {**movement, "balance_after": fresh.stock})
        else:
            new_balance = fresh.stock + delta
            if new_balance < 0 and not (allow_negative or fresh.allow_backorder):
                raise InsufficientStock(fresh, abs(delta), max(fresh.stock - fresh.reserved, 0))
            await session.execute("UPDATE product_variants SET stock = stock + ? WHERE id = ?", [delta, fresh.pk])
            moved = await q.insert(session, "inventory_movements", {**movement, "balance_after": new_balance})

    await emit(c, "inventory.adjusted", store=store, variant=fresh, movement=moved, delta=delta,
               sku=fresh.sku or fresh.title, actor=actor_id)
    await _maybe_warn_low_stock(c, store, fresh.pk)
    return moved


async def reserve(c: Any, *, store: q.Row, variant_id: int, quantity: int, db: Any = None) -> None:
    """Hold stock for a checkout in progress (atomic conditional update)."""
    db = db or await c.db()
    variant = await q.get(db, "product_variants", variant_id)
    if variant is None:
        raise InsufficientStock(q.Row(title="Unknown item"), quantity, 0)
    if not variant.track_inventory or variant.allow_backorder:
        return
    updated = await db.execute(
        "UPDATE product_variants SET reserved = reserved + ? WHERE id = ? AND stock - reserved >= ?",
        [quantity, variant_id, quantity],
    )
    if not updated:
        fresh = await q.get(db, "product_variants", variant_id)
        raise InsufficientStock(fresh or variant, quantity, (fresh or variant).available or 0)


async def release_reservation(c: Any, *, variant_id: int, quantity: int, db: Any = None) -> None:
    """Give a held reservation back without touching stock. Clamped at zero, so a release that ran twice cannot go negative."""
    db = db or await c.db()
    variant = await q.get(db, "product_variants", variant_id)
    if variant is None or not variant.track_inventory:
        return
    await db.execute(
        "UPDATE product_variants SET reserved = CASE WHEN reserved >= ? THEN reserved - ? ELSE 0 END WHERE id = ?",
        [quantity, quantity, variant_id],
    )


async def commit_reservation(c: Any, *, store: q.Row, variant_id: int, quantity: int, order_id: int | None = None) -> None:
    """Turn a reservation into a sale: reserved down and stock down together."""
    await release_reservation(c, variant_id=variant_id, quantity=quantity)
    db = await c.db()
    variant = await q.get(db, "product_variants", variant_id)
    if variant is None:
        return
    await adjust(c, store=store, variant=variant, delta=-quantity, reason="sold",
                 reference_type="order", reference_id=order_id, allow_negative=True)


async def check_availability(c: Any, items: list[tuple[int, int]]) -> None:
    """Assert every ``(variant_id, quantity)`` can be fulfilled (a courtesy; ``reserve`` is the guarantee)."""
    db = await c.db()
    for variant_id, quantity in items:
        variant = await q.get(db, "product_variants", variant_id)
        if variant is None:
            raise InsufficientStock(q.Row(title="Unknown item"), quantity, 0)
        if variant.available < quantity:
            raise InsufficientStock(variant, quantity, variant.available)


async def _maybe_warn_low_stock(c: Any, store: q.Row, variant_id: int) -> None:
    """Announce the crossing below the threshold, once (not on every later sale)."""
    db = await c.db()
    variant = await q.get(db, "product_variants", variant_id)
    if variant is None or not variant.track_inventory or variant.available > variant.low_stock_threshold:
        return
    previous = await q.find(db, "inventory_movements", {"variant_id": variant_id}, order="id DESC", limit=2)
    if len(previous) > 1 and previous[1].balance_after <= variant.low_stock_threshold:
        return
    product = await q.get(db, "products", variant.product_id)
    await emit(c, "inventory.out" if variant.available <= 0 else "inventory.low", store=store, variant=variant, product=product)
