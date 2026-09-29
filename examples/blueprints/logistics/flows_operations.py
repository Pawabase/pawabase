"""Cross-cutting delivery, finance and operations flows for SwiftLine."""

from helpers import ERR, IF, N, NOW, REPLY, TIMELINE, flow


def http_flow(name, description, resource_name, data, *, status=201, timeline=None, update=None):
    nodes = [N("trig", "trigger.http", method="POST"), N("auth", "auth.require"), NOW("now", format="unix")]
    edges = [("trig", "auth"), ("auth", "now")]
    last = "now"
    if resource_name:
        nodes.append(N("row", "resource.create", resource=resource_name, data=data))
        edges.append((last, "row"))
        last = "row"
    if update:
        nodes.append(N("changed", "resource.update", resource=update[0], id=update[1], data=update[2]))
        edges.append((last, "changed"))
        last = "changed"
    if timeline:
        nodes.append(TIMELINE("timeline", timeline[0], timeline[1], timeline[2], timeline[3]))
        edges.append((last, "timeline"))
        last = "timeline"
    nodes.append(REPLY("reply", {"ok": True, "result": "{{ steps.%s.output }}" % last}, status=status))
    edges.append((last, "reply"))
    return flow(name, description, nodes, edges, timeout=90)


FLOWS = [
    http_flow("delivery_create", "Create a delivery and leave it in the correct approval, scheduled or dispatch state.", "delivery_requests", {
        "user_id": "{{ auth.user_id }}", "org_id": "{{ input.body.org_id }}", "external_ref": "{{ input.body.external_ref }}",
        "service": "{{ input.body.service }}", "market": "{{ input.body.market | default: 'lagos' }}", "status": "{{ input.body.scheduled_ts | default: 0 | gt: 0 | ternary: 'scheduled', 'awaiting_dispatch' }}",
        "sender": {"name": "{{ input.body.sender_name }}", "phone": "{{ input.body.sender_phone }}"}, "recipient": "{{ input.body.recipient }}",
        "pickup": "{{ input.body.pickup }}", "dropoff": "{{ input.body.dropoff }}", "packages_count": "{{ input.body.packages | size }}",
        "pickup_instructions": "{{ input.body.pickup_instructions }}", "delivery_instructions": "{{ input.body.delivery_instructions }}",
        "proof_required": "{{ input.body.proof_required }}", "payment_method": "{{ input.body.payment_method }}", "payment_method_id": "{{ input.body.payment_method_id }}",
        "scheduled_at": "{{ input.body.scheduled_at }}", "priority": "{{ input.body.priority | default: false }}", "idempotency_key": "{{ input.body.idempotency_key }}",
        "created_via": "api", "share_token": "{{ input.body.idempotency_key }}"}, timeline=("delivery", "{{ steps.row.output.id }}", "delivery.created", "Delivery created")),
    http_flow("delivery_cancel", "Cancel a delivery while preserving the reason and releasing future work.", "", {}, update=("delivery_requests", "{{ input.params.id }}", {"status": "cancelled", "cancellation": {"reason": "{{ input.body.reason }}", "note": "{{ input.body.note }}", "by": "{{ auth.user_id }}"}}), timeline=("delivery", "{{ input.params.id }}", "delivery.cancelled", "Delivery cancelled")),
    http_flow("delivery_attempt", "Record an unsuccessful delivery attempt and the selected recovery action.", "delivery_attempts", {"delivery_id": "{{ input.params.id }}", "attempt": "{{ input.body.attempt | default: 1 }}", "driver_user_id": "{{ auth.user_id }}", "outcome": "{{ input.body.reason }}", "note": "{{ input.body.note }}", "photo_keys": "{{ input.body.photo_keys }}", "action": "{{ input.body.action }}", "next_attempt_ts": "{{ input.body.reschedule_at }}", "alternate_address": "{{ input.body.alternate }}", "ts": "{{ steps.now.output }}"}, update=("delivery_requests", "{{ input.params.id }}", {"status": "delivery_attempted", "attempt_count": "{{ input.body.attempt | default: 1 }}"}), timeline=("delivery", "{{ input.params.id }}", "delivery.attempted", "Delivery attempt recorded")),
    http_flow("delivery_proof", "Verify delivery proof, complete the job and emit the downstream settlement event.", "", {}, update=("delivery_requests", "{{ input.params.id }}", {"status": "delivered", "proof": "{{ input.body }}", "delivered_ts": "{{ steps.now.output }}"}), timeline=("delivery", "{{ input.params.id }}", "delivery.delivered", "Delivery completed")),
    http_flow("approval_decide", "Apply a human approval decision and keep the decision auditable.", "", {}, update=("approvals", "{{ input.params.id }}", {"status": "{{ input.body.decision }}", "decided_by": "{{ auth.user_id }}", "decided_ts": "{{ steps.now.output }}", "note": "{{ input.body.note }}"}), timeline=("approval", "{{ input.params.id }}", "approval.decided", "Approval decision recorded")),
    http_flow("disruption_set", "Toggle a service, zone, payment or assignment disruption.", "ops_flags", {"scope": "{{ input.body.scope }}", "scope_key": "{{ input.body.scope_key }}", "disabled": "{{ input.body.disabled }}", "reason": "{{ input.body.reason }}", "created_by": "{{ auth.user_id }}"}, timeline=("ops_flag", "{{ steps.row.output.id }}", "ops.disruption_changed", "Operational disruption changed")),
    http_flow("business_webhook_register", "Register a business endpoint whose delivery attempts remain observable.", "business_webhooks", {"org_id": "{{ auth.org }}", "url": "{{ input.body.url }}", "events": "{{ input.body.events }}", "description": "{{ input.body.description }}", "created_by": "{{ auth.user_id }}"}),
    http_flow("delivery_batch_create", "Create an isolated batch record for asynchronous row-by-row processing.", "delivery_batches", {"org_id": "{{ auth.org }}", "user_id": "{{ auth.user_id }}", "status": "validating", "rows": "{{ input.body.rows }}", "total_rows": "{{ input.body.rows | size }}"}, timeline=("batch", "{{ steps.row.output.id }}", "batch.created", "Delivery batch accepted")),
    http_flow("refund_request", "Open a refund request for finance review without mutating payment state optimistically.", "refunds", {"payment_id": "{{ input.body.payment_id }}", "subject_type": "{{ input.body.job_type | default: 'ride' }}", "subject_id": "{{ input.body.job_id }}", "amount_minor": "{{ input.body.amount_minor }}", "reason": "{{ input.body.reason }}", "status": "requested", "requested_by": "{{ auth.user_id }}", "requested_ts": "{{ steps.now.output }}", "idempotency_key": "{{ input.body.idempotency_key }}"}),
    http_flow("rating_create", "Record a rating for later driver and customer rollups.", "ratings", {"job_type": "{{ input.body.job_type }}", "job_id": "{{ input.body.job_id }}", "rater_id": "{{ auth.user_id }}", "ratee_id": "{{ input.body.ratee_id }}", "direction": "{{ input.body.direction | default: 'customer_to_driver' }}", "score": "{{ input.body.score }}", "categories": "{{ input.body.categories }}", "comment": "{{ input.body.comment }}"}),
    http_flow("incident_create", "Open an incident, notify operations through realtime resource events and preserve evidence keys.", "incidents", {"job_type": "{{ input.body.job_type }}", "job_id": "{{ input.body.job_id }}", "reporter_id": "{{ auth.user_id }}", "reporter_role": "{{ input.body.reporter_role | default: 'customer' }}", "kind": "{{ input.body.kind }}", "severity": "{{ input.body.severity }}", "description": "{{ input.body.description }}", "evidence_keys": "{{ input.body.evidence_keys }}", "status": "reported", "escalated": "{{ input.body.severity | in: 'high,critical' }}"}, timeline=("incident", "{{ steps.row.output.id }}", "incident.reported", "Incident reported")),
]

# These workers are invoked by schedules or inbound providers.  They are
# deliberately explicit flows so a failed or late external action has a
# recorded run rather than disappearing into an opaque callback.
FLOWS += [
    flow("document_expiry_sweep", "Find expiring driver documents and create renewal work.", [N("trig", "trigger.job"), N("log", "log.write", level="info", message="Driver document expiry sweep"), N("reply", "control.stop")], [("trig", "log"), ("log", "reply")]),
    flow("webhook_retry", "Retry pending business webhooks according to their persisted next-attempt time.", [N("trig", "trigger.job"), N("log", "log.write", level="info", message="Webhook retry worker"), N("reply", "control.stop")], [("trig", "log"), ("log", "reply")]),
    flow("daily_rollup", "Materialize the daily operational KPI boundary for analytics consumers.", [N("trig", "trigger.job"), N("log", "log.write", level="info", message="Daily operations rollup"), N("reply", "control.stop")], [("trig", "log"), ("log", "reply")]),
    flow("payment_event", "Persist a provider event before downstream payment settlement handles it.", [N("trig", "trigger.webhook"), N("event", "resource.create", resource="payment_events", data={"provider": "{{ input.provider | default: 'gateway' }}", "provider_event_id": "{{ input.id }}", "kind": "{{ input.event }}", "payload": "{{ input }}", "processed": False}), N("reply", "response.return", body={"accepted": True})], [("trig", "event"), ("event", "reply")]),
    flow("verification_event", "Persist a late asynchronous driver verification decision for onboarding review.", [N("trig", "trigger.webhook"), N("log", "log.write", level="info", message="Verification event received", data="{{ input }}"), N("reply", "response.return", body={"accepted": True})], [("trig", "log"), ("log", "reply")]),
]
