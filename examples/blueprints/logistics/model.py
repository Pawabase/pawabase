"""Roles, schemas, transformers and policies for the SwiftLine logistics platform.

Actors
------
End users are Akountz users. Platform-side work is gated by roles (assigned in
Studio → Auth → Users); fleet and business work is gated by organization
membership (``$auth.org`` + ``$auth.org_role``), the same pattern the commerce
blueprint uses for stores:

* a **fleet** is an organization; its slug appears as ``fleet_id`` on rows;
* a **business** is an organization; its slug appears as ``org_id`` on rows.

Policies are JSON conditions. List policies are written so record equalities
push down into SQL (a single ``$record.field = $auth…`` conjunction), which
keeps pagination correct; anything cross-actor is served by a custom route
whose flow queries with service rights and enforces rules explicitly.
"""

from helpers import (
    ALL,
    ANY,
    AUTH,
    AUTH_EQ,
    EQ,
    EXISTS,
    F,
    GT,
    IN,
    NIN,
    NOT,
    OWNER,
    ROLE,
    TRUTHY,
    money,
)

# ── roles (written to Akountz for every environment) ─────────────────────────

ROLES = [
    {"name": "customer", "description": "Rider / sender. Given at sign-up.",
     "permissions": ["ride.create", "ride.cancel", "delivery.create", "delivery.cancel", "support.write"]},
    {"name": "driver", "description": "Independent or fleet driver.",
     "permissions": ["drive.accept", "drive.operate"]},
    {"name": "fleet_owner", "description": "Owns a fleet organization.",
     "permissions": ["fleet.manage", "fleet.drivers", "fleet.payouts"]},
    {"name": "dispatcher", "description": "Assigns and reassigns jobs in an area.",
     "permissions": ["dispatch.manage", "ops.read"]},
    {"name": "business_admin", "description": "Operates a business organization's logistics.",
     "permissions": ["business.manage", "business.billing", "delivery.create"]},
    {"name": "support_agent", "description": "Customer support: reads everything, replies to tickets.",
     "permissions": ["support.read", "support.reply", "support.manage"]},
    {"name": "ops_agent", "description": "Operations center: live map, interventions, disruptions.",
     "permissions": ["ops.read", "ops.manage", "dispatch.manage", "support.read"]},
    {"name": "finance_agent", "description": "Payments, refunds, payouts, invoices.",
     "permissions": ["finance.read", "finance.manage", "payouts.manage", "support.read"]},
    {"name": "risk_agent", "description": "Fraud and risk review, account restrictions.",
     "permissions": ["risk.read", "risk.manage", "support.read"]},
    {"name": "onboarding_agent", "description": "Driver verification and activation.",
     "permissions": ["onboarding.review", "onboarding.manage", "support.read"]},
    {"name": "admin", "description": "Platform administrator.", "permissions": ["*"]},
    {"name": "super_admin", "description": "Full control, including admin management.", "permissions": ["*"]},
]

# ── shared field snippets ────────────────────────────────────────────────────

ADDRESS_FIELDS = [
    F("label", max_length=40, description="Home, Work, …"),
    F("line1", required=True, max_length=160),
    F("line2", max_length=160),
    F("city", required=True, max_length=80),
    F("region", max_length=80),
    F("country", required=True, pattern=r"^[A-Z]{2}$"),
    F("postal_code", max_length=20),
    F("lat", "number", required=True, minimum=-90, maximum=90),
    F("lng", "number", required=True, minimum=-180, maximum=180),
]

# ── schemas (route input validation; also used by ref fields) ────────────────

SCHEMAS = [
    {"name": "Address", "description": "A physical address with coordinates.", "fields": ADDRESS_FIELDS},
    {"name": "StopInput", "description": "One stop on a multi-stop journey.", "fields": [
        F("address", "ref", schema="Address", required=True),
        F("instructions", "text", max_length=500),
        F("contact_name", max_length=120),
        F("contact_phone", max_length=32),
    ]},
    {"name": "EstimateInput", "description": "Fare / price estimate request.", "fields": [
        F("service", required=True, description="Service slug, e.g. economy-ride"),
        F("pickup_lat", "number", required=True, minimum=-90, maximum=90),
        F("pickup_lng", "number", required=True, minimum=-180, maximum=180),
        F("dropoff_lat", "number", minimum=-90, maximum=90),
        F("dropoff_lng", "number", minimum=-180, maximum=180),
        F("stops", "array", items={"type": "json"}, default=[]),
        F("passengers", "integer", default=1, minimum=1, maximum=8),
        F("package_weight_kg", "number", minimum=0, maximum=2000),
        F("scheduled_at", "datetime", description="When omitted, this is an immediate request"),
        F("promo_code", max_length=40),
        F("market", max_length=60, description="Market slug; resolved from coordinates when omitted"),
    ]},
    {"name": "RideRequestInput", "description": "Confirm a ride after estimating.", "fields": [
        F("service", required=True),
        F("pickup", "ref", schema="Address", required=True),
        F("dropoff", "ref", schema="Address", required=True),
        F("stops", "array", items={"type": "ref", "schema": "StopInput"}, default=[]),
        F("passengers", "integer", default=1, minimum=1, maximum=8),
        F("pickup_notes", "text", max_length=500),
        F("accessibility", "array", items={"type": "string"}, default=[], description="wheelchair, child-seat, …"),
        F("payment_method", required=True, enum=["card", "wallet", "cash", "business", "promo_balance"]),
        F("payment_method_id", "integer"),
        F("scheduled_at", "datetime", description="Display form; scheduled_ts is authoritative"),
        F("scheduled_ts", "integer", description="Epoch seconds for a future ride"),
        F("market", max_length=60),
        F("priority", "boolean", default=False),
        F("promo_code", max_length=40),
        F("org_id", pattern=r"^[a-z0-9][a-z0-9-]{1,62}$", description="Bill a business organization you belong to"),
        F("estimate_id", "integer", description="The estimate this confirmation references"),
        F("idempotency_key", max_length=64, description="Safe retries: the same key never creates two rides"),
    ]},
    {"name": "CancelInput", "description": "Cancel a job, with a reason.", "fields": [
        F("reason", required=True, max_length=60),
        F("note", "text", max_length=500),
    ]},
    {"name": "PackageInput", "description": "One package in a delivery.", "fields": [
        F("description", required=True, max_length=200),
        F("weight_kg", "number", required=True, minimum=0.01, maximum=2000),
        F("length_cm", "number", minimum=0, maximum=600),
        F("width_cm", "number", minimum=0, maximum=600),
        F("height_cm", "number", minimum=0, maximum=600),
        F("fragile", "boolean", default=False),
        F("declared_value_minor", "integer", minimum=0, description="For insurance and risk tiers"),
        F("photo_key", max_length=300, description="package-photos object key"),
    ]},
    {"name": "DeliveryInput", "description": "Create a delivery (sender side).", "fields": [
        F("service", required=True),
        F("pickup", "ref", schema="Address", required=True),
        F("dropoff", "ref", schema="Address", required=True),
        F("sender_name", required=True, max_length=120),
        F("sender_phone", required=True, max_length=32),
        F("recipient", "ref", schema="RecipientInput", required=True),
        F("packages", "array", items={"type": "ref", "schema": "PackageInput"}, required=True),
        F("pickup_instructions", "text", max_length=500),
        F("delivery_instructions", "text", max_length=500),
        F("pickup_window_start", "datetime"),
        F("pickup_window_end", "datetime"),
        F("delivery_window_start", "datetime"),
        F("delivery_window_end", "datetime"),
        F("scheduled_at", "datetime", description="Display form; scheduled_ts is authoritative"),
        F("scheduled_ts", "integer", description="Epoch seconds for a future dispatch"),
        F("market", max_length=60),
        F("priority", "boolean", default=False),
        F("proof_required", "array", items={"type": "string"}, default=["photo"],
            description="Any of: photo, signature, pin, id_check, note"),
        F("payment_method", required=True, enum=["card", "wallet", "cash", "business", "promo_balance"]),
        F("payment_method_id", "integer"),
        F("org_id", pattern=r"^[a-z0-9][a-z0-9-]{1,62}$"),
        F("external_ref", max_length=80, description="The sender's own reference, e.g. an ERP order id"),
        F("promo_code", max_length=40),
        F("idempotency_key", max_length=64),
    ]},
    {"name": "RecipientInput", "description": "Who receives a delivery.", "fields": [
        F("name", required=True, max_length=120),
        F("phone", required=True, max_length=32),
        F("email", "email"),
        F("delivery_pin", max_length=12, description="PIN the recipient quotes to the driver"),
        F("id_required", "boolean", default=False),
        F("notes", "text", max_length=500),
    ]},
    {"name": "RatingInput", "description": "Rate a completed job.", "fields": [
        F("job_type", required=True, enum=["ride", "delivery"]),
        F("job_id", "integer", required=True),
        F("score", "integer", required=True, minimum=1, maximum=5),
        F("categories", "json", description='{"cleanliness": 4, "punctuality": 5}'),
        F("comment", "text", max_length=2000),
    ]},
    {"name": "IncidentInput", "description": "Report an incident during an active job.", "fields": [
        F("job_type", required=True, enum=["ride", "delivery"]),
        F("job_id", "integer", required=True),
        F("kind", required=True, enum=["safety", "accident", "harassment", "vehicle", "theft", "damage", "other"]),
        F("severity", required=True, enum=["low", "medium", "high", "critical"]),
        F("description", "text", required=True, max_length=5000),
        F("evidence_keys", "array", items={"type": "string"}, default=[], description="incident-evidence object keys"),
    ]},
    {"name": "TicketInput", "description": "Open a support ticket.", "fields": [
        F("subject", required=True, max_length=200),
        F("category", required=True, enum=["driver_behavior", "customer_behavior", "lost_property", "incorrect_fare",
                                          "missing_package", "damaged_package", "payment", "refund", "safety",
                                          "account", "driver_onboarding", "other"]),
        F("message", "text", required=True, max_length=5000),
        F("job_type", enum=["ride", "delivery"]),
        F("job_id", "integer"),
        F("attachment_keys", "array", items={"type": "string"}, default=[]),
    ]},
    {"name": "TicketReplyInput", "description": "Reply on a ticket.", "fields": [
        F("message", "text", required=True, max_length=5000),
        F("attachment_keys", "array", items={"type": "string"}, default=[]),
        F("close", "boolean", default=False),
    ]},
    {"name": "LostItemInput", "description": "Report an item left behind on a ride.", "fields": [
        F("ride_id", "integer", required=True),
        F("description", "text", required=True, max_length=2000),
        F("contact_phone", max_length=32),
    ]},
    {"name": "OnboardingInput", "description": "Start a driver application.", "fields": [
        F("full_name", required=True, max_length=160),
        F("phone", required=True, max_length=32),
        F("license_number", required=True, max_length=60),
        F("license_expiry", "date", required=True),
        F("market", required=True, description="Home market slug"),
        F("fleet_id", pattern=r"^[a-z0-9][a-z0-9-]{1,62}$", description="Join a fleet directly"),
    ]},
    {"name": "VehicleInput", "description": "Register a vehicle.", "fields": [
        F("class", required=True, enum=["bicycle", "motorcycle", "tricycle", "compact_car", "standard_car",
                                        "premium_car", "van", "small_truck", "large_truck"]),
        F("make", required=True, max_length=60),
        F("model", required=True, max_length=60),
        F("year", "integer", minimum=1990, maximum=2100),
        F("color", max_length=40),
        F("plate", required=True, max_length=20),
        F("passenger_capacity", "integer", default=1, minimum=0, maximum=60),
        F("weight_capacity_kg", "number", default=0, minimum=0, maximum=40000),
        F("cargo_length_cm", "number", minimum=0), F("cargo_width_cm", "number", minimum=0),
        F("cargo_height_cm", "number", minimum=0),
        F("registration_expiry", "date"),
        F("insurance_expiry", "date"),
    ]},
    {"name": "LocationPingInput", "description": "One driver location update.", "fields": [
        F("lat", "number", required=True, minimum=-90, maximum=90),
        F("lng", "number", required=True, minimum=-180, maximum=180),
        F("heading", "number", minimum=0, maximum=360),
        F("speed_kph", "number", minimum=0, maximum=400),
        F("accuracy_m", "number", minimum=0),
        F("recorded_ts", "integer", required=True, description="Device clock, epoch seconds — ordering key"),
        F("state", description="Driver-reported state; the platform keeps the authoritative one"),
    ]},
    {"name": "ApprovalDecisionInput", "description": "Approve or reject a pending approval.", "fields": [
        F("decision", required=True, enum=["approved", "rejected"]),
        F("note", "text", max_length=1000),
    ]},
    {"name": "RefundRequestInput", "description": "Ask for a refund.", "fields": [
        F("payment_id", "integer", required=True),
        F("amount_minor", "integer", minimum=1, description="Omit for a full refund"),
        F("reason", required=True, max_length=240),
    ]},
    {"name": "PromoValidateInput", "description": "Check a promo code against a quote.", "fields": [
        F("code", required=True, max_length=40),
        F("service", required=True),
        F("estimate_minor", "integer", required=True, minimum=0),
    ]},
    {"name": "BusinessWebhookInput", "description": "Register a business webhook endpoint.", "fields": [
        F("url", "url", required=True),
        F("events", "array", items={"type": "string"}, default=["delivery.*"]),
        F("description", max_length=240),
    ]},
    {"name": "FleetSetupInput", "description": "Create a fleet for the active organization.", "fields": [
        F("name", required=True, max_length=120),
        F("market", required=True),
        F("contact_email", "email", required=True),
        F("commission_bps", "integer", default=1000, minimum=0, maximum=4000,
            description="Fleet's cut of driver earnings, basis points"),
    ]},
    {"name": "BusinessSetupInput", "description": "Register a business organization on the platform.", "fields": [
        F("name", required=True, max_length=120),
        F("billing_email", "email", required=True),
        F("market", required=True),
        F("monthly_limit_minor", "integer", minimum=0, description="Spending cap across employees"),
        F("approval_threshold_minor", "integer", minimum=0,
            description="Jobs above this need a manager's approval"),
    ]},
    {"name": "OrgPolicyInput", "description": "Set an organization's ride/delivery policy.", "fields": [
        F("max_ride_minor", "integer", minimum=0),
        F("allowed_services", "array", items={"type": "string"}, default=[]),
        F("operating_hours", "json", description='{"start": "07:00", "end": "21:00"}'),
        F("allowed_pickup_zones", "array", items={"type": "string"}, default=[]),
        F("allowed_dropoff_zones", "array", items={"type": "string"}, default=[]),
        F("monthly_limit_minor", "integer", minimum=0),
        F("per_employee_limit_minor", "integer", minimum=0),
        F("approval_threshold_minor", "integer", minimum=0),
    ]},
    {"name": "DisruptionInput", "description": "Toggle an operational disruption switch.", "fields": [
        F("scope", required=True, enum=["service", "zone_pickup", "zone_delivery", "payment_method",
                                        "assignments", "vehicle_class"]),
        F("scope_key", required=True, max_length=80, description="service slug, zone slug, method, or class"),
        F("disabled", "boolean", required=True),
        F("reason", required=True, max_length=240),
    ]},
    {"name": "ReassignInput", "description": "Operations removes the current driver.", "fields": [
        F("reason", required=True, max_length=240),
    ]},
    {"name": "ProofInput", "description": "Proof of delivery collected at the door.", "fields": [
        F("photo_keys", "array", items={"type": "string"}, default=[]),
        F("signature_key", max_length=300),
        F("pin", max_length=12),
        F("recipient_name", max_length=120),
        F("id_checked", "boolean", default=False),
        F("note", "text", max_length=1000),
        F("lat", "number", minimum=-90, maximum=90),
        F("lng", "number", minimum=-180, maximum=180),
    ]},
    {"name": "AttemptInput", "description": "Record a failed delivery attempt.", "fields": [
        F("reason", required=True, enum=["recipient_unavailable", "address_issue", "refused",
                                         "access_denied", "other"]),
        F("note", "text", max_length=1000),
        F("photo_keys", "array", items={"type": "string"}, default=[]),
        F("action", required=True, enum=["retry_later", "contact_recipient", "wait", "reschedule",
                                         "alternate_address", "return_to_sender", "escalate"]),
        F("reschedule_at", "datetime"),
        F("alternate", "ref", schema="Address"),
    ]},
    {"name": "BatchRowInput", "description": "One row of a delivery batch.", "fields": [
        F("external_ref", max_length=80),
        F("service", required=True),
        F("pickup", "ref", schema="Address", required=True),
        F("dropoff", "ref", schema="Address", required=True),
        F("sender_name", required=True, max_length=120),
        F("sender_phone", required=True, max_length=32),
        F("recipient", "ref", schema="RecipientInput", required=True),
        F("packages", "array", items={"type": "ref", "schema": "PackageInput"}, required=True),
        F("delivery_instructions", "text", max_length=500),
        F("scheduled_at", "datetime"),
        F("scheduled_ts", "integer"),
    ]},
    {"name": "ChatInput", "description": "A message on an active job.", "fields": [
        F("message", "text", required=True, max_length=2000),
    ]},
    {"name": "PayoutAccountInput", "description": "Where a driver or fleet receives payouts.", "fields": [
        F("provider", required=True, enum=["bank_transfer", "mobile_money"]),
        F("account_name", required=True, max_length=160),
        F("account_number", required=True, max_length=40),
        F("bank_code", required=True, max_length=20),
    ]},
]

# ── transformers ─────────────────────────────────────────────────────────────

TRANSFORMERS = [
    {"name": "public_driver", "description": "What a customer sees of their driver.",
     "definition": {"pick": ["user_id", "full_name", "phone", "state", "rating_avg", "rating_count",
                             "trips_completed", "vehicle_class", "vehicle_plate", "vehicle_description",
                             "last_lat", "last_lng", "last_location_ts"]}},
    {"name": "public_vehicle", "description": "Vehicle details safe for customers.",
     "definition": {"pick": ["class", "make", "model", "year", "color", "plate", "passenger_capacity"]}},
    {"name": "customer_payment", "description": "Payments without provider internals.",
     "definition": {"omit": ["provider_payload", "provider_ref", "failure_detail", "risk_flags"]}},
    {"name": "public_profile", "description": "A user's public rider/driver card.",
     "definition": {"omit": ["phone", "email", "license_number", "license_expiry", "home_address",
                             "payout_account", "date_of_birth"]}},
    {"name": "business_delivery", "description": "Delivery rows for business API consumers.",
     "definition": {"omit": ["risk_score", "risk_flags", "internal_notes"]}},
    {"name": "notification_card", "description": "Notification payloads pushed to channels.",
     "definition": {"pick": ["id", "kind", "title", "body", "link", "created_at"]}},
    {"name": "support_ticket_view", "description": "Ticket rows for list screens.",
     "definition": {"omit": ["internal_notes"]}},
    {"name": "earnings_view", "description": "Earnings without adjustment internals.",
     "definition": {"omit": ["adjustment_notes"]}},
]

# ── policies ─────────────────────────────────────────────────────────────────

ADMIN = ROLE("admin", "super_admin")
STAFF_READ = ROLE("support_agent", "ops_agent", "finance_agent", "risk_agent",
                  "onboarding_agent", "dispatcher", "admin", "super_admin")
OPS = ROLE("ops_agent", "dispatcher", "admin", "super_admin")
FINANCE = ROLE("finance_agent", "admin", "super_admin")
RISK = ROLE("risk_agent", "ops_agent", "admin", "super_admin")
ONBOARDING = ROLE("onboarding_agent", "admin", "super_admin")
DRIVER = ROLE("driver")
CUSTOMER = ROLE("customer")

#: Staff of the organization the row belongs to (business rows carry ``org_id``).
def org_staff(field, *roles):
    return ALL(
        EXISTS("$auth.org"),
        IN("$auth.org_role", list(roles)),
        EQ(f"$record.{field}", "$auth.org"),
    )


def org_create(field, *roles):
    return ALL(
        EXISTS("$auth.org"),
        IN("$auth.org_role", list(roles)),
        EQ(f"$input.{field}", "$auth.org"),
    )


ORG_ROLES = ["owner", "admin", "member", "viewer"]
ORG_EDITORS = ["owner", "admin", "member"]
ORG_MANAGERS = ["owner", "admin"]

POLICIES = [
    # staff groups
    {"name": "staff_read", "description": "Any platform agent can read.", "condition": STAFF_READ},
    {"name": "ops_manage", "description": "Operations and dispatchers manage.", "condition": OPS},
    {"name": "finance_manage", "description": "Finance agents manage.", "condition": FINANCE},
    {"name": "risk_manage", "description": "Risk and ops manage.", "condition": RISK},
    {"name": "onboarding_manage", "description": "Onboarding agents manage.", "condition": ONBOARDING},
    {"name": "admin_only", "description": "Platform administrators only.", "condition": ADMIN},

    # customers and drivers
    {"name": "customer_self", "description": "The user the row belongs to.",
     "condition": OWNER("user_id")},
    {"name": "customer_create", "description": "Signed-in users creating their own rows.",
     "condition": ALL(AUTH(), EQ("$input.user_id", "$auth.user_id"))},
    {"name": "driver_role", "description": "Signed-in drivers.",
     "condition": ALL(AUTH(), DRIVER)},
    {"name": "driver_self", "description": "The driver this profile belongs to, or staff.",
     "condition": ANY(OWNER("user_id"), STAFF_READ)},
    {"name": "job_party", "description": "The customer, the assigned driver, or staff.",
     "condition": ANY(OWNER("user_id"), AUTH_EQ("driver_user_id", "user_id"), STAFF_READ)},

    # fleets
    {"name": "fleet_staff", "description": "Staff of the fleet org the row belongs to.",
     "condition": org_staff("fleet_id", *ORG_ROLES)},
    {"name": "fleet_editors", "description": "Operating fleet staff.",
     "condition": org_staff("fleet_id", *ORG_EDITORS)},
    {"name": "fleet_managers", "description": "Fleet owners and admins.",
     "condition": org_staff("fleet_id", *ORG_MANAGERS)},
    {"name": "fleet_manager_create", "description": "Fleet managers creating rows for their fleet.",
     "condition": org_create("fleet_id", *ORG_MANAGERS)},

    # businesses
    {"name": "business_staff", "description": "Members of the business org the row belongs to.",
     "condition": org_staff("org_id", *ORG_ROLES)},
    {"name": "business_editors", "description": "Operating business members.",
     "condition": org_staff("org_id", *ORG_EDITORS)},
    {"name": "business_managers", "description": "Business org owners and admins.",
     "condition": org_staff("org_id", *ORG_MANAGERS)},
    {"name": "business_member_create", "description": "Business members creating rows for their org.",
     "condition": org_create("org_id", *ORG_EDITORS)},
    {"name": "business_api", "description": "Business org member or a scoped service key.",
     "condition": ANY(org_staff("org_id", *ORG_EDITORS), {"scope": "deliveries:write"} , ADMIN)},

    # config
    {"name": "config_read", "description": "Anyone reads public configuration.",
     "condition": True},
    {"name": "config_write", "description": "Only operations and admins change configuration.",
     "condition": OPS},

    # recipients: readable by the sender (user_id) and the org that owns them
    {"name": "recipient_owner", "description": "The sender who saved the recipient, their org, or staff.",
     "condition": ANY(OWNER("user_id"), org_staff("org_id", *ORG_ROLES), STAFF_READ)},

    # jobs needing approval visibility
    {"name": "approval_party", "description": "The requester, an org manager, or staff.",
     "condition": ANY(OWNER("user_id"), org_staff("org_id", *ORG_MANAGERS), STAFF_READ)},

    # support
    {"name": "ticket_party", "description": "The ticket owner or support staff.",
     "condition": ANY(OWNER("user_id"), ROLE("support_agent", "ops_agent", "admin", "super_admin"))},

    # realtime channel rules reference these
    {"name": "channel_self", "description": "A user's private channel.",
     "condition": AUTH()},
    {"name": "channel_driver", "description": "Drivers receive their offers and jobs.",
     "condition": ALL(AUTH(), DRIVER)},
    {"name": "channel_ops", "description": "Operations watch live operations.",
     "condition": OPS},
    {"name": "channel_fleet", "description": "Fleet staff watch their fleet.",
     "condition": ALL(EXISTS("$auth.org"), IN("$auth.org_role", ORG_ROLES))},
    {"name": "channel_business", "description": "Business staff watch their org's jobs.",
     "condition": ALL(EXISTS("$auth.org"), IN("$auth.org_role", ORG_ROLES))},
]
