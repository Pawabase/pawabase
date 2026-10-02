"""Background work: everything slow, unreliable or merely unimportant to the request.

A merchant's webhook endpoint, an SMTP server, a 4000px JPEG and a ninety-day aggregate all have one thing in common: none of them should be able to hold up
an order. These are Pawabase functions with a ``service`` policy (no client can call them), reached by a schedule, an event subscription, or a handler
that queued them with ``c.dispatch``.

**Idempotent**, every one. The queue is at-least-once, so a job that ran twice must have the effect of one: the analytics roll-up recomputes rather than
increments, the image pipeline writes to a content-addressed key, the delivery worker claims a row before sending, the export claims its row first.

The original gave each job a queue (``media``, ``mail``, ``webhooks``, ``payments``, ``analytics``, ``exports``) so one slow class of work could not starve
the others; Pawabase's worker pool is shared, and ``timeout`` is the per-job bound.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sell4me_kit.listeners  # noqa: F401
from sell4me_kit import mail_templates, media, q
from sell4me_kit.context import Ctx
from sell4me_kit.endpoints import job
from sell4me_kit.events import emit
from sell4me_kit.images import ImageError, process_upload
from sell4me_kit.money import Money
from sell4me_kit.payments import get_provider
from sell4me_kit.services import analytics, campaigns, exports, inventory, payments, payouts, segments, webhooks_in, webhooks_out
from sell4me_kit.services import carts as cart_service

log = logging.getLogger("sell4me.jobs")


# ── mail ─────────────────────────────────────────────────────────────────

@job("mail.send", "Send one templated email. Nothing in the application sends inline: a slow or down SMTP server must not be able to fail the order that triggered the receipt", timeout=60)
async def mail_send(c: Ctx):
    data = c.input
    html, text, subject = mail_templates.render(data["template"], data.get("context") or {}, await c.settings())
    await c.runtime.send_mail([data["to"]], data.get("subject") or subject, html=html, text=text)
    return {"sent": True, "to": data["to"], "template": data["template"]}


# ── media ────────────────────────────────────────────────────────────────

@job("image.process", "Turn an uploaded image into its derivatives (sizes, a blur placeholder, the dominant colour). Written beside the original under the same hash directory, so deletion is a prefix and a rerun overwrites identical bytes", timeout=300)
async def image_process(c: Ctx):
    db = await c.db()
    image = await q.get(db, "product_images", c.input["image_id"])
    if image is None or not image.storage_key:
        return {"skipped": "no such image"}
    if image.variants:
        return {"skipped": "already processed"}
    data = await media.read(c, image.storage_key)
    try:
        processed = await process_upload(data)
    except ImageError as error:
        log.warning("image %s could not be processed: %s", image.pk, error)
        return {"skipped": str(error)}
    settings = await c.settings()
    folder = image.storage_key.rsplit("/", 1)[0]
    variants: dict[str, str] = {}
    for name, (payload, content_type, _, _) in processed.variants.items():
        key = f"{folder}/{name}.{content_type.rsplit('/', 1)[-1].replace('jpeg', 'jpg')}"
        await media.write(c, key, payload, content_type=content_type)
        variants[name] = media.media_url(settings, key)
    await q.update(db, "product_images", image.pk, {"variants": variants, "width": processed.width, "height": processed.height, "placeholder": processed.placeholder,
                                                    "dominant_color": processed.dominant, "processed_at": datetime.now(UTC)})
    return {"variants": sorted(variants)}


# ── merchant webhooks ────────────────────────────────────────────────────

@job("webhooks.deliver", "Deliver one queued webhook attempt. The retry schedule lives in the delivery row's next_attempt_at, not in the queue: a merchant debugging their endpoint needs to see six attempts over two hours with their status codes", timeout=30)
async def webhook_deliver(c: Ctx):
    db = await c.db()
    delivery = await q.get(db, "webhook_deliveries", c.input["delivery_id"])
    if delivery is None or delivery.status not in ("pending", "sending"):
        return {"skipped": True}
    await webhooks_out.attempt(c, delivery)
    return {"attempted": delivery.pk}


@job("webhooks.sweep", "Send every delivery whose retry time has come. Claiming happens inside deliver_pending, so two sweeps overlapping produce one delivery, not two", timeout=120)
async def webhooks_sweep(c: Ctx):
    return {"attempted": await webhooks_out.deliver_pending(c, limit=100)}


# ── commerce ─────────────────────────────────────────────────────────────

@job("carts.sweep_abandoned", "Close out checkouts that were given up on. Two things happen together and must not be separated: the stock reservation is released and the abandonment is recorded (releasing without recording loses the recovery; recording without releasing leaves stock held by a shopper who left)", timeout=300)
async def carts_sweep(c: Ctx):
    db = await c.db()
    cutoff = datetime.now(UTC) - timedelta(minutes=cart_service.ABANDON_AFTER_MINUTES)
    abandoned = 0
    for cart in await q.find(db, "carts", {"status": "checkout", "last_activity_at": q.lt(cutoff)}, limit=200):
        try:
            items = await db.fetch("SELECT ci.variant_id, ci.quantity, v.price_minor FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id "
                                   "WHERE ci.cart_id = ? AND ci.deleted_at IS NULL", [cart.pk])
            # Claim the cart first: a second sweep finds it already abandoned and releases nothing twice.
            if not await db.execute("UPDATE carts SET status = 'abandoned', updated_at = ? WHERE id = ? AND status = 'checkout'", [datetime.now(UTC), cart.pk]):
                continue
            for item in items:
                await inventory.release_reservation(c, variant_id=item["variant_id"], quantity=item["quantity"])
            record = None
            if items and not await q.exists(db, "abandoned_carts", {"cart_id": cart.pk}):
                record = await q.insert(db, "abandoned_carts", {"store_id": cart.store_id, "cart_id": cart.pk, "customer_id": cart.customer_id, "email": cart.email,
                                                                "value_minor": sum(i["price_minor"] * i["quantity"] for i in items), "currency": cart.currency,
                                                                "item_count": sum(i["quantity"] for i in items), "recovery_status": "pending", "recovery_token": secrets.token_urlsafe(24)})
            if record is not None:
                store = await q.get(db, "stores", cart.store_id)
                await emit(c, "cart.abandoned", store=store, cart=cart, record=record)
            abandoned += 1
        except Exception:  # noqa: BLE001
            log.exception("abandoning cart %s failed", cart.pk)
    return {"abandoned": abandoned}


@job("carts.recover_email", "Email a shopper the link back to their basket", timeout=60)
async def carts_recover_email(c: Ctx):
    db = await c.db()
    record = await q.get(db, "abandoned_carts", c.input["record_id"])
    if record is None or not record.email or record.recovery_status != "pending":
        return {"skipped": True}
    store = await q.get(db, "stores", record.store_id)
    items = await db.fetch("SELECT ci.quantity, v.title AS variant, p.title FROM cart_items ci JOIN product_variants v ON v.id = ci.variant_id JOIN products p ON p.id = v.product_id "
                           "WHERE ci.cart_id = ? AND ci.deleted_at IS NULL LIMIT 4", [record.cart_id])
    # Claim before sending: a retry after a slow send must not email the shopper twice.
    if not await db.execute("UPDATE abandoned_carts SET recovery_status = 'notified', notified_at = ? WHERE id = ? AND recovery_status = 'pending'", [datetime.now(UTC), record.pk]):
        return {"skipped": True}
    await c.dispatch("mail.send", {"template": "abandoned_cart", "to": record.email, "subject": f"You left something at {store.name}", "context": {
        "store": {"name": store.name, "slug": store.slug}, "value": Money(record.value_minor, record.currency).format(), "recover_url": f"/cart/recover/{record.recovery_token}",
        "items": [{"title": i["title"], "variant": i["variant"], "quantity": i["quantity"], "image": None} for i in items]}})
    return {"notified": record.pk}


@job("payouts.initiate", "Pay a merchant their share of one order, once it is confirmed. Dispatched by the order.paid listener rather than called inline: a Paystack transfer is one more network call the shopper completing checkout should not wait on. Idempotent: an order that already has a payout row never gets a second transfer", timeout=60)
async def payouts_initiate(c: Ctx):
    order = await q.get(await c.db(), "orders", c.input["order_id"])
    if order is None:
        return {"skipped": True}
    try:
        payout = await payouts.initiate_payout_for_order(c, order)
    except payouts.PayoutError as error:
        log.warning("could not initiate payout for order %s: %s", order.pk, error)
        return {"error": str(error)}
    return {"payout": payout.pk if payout else None, "status": payout.status if payout else None}


@job("payments.reconcile", "Ask the provider about payments a webhook never resolved. Webhooks get lost (a deploy during delivery, an endpoint briefly failing, a provider outage); without this an order the customer genuinely paid for sits pending until someone notices", timeout=180)
async def payments_reconcile(c: Ctx):
    db = await c.db()
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    settled = 0
    for payment in await q.find(db, "payments", {"status": q.in_(["pending", "processing"]), "created_at": q.lt(cutoff)}, limit=100):
        try:
            store = await q.get(db, "stores", payment.store_id)
            provider, credentials, _ = await payments.credentials_for(c, store, payment.provider)
            result = await provider.verify(credentials=credentials, reference=payment.reference)
            # settle is idempotent: a payment a webhook resolved between the query and this call is a no-op.
            await payments.settle(c, store=store, provider_key=payment.provider, result=result)
            settled += 1
        except Exception:  # noqa: BLE001
            log.warning("could not reconcile payment %s", payment.reference)
    return {"checked": settled}


# ── analytics ────────────────────────────────────────────────────────────

@job("analytics.aggregate_day", "Recompute one store-day of analytics. Idempotent by construction: it computes totals and *writes* them, never increments", timeout=300)
async def analytics_day(c: Ctx):
    db = await c.db()
    store = await q.get(db, "stores", c.input["store_id"])
    if store is None:
        return {"skipped": True}
    await analytics.aggregate_day(db, store, date.fromisoformat(c.input["day"]))
    return {"store": store.pk, "day": c.input["day"]}


@job("analytics.aggregate_yesterday", "Roll up the day that just closed for every store: yesterday *and* the day before, because a payment webhook arriving after midnight belongs to the earlier day and recomputing it is free", timeout=600)
async def analytics_yesterday(c: Ctx):
    db, today, count = await c.db(), datetime.now(UTC).date(), 0
    for store in await q.find(db, "stores"):
        for offset in (1, 2):
            await c.dispatch("analytics.aggregate_day", {"store_id": store.pk, "day": (today - timedelta(days=offset)).isoformat()})
            count += 1
    return {"queued": count}


@job("segments.refresh_all", "Recompute every segment's cached size (a cache, shown with its timestamp)", timeout=300)
async def segments_refresh(c: Ctx):
    db = await c.db()
    for store in await q.find(db, "stores"):
        try:
            await segments.refresh_counts(db, store)
        except Exception:  # noqa: BLE001
            log.exception("refreshing segments for store %s failed", store.pk)
    return {"ok": True}


@job("campaigns.refresh", "Recompute one campaign's attributed orders and revenue", timeout=120)
async def campaign_refresh(c: Ctx):
    db = await c.db()
    campaign = await q.get(db, "campaigns", c.input["campaign_id"])
    if campaign is not None:
        await campaigns.refresh_results(db, campaign)
    return {"campaign": c.input["campaign_id"]}


@job("campaigns.advance", "Start and finish campaigns whose window has arrived or passed, and refresh the results of those running", timeout=300)
async def campaigns_advance(c: Ctx):
    started, completed = await campaigns.advance_scheduled(await c.db())
    return {"started": started, "completed": completed}


# ── exports ──────────────────────────────────────────────────────────────

@job("exports.run", "Produce one requested export (CSV, or the Excel workbook for the complete store export). The job claims its row first, so two workers produce one file", timeout=600)
async def export_run(c: Ctx):
    db = await c.db()
    job_row = await q.get(db, "export_jobs", c.input["export_id"])
    if job_row is None:
        return {"skipped": True}
    try:
        done = await exports.run(c, job_row)
    except Exception as error:  # noqa: BLE001
        log.exception("export %s failed", job_row.pk)
        await q.update(db, "export_jobs", job_row.pk, {"status": "failed", "error": str(error)[:500]})
        return {"failed": str(error)[:200]}
    return {"status": done.status, "rows": done.row_count}


@job("exports.run_queued", "Run exports that were queued but whose job was lost (a restart between the request and the worker)", timeout=120)
async def export_run_queued(c: Ctx):
    db = await c.db()
    stale = await q.find(db, "export_jobs", {"status": "queued", "created_at": q.lt(datetime.now(UTC) - timedelta(minutes=2))}, limit=20)
    for row in stale:
        await c.dispatch("exports.run", {"export_id": row.pk})
    return {"requeued": len(stale)}


@job("exports.expire", "Delete finished export files past their retention window. Exports hold customer names, emails and addresses: a file nobody downloaded should not sit in storage indefinitely", timeout=300)
async def export_expire(c: Ctx):
    db = await c.db()
    expired = await q.find(db, "export_jobs", {"status": "complete", "expires_at": q.lt(datetime.now(UTC))}, limit=50)
    for row in expired:
        try:
            await exports.discard(c, row)
        except Exception:  # noqa: BLE001
            log.exception("expiring export %s failed", row.pk)
    return {"expired": len(expired)}


# ── inbound payment webhooks ─────────────────────────────────────────────

async def _store_for(db: Any, provider_key: str, body: dict[str, Any], reference: str | None) -> q.Row | None:
    """Which merchant a delivery belongs to. The original put the slug in the URL; Pawabase's inbound hook is one URL, so the delivery has to say.

    In order: the store slug we put in the charge's metadata; the payment, refund or payout the provider's reference names. Anything else is not ours
    (another integration on the same account) and is ignored, not failed.
    """
    data = body.get("data") or {}
    slug = (data.get("metadata") or {}).get("store") if isinstance(data.get("metadata"), dict) else None
    if slug:
        store = await q.first(db, "stores", {"slug": str(slug)})
        if store is not None:
            return store
    refs = [r for r in (reference, data.get("reference"), data.get("transaction_reference"), data.get("transfer_code")) if r]
    for ref in refs:
        for table, column in (("payments", "provider_reference"), ("payments", "reference"), ("refunds", "provider_reference"), ("payouts", "provider_reference")):
            row = await q.first(db, table, {column: str(ref)})
            if row is not None:
                return await q.get(db, "stores", row.store_id)
    return None


@job("payments.webhook_event", "Handle one verified provider delivery exactly once. Pawabase's inbound hook has already checked the signature (HMAC-SHA512 keyed by the platform's Paystack secret) and parsed the body; this records the delivery under a unique (provider, event_id) key and runs an idempotent handler. Anything stored answers success, because a provider that gets an error retries for days", timeout=120)
async def payment_webhook_event(c: Ctx):
    envelope = ((c.input.get("event") or {}).get("payload")) or {}
    body, hook = envelope.get("body") or {}, envelope.get("hook") or "paystack"
    provider_key = {"paystack": "paystack", "stripe": "stripe", "sandbox": "sandbox"}.get(hook, "paystack")
    provider = get_provider(provider_key)
    if not hasattr(provider, "normalise"):
        return {"received": True, "handled": False, "reason": f"{provider_key} deliveries are not accepted on this hook"}
    event = provider.normalise(body)
    store = await _store_for(await c.db(), provider_key, body, event.provider_reference)
    if store is None:
        return {"received": True, "handled": False, "reason": "no store for this delivery"}
    return await webhooks_in.handle(c, store=store, provider_key=provider_key, event=event)
