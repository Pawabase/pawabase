"""Delivering one event to one outbound webhook endpoint.

Each attempt is one job. A failed attempt schedules the next as a delayed job (see :class:`PawabaseJob`), following
:data:`RETRY_SCHEDULE` with jitter, up to the endpoint's ``max_attempts``: an endpoint that is down for an afternoon still
gets the event. While waiting, the delivery is ``retrying`` with its ``next_attempt_at``; after the last attempt it is
``failed`` and can be redelivered from Studio or the API.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from pawabase_server.automation.jobs.base import PawabaseJob
from pawabase_server.automation.webhooks import SIGNATURE_HEADER, delivery_body, sign_payload
from pawabase_server.core.retry import RetryPolicy
from pawabase_server.db.models import WebhookDelivery
from pawabase_server.infra.outbound import OutboundRefused, request_once

#: Seconds before attempts 2, 3, 4…: about 10 s, 1 min, 5 min, 30 min, 2 h, 6 h, 12 h, then daily. Ten attempts span ~2.5 days.
RETRY_SCHEDULE = (10, 60, 300, 1800, 7200, 21600, 43200, 86400, 86400)


class DeliveryFailed(RuntimeError):
    pass


class DeliverWebhookJob(PawabaseJob):
    """POSTs a signed event, retrying with backoff until it is accepted."""

    queue = "webhooks"
    tries = 5
    timeout = 30.0

    @classmethod
    def policy(cls) -> RetryPolicy:
        return RetryPolicy(cls.tries, schedule=RETRY_SCHEDULE)

    def retry_policy(self) -> RetryPolicy:
        """The endpoint's attempt limit (1–10, default 5), on the long webhook schedule."""
        return self.policy().with_attempts(
            min(int(self.params.get("max_attempts") or self.tries), 10)
        )

    async def retrying(self, next_attempt: int, delay: float, error: str) -> None:
        await WebhookDelivery.filter(id=self.params["delivery_id"]).update(
            status="retrying", next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay)
        )

    async def perform(self) -> Any:
        from pawabase_server.infra.outbound import check_target

        platform, _state = await self.environment()
        delivery = (
            await WebhookDelivery.filter(id=self.params["delivery_id"])
            .prefetch_related("endpoint")
            .first()
        )
        if delivery is None:
            return {"skipped": "delivery no longer exists"}
        endpoint = delivery.endpoint
        secret = platform.box.open(endpoint.secret_ciphertext, self.env)
        body = delivery_body(delivery.event_id, delivery.event, delivery.payload, self.env)
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
            delivery.next_attempt_at = None
            await delivery.save()
            return {"status": response["status"]}
        delivery.status, delivery.error = "retrying", f"HTTP {response['status']}"
        await delivery.save()
        raise DeliveryFailed(f"endpoint answered {response['status']}")

    async def failed(self, exception: Exception) -> None:
        await WebhookDelivery.filter(id=self.params["delivery_id"]).update(
            status="failed", next_attempt_at=None
        )
