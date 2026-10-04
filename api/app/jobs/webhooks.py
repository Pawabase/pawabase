"""Delivering one event to one outbound webhook endpoint."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.jobs.base import PawabaseJob
from app.outbound import OutboundRefused, request_once
from app.webhooks import SIGNATURE_HEADER, delivery_body, sign_payload
from database.models import WebhookDelivery


class DeliveryFailed(RuntimeError):
    pass


class DeliverWebhookJob(PawabaseJob):
    """POSTs a signed event, retrying with backoff until it is accepted."""

    queue = "webhooks"
    tries = 5
    backoff = 2
    timeout = 30.0

    async def perform(self) -> Any:
        from app.outbound import check_target

        platform, _state = await self.environment()
        delivery = (
            await WebhookDelivery.filter(id=self.params["delivery_id"])
            .prefetch_related("endpoint")
            .first()
        )
        if delivery is None:
            return {"skipped": "delivery no longer exists"}
        endpoint = delivery.endpoint
        secret = platform.box.open(endpoint.secret_ciphertext)
        body = delivery_body(
            delivery.event_id, delivery.event, delivery.payload, self.env
        )
        headers = {
            **{str(k): str(v) for k, v in (endpoint.headers or {}).items()},
            "content-type": "application/json",
            SIGNATURE_HEADER: sign_payload(secret, body),
            "Pawabase-Event": delivery.event,
            "Pawabase-Delivery": str(delivery.id),
        }
        delivery.attempts += 1
        started = time.perf_counter()
        try:
            check_target(
                endpoint.url, allow_private=platform.settings.app_env in ("local", "testing")
            )
            response = await request_once(
                platform.outbound, "POST", endpoint.url, headers=headers, content=body, timeout=15.0
            )
        except OutboundRefused as exc:
            delivery.status, delivery.error = "failed", str(exc)
            await delivery.save()
            return {"refused": str(exc)}
        except Exception as exc:
            delivery.status, delivery.error = "retrying", f"{type(exc).__name__}: {exc}"
            delivery.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            await delivery.save()
            raise DeliveryFailed(delivery.error) from exc
        delivery.duration_ms = round((time.perf_counter() - started) * 1000, 3)
        delivery.response_status = response["status"]
        delivery.response_body = str(response["body"])[:2000]
        if 200 <= response["status"] < 300:
            delivery.status, delivery.error = "delivered", None
            delivery.delivered_at = datetime.now(UTC)
            await delivery.save()
            return {"status": response["status"]}
        delivery.status, delivery.error = "retrying", f"HTTP {response['status']}"
        await delivery.save()
        raise DeliveryFailed(f"endpoint answered {response['status']}")

    async def failed(self, exception: Exception) -> None:
        await WebhookDelivery.filter(id=self.params["delivery_id"]).update(status="failed")
