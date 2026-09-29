"""Build ``swiftline.blueprint.json`` for the SwiftLine logistics example.

The example is intentionally assembled from the resource and flow modules next
to this file.  The generated document is the artifact imported by Studio; no
application-specific server code is required.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from flows_matching import FLOWS as MATCHING_FLOWS  # noqa: E402
from flows_operations import FLOWS as OPERATIONS_FLOWS  # noqa: E402
from flows_trip import FLOWS as TRIP_FLOWS  # noqa: E402
from model import POLICIES, ROLES, SCHEMAS, TRANSFORMERS  # noqa: E402
from resources_a import RESOURCES as RESOURCES_A  # noqa: E402
from resources_b import RESOURCES as RESOURCES_B  # noqa: E402
from resources_c import RESOURCES as RESOURCES_C  # noqa: E402
from helpers import route  # noqa: E402


FLOWS = MATCHING_FLOWS + OPERATIONS_FLOWS + TRIP_FLOWS
RESOURCES = RESOURCES_A + RESOURCES_B + RESOURCES_C


def _routes():
    customer = ["Customer"]
    driver = ["Driver"]
    ops = ["Operations"]
    business = ["Business"]
    return [
        route("POST", "/estimate", "fare_estimate", "Price a ride or delivery before booking.", "customer_create", "fare_estimate", tags=customer, input_schema="EstimateInput", rate={"limit": 60, "window": 60}),
        route("POST", "/rides", "ride_create", "Request an immediate or scheduled ride.", "customer_create", "ride_create", tags=customer, input_schema="RideRequestInput", rate={"limit": 20, "window": 60}),
        route("POST", "/rides/{id}/cancel", "ride_cancel", "Cancel a ride with stage-aware fee handling.", "job_party", "ride_cancel", tags=customer, input_schema="CancelInput"),
        route("POST", "/deliveries", "delivery_create", "Create an immediate, scheduled, or multi-stop delivery.", "business_api", "delivery_create", tags=business, input_schema="DeliveryInput", rate={"limit": 30, "window": 60}),
        route("POST", "/deliveries/{id}/cancel", "delivery_cancel", "Cancel a delivery and release its assignment.", "job_party", "delivery_cancel", tags=customer, input_schema="CancelInput"),
        route("POST", "/deliveries/{id}/attempt", "delivery_attempt", "Record a failed delivery attempt and choose the next action.", "job_party", "delivery_attempt", tags=driver, input_schema="AttemptInput"),
        route("POST", "/deliveries/{id}/proof", "delivery_proof", "Submit recipient confirmation and proof of delivery.", "job_party", "delivery_proof", tags=driver, input_schema="ProofInput"),
        route("POST", "/driver/state", "driver_set_state", "Change driver availability through the lifecycle state machine.", "driver_role", "driver_set_state", tags=driver),
        route("POST", "/driver/location", "driver_location", "Ingest an idempotent, out-of-order-safe location ping.", "driver_role", "driver_location", tags=driver, input_schema="LocationPingInput", rate={"limit": 240, "window": 60}),
        route("POST", "/driver/offers/{id}/accept", "driver_accept", "Accept an offer using a unique claim race.", "driver_role", "driver_accept", tags=driver),
        route("POST", "/driver/offers/{id}/reject", "driver_reject", "Reject an offer and remain eligible for later rounds.", "driver_role", "driver_reject", tags=driver),
        route("POST", "/jobs/{type}/{id}/arrive", "driver_arrive", "Mark pickup arrival and start waiting-time accounting.", "driver_role", "driver_arrive", tags=driver),
        route("POST", "/rides/{id}/start", "trip_start", "Start a ride after pickup checks and waiting settlement.", "driver_role", "trip_start", tags=driver),
        route("POST", "/rides/{id}/stops/{stop}/arrive", "stop_arrive", "Arrive at an intermediate stop.", "driver_role", "stop_arrive", tags=driver),
        route("POST", "/rides/{id}/stops/{stop}/complete", "stop_complete", "Complete an intermediate stop and resume the trip.", "driver_role", "stop_complete", tags=driver),
        route("POST", "/rides/{id}/complete", "trip_complete", "Complete a ride, settle payment and record earnings.", "driver_role", "trip_complete", tags=driver),
        route("POST", "/jobs/{type}/{id}/chat", "chat_send", "Send a message between the job parties.", "job_party", "chat_send", tags=["Support"], input_schema="ChatInput", rate={"limit": 60, "window": 60}),
        route("GET", "/track/{token}", "share_track", "Read a token-gated public tracking snapshot.", "public", "share_track", tags=["Tracking"], rate={"limit": 60, "window": 60}),
        route("POST", "/ops/jobs/{type}/{id}/reassign", "request_rematch", "Release a driver and return the job to matching.", "ops_manage", "request_rematch", tags=ops, input_schema="ReassignInput"),
        route("POST", "/ops/approvals/{id}", "approval_decide", "Approve or reject a business job.", "business_managers", "approval_decide", tags=ops, input_schema="ApprovalDecisionInput"),
        route("POST", "/ops/disruptions", "disruption_set", "Enable or clear a service or area disruption.", "config_write", "disruption_set", tags=ops, input_schema="DisruptionInput"),
        route("POST", "/business/webhooks", "business_webhook_register", "Register a business event endpoint.", "business_managers", "business_webhook_register", tags=business, input_schema="BusinessWebhookInput"),
        route("POST", "/business/batches", "delivery_batch_create", "Accept a large delivery batch and isolate invalid rows.", "business_api", "delivery_batch_create", tags=business),
        route("POST", "/support/refunds", "refund_request", "Open a refund request for finance review.", "job_party", "refund_request", tags=["Finance"], input_schema="RefundRequestInput"),
        route("POST", "/actions/ratings", "rating_create", "Rate a completed ride or delivery.", "customer_create", "rating_create", tags=customer, input_schema="RatingInput"),
        route("POST", "/actions/incidents", "incident_create", "Report a safety, vehicle, or delivery incident.", "job_party", "incident_create", tags=["Support"], input_schema="IncidentInput"),
    ]


BUCKETS = [
    {"name": "driver-documents", "description": "Identity, licence, insurance and vehicle documents.", "public": False, "read_policy": "onboarding_manage", "write_policy": "driver_role", "accepts": ["image/*", "application/pdf"], "max_bytes": 15 * 1024 * 1024, "signed_uploads": True},
    {"name": "delivery-proof", "description": "Package photos, signatures and recipient evidence.", "public": False, "read_policy": "job_party", "write_policy": "driver_role", "accepts": ["image/*", "application/pdf"], "max_bytes": 20 * 1024 * 1024, "signed_uploads": True},
    {"name": "support-attachments", "description": "Attachments for support and incident cases.", "public": False, "read_policy": "ticket_party", "write_policy": "ticket_party", "accepts": ["image/*", "application/pdf", "text/plain"], "max_bytes": 20 * 1024 * 1024, "signed_uploads": True},
]

SUBSCRIPTIONS = [
    {"name": "job_live_updates", "description": "Publish job state changes to the operations stream.", "event": "*.updated", "target_type": "realtime", "target": "ops:live", "condition": {"role": ["ops_agent", "dispatcher", "admin", "super_admin"]}, "enabled": True},
    {"name": "notification_fanout", "description": "Deliver notification rows to realtime and configured mail channels.", "event": "notifications.created", "target_type": "realtime", "target": "user:{{ event.user_id }}", "condition": None, "enabled": True},
]

WEBHOOKS = [
    {"name": "business_delivery_events", "description": "Business delivery lifecycle events; disabled until an endpoint is configured.", "url": "https://business.example/hooks/swiftline", "events": ["delivery.created", "delivery.delivered", "delivery.failed", "delivery.cancelled"], "headers": {"X-Source": "swiftline"}, "enabled": False, "max_attempts": 8},
    {"name": "operations_warehouse", "description": "Operational metrics stream with retry tracking.", "url": "https://warehouse.example/hooks/swiftline", "events": ["ride.*", "delivery.*", "payment.*", "assignment.*"], "headers": {"X-Source": "swiftline"}, "enabled": False, "max_attempts": 6},
]

INBOUND = [
    {"slug": "payment-events", "name": "Payment provider events", "description": "Late authorizations, captures, failures and refunds from the payment provider.", "verification": "hmac-sha256", "signature_header": "x-signature", "target_type": "flow", "target": "payment_event", "enabled": True},
    {"slug": "driver-verification", "name": "Background verification events", "description": "Asynchronous identity and risk decisions from the verification provider.", "verification": "hmac-sha256", "signature_header": "x-signature", "target_type": "flow", "target": "verification_event", "enabled": True},
]

SCHEDULES = [
    {"name": "offer_expiry_sweep", "description": "Expire unanswered offers and advance matching.", "cron": None, "interval_seconds": 30, "target_type": "flow", "target": "matching_close", "payload": {"source": "scheduler"}, "enabled": True},
    {"name": "driver_document_reminders", "description": "Remind drivers and escalate expired documents.", "cron": "0 7 * * *", "interval_seconds": None, "target_type": "flow", "target": "document_expiry_sweep", "payload": {}, "enabled": True},
    {"name": "webhook_retry_worker", "description": "Retry failed business webhooks with exponential backoff.", "cron": None, "interval_seconds": 60, "target_type": "flow", "target": "webhook_retry", "payload": {}, "enabled": True},
    {"name": "daily_operations_rollup", "description": "Materialize market and service KPIs for operations dashboards.", "cron": "10 0 * * *", "interval_seconds": None, "target_type": "flow", "target": "daily_rollup", "payload": {}, "enabled": True},
]

SETTINGS = {
    "public_docs": True,
    "realtime": {"allow_client_publish": False, "channels": [
        {"pattern": "job:{{ auth.user_id }}:*", "subscribe": "channel_self", "publish": "deny", "presence": True, "history": 100},
        {"pattern": "driver:{{ auth.user_id }}", "subscribe": "channel_driver", "publish": "deny", "presence": True, "history": 100},
        {"pattern": "ops:live", "subscribe": "channel_ops", "publish": "deny", "presence": True, "history": 500},
        {"pattern": "fleet:{{ auth.org }}", "subscribe": "channel_fleet", "publish": "deny", "presence": True, "history": 100},
        {"pattern": "business:{{ auth.org }}", "subscribe": "channel_business", "publish": "deny", "presence": True, "history": 100},
    ]},
}

AUTH = {"signup_enabled": True, "require_email_verification": False, "password_policy": "basic", "password_min_length": 8, "access_ttl": 900, "refresh_ttl": 2592000, "magic_link_enabled": True, "mfa_enabled": True, "default_roles": ["customer"]}


def sample_data():
    data = defaultdict(list)
    data["markets"].append({"slug": "lagos", "name": "Lagos", "country": "NG", "currency": "NGN", "timezone": "Africa/Lagos", "center_lat": 6.5244, "center_lng": 3.3792, "km_per_deg_lng": 110.8, "active": True})
    data["zones"] += [
        {"slug": "victoria-island", "market": "lagos", "name": "Victoria Island", "kind": "premium", "min_lat": 6.42, "max_lat": 6.46, "min_lng": 3.40, "max_lng": 3.47, "priority": 10, "active": True},
        {"slug": "lagos-mainland", "market": "lagos", "name": "Lagos Mainland", "kind": "operational", "min_lat": 6.45, "max_lat": 6.60, "min_lng": 3.25, "max_lng": 3.42, "priority": 5, "active": True},
    ]
    data["services"] += [
        {"slug": "economy-ride", "name": "Economy Ride", "kind": "ride", "active": True, "vehicle_classes": ["compact_car", "standard_car"], "min_class_rank": 4, "max_class_rank": 6, "cancellation": {"free_before_assign": True, "fee_after_assign_minor": 50000, "fee_after_arrival_minor": 150000, "driver_comp_minor": 75000}, "waiting": {"free_seconds": 180, "per_minute_minor": 3000, "max_billable_seconds": 1800}, "compensation": {"driver_share_bps": 7500}, "platform_fee_bps": 2500},
        {"slug": "motorbike-delivery", "name": "Motorbike Delivery", "kind": "delivery", "active": True, "vehicle_classes": ["motorcycle"], "min_class_rank": 2, "max_class_rank": 2, "max_weight_kg": 25, "cancellation": {"free_before_assign": True, "fee_after_assign_minor": 50000, "fee_after_arrival_minor": 100000, "driver_comp_minor": 50000}, "waiting": {"free_seconds": 120, "per_minute_minor": 2000, "max_billable_seconds": 1200}, "compensation": {"driver_share_bps": 7000}, "platform_fee_bps": 3000},
    ]
    data["pricing_rules"].append({"market": "lagos", "service": "economy-ride", "base_minor": 50000, "minimum_minor": 150000, "per_km_minor": 12000, "per_minute_minor": 3000, "stop_fee_minor": 25000, "schedule_fee_minor": 10000, "priority_fee_minor": 0, "tax_bps": 750, "surge_t1_ratio": 1.5, "surge_t1_multiplier": 1.2, "active": True})
    data["pricing_rules"].append({"market": "lagos", "service": "motorbike-delivery", "base_minor": 80000, "minimum_minor": 200000, "per_km_minor": 15000, "per_minute_minor": 2500, "stop_fee_minor": 0, "weight_per_kg_minor": 1000, "tax_bps": 750, "surge_t1_ratio": 1.5, "surge_t1_multiplier": 1.15, "active": True})
    data["feature_flags"] += [{"key": "scheduled-deliveries", "rollout": {"markets": ["lagos"], "percentage": 100}, "enabled": True}, {"key": "surge-pricing", "rollout": {"markets": ["lagos"], "percentage": 100}, "enabled": True}]
    return dict(data)


def build():
    # Keep imports retry-safe.  Fixed demo slugs (for example ``lagos``) can
    # survive a partially rolled-back import and collide on a later attempt.
    # Sample rows remain available through ``sample_data()`` for explicit local
    # seeding, but are not part of the portable production blueprint.
    return {"format": "pawabase.blueprint", "version": 1, "name": "SwiftLine Mobility & Logistics", "description": "A multi-tenant realtime mobility and last-mile logistics platform with rides, scheduled and multi-stop deliveries, driver onboarding, matching races, payments, webhooks, operations, support and audit history.", "source": {"project": "swiftline", "env": "blueprint", "generator": "examples/blueprints/logistics/build.py"}, "definitions": {"schemas": SCHEMAS, "transformers": TRANSFORMERS, "policies": POLICIES, "resources": RESOURCES, "flows": FLOWS, "routes": _routes(), "buckets": BUCKETS, "subscriptions": SUBSCRIPTIONS, "webhooks": WEBHOOKS, "inbound-hooks": INBOUND, "schedules": SCHEDULES}, "roles": ROLES, "auth": AUTH, "settings": SETTINGS, "data": {}}


if __name__ == "__main__":
    blueprint = build()
    out = HERE / "swiftline.blueprint.json"
    out.write_text(json.dumps(blueprint, indent=2, ensure_ascii=False) + "\n")
    counts = {k: len(v) for k, v in blueprint["definitions"].items()}
    rows = sum(len(v) for v in blueprint["data"].values())
    print(f"wrote {out.name}: {sum(counts.values())} definitions {counts}, {len(ROLES)} roles, {rows} sample rows")
