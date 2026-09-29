"""Driver operations and the trip lifecycle: state, location, arrival, waiting,
stops, completion, cancellation, chat and public tracking links."""

from helpers import (
    ERR, IF, NOW, N, SET, CALC, EMIT, NOTIFY_USER, PUBLISH, REPLY, TIMELINE, flow,
)
from flows_util import ZONE_SQL

FLOWS = []

# ── driver state machine ─────────────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/state"),
    N("auth", "auth.require", role="driver"),
    N("me", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    IF("found", {"truthy": "$steps.me.output.total"}),
    ERR("no_profile", 404, "no_driver_profile", "Complete driver onboarding first"),
    SET("me_set", me="{{ steps.me.output.data.0 }}"),
    IF("activated", {"eq": ["$vars.me.onboarding_status", "activated"]}),
    ERR("not_active", 409, "not_activated", "Your onboarding isn't complete yet"),
    IF("blocked", {"in": ["$vars.me.state", ["suspended", "restricted"]]}),
    ERR("is_blocked", 423, "driver_restricted", "Your account is restricted: {{ vars.me.restricted_reason }}"),
    IF("on_job", {"all": [
        {"eq": ["{{ input.body.state }}", "offline"]},
        {"in": ["$vars.me.state", ["to_pickup", "waiting_at_pickup", "active_trip", "active_delivery", "at_stop"]]}]}),
    ERR("busy", 409, "on_active_job", "Finish or cancel your current job first"),
    # going available requires a compliant vehicle
    IF("want_avail", {"eq": ["{{ input.body.state }}", "available"]}),
    IF("has_vehicle", {"truthy": "{{ input.body.vehicle_id }}"}),
    N("veh", "resource.get", resource="vehicles", id="{{ input.body.vehicle_id | default: vars.me.current_vehicle_id }}"),
    ERR("no_veh", 422, "vehicle_required", "Register and select a vehicle to go available"),
    IF("veh_mine", {"eq": ["$steps.veh.output.driver_user_id", "$auth.user_id"]}),
    ERR("veh_not_mine", 403, "forbidden", "That vehicle belongs to another driver"),
    NOW("today", format="date"),
    IF("veh_ok", {"all": [
        {"eq": ["$steps.veh.output.status", "active"]},
        {"eq": ["$steps.veh.output.verified", True]},
        {"any": [{"empty": "$steps.veh.output.insurance_expiry"},
                 {"gte": ["$steps.veh.output.insurance_expiry", "$steps.today.output"]}]},
        {"any": [{"empty": "$steps.veh.output.registration_expiry"},
                 {"gte": ["$steps.veh.output.registration_expiry", "$steps.today.output"]}]},
    ]}),
    ERR("veh_bad", 422, "vehicle_not_eligible",
        "This vehicle is not verified, is out of service, or has expired documents"),
    N("veh_class", "db.query", sql=(
        "SELECT CASE class WHEN 'bicycle' THEN 1 WHEN 'motorcycle' THEN 2 WHEN 'tricycle' THEN 3 "
        "WHEN 'compact_car' THEN 4 WHEN 'standard_car' THEN 5 WHEN 'premium_car' THEN 6 "
        "WHEN 'van' THEN 7 WHEN 'small_truck' THEN 8 WHEN 'large_truck' THEN 9 ELSE 0 END AS rank "
        "FROM vehicles WHERE id = ?"), params=["{{ steps.veh.output.id }}"]),
    N("veh_bind", "resource.update", resource="driver_profiles", id="{{ vars.me.id }}", data={
        "current_vehicle_id": "{{ steps.veh.output.id }}",
        "vehicle_class": "{{ steps.veh.output.class }}",
        "vehicle_class_rank": "{{ steps.veh_class.output.0.rank }}",
        "vehicle_weight_kg": "{{ steps.veh.output.weight_capacity_kg }}",
        "vehicle_plate": "{{ steps.veh.output.plate }}",
        "vehicle_description": "{{ steps.veh.output.color }} {{ steps.veh.output.make }} {{ steps.veh.output.model }}",
    }),
    N("veh_mark", "resource.list", resource="vehicles", filters={"id": "{{ steps.veh.output.id }}"}, limit=1),
    NOW("since", format="unix"),
    N("go", "resource.update", resource="driver_profiles", id="{{ vars.me.id }}",
      data={"state": "available", "online_since_ts": "{{ steps.since.output }}", "location_stale": True}),
    TIMELINE("tl_avail", "application", "{{ auth.user_id }}", "driver.available",
             "Driver went available in {{ vars.me.market }}", actor="{{ auth.user_id }}"),
    REPLY("reply_avail", {"state": "available", "vehicle": "{{ steps.veh.output }}"}),
    # simpler transitions
    N("go2", "resource.update", resource="driver_profiles", id="{{ vars.me.id }}",
      data={"state": "{{ input.body.state }}"}),
    IF("went_offline", {"eq": ["{{ input.body.state }}", "offline"]}),
    N("clr", "resource.update", resource="driver_profiles", id="{{ vars.me.id }}",
      data={"online_since_ts": None, "location_stale": True}),
    EMIT("ev_off", "driver.offline", {"driver_user_id": "{{ auth.user_id }}", "market": "{{ vars.me.market }}"}),
    EMIT("ev_on", "driver.{{ input.body.state }}", {"driver_user_id": "{{ auth.user_id }}",
                                                     "market": "{{ vars.me.market }}"}),
    REPLY("reply", {"state": "{{ input.body.state }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "me"), ("me", "found"),
    ("found", "me_set", "true"), ("found", "no_profile", "false"),
    ("me_set", "activated"), ("activated", "blocked", "true"), ("activated", "not_active", "false"),
    ("blocked", "on_job", "false"), ("blocked", "is_blocked", "true"),
    ("on_job", "want_avail", "false"), ("on_job", "busy", "true"),
    ("want_avail", "has_vehicle", "true"), ("want_avail", "go2", "false"),
    ("has_vehicle", "veh", "true"),
    ("has_vehicle", "veh", "false"),
    ("veh", "veh_mine", "next"), ("veh", "no_veh", "missing"),
    ("veh_mine", "today", "true"), ("veh_mine", "veh_not_mine", "false"),
    ("today", "veh_ok"), ("veh_ok", "veh_class", "true"), ("veh_ok", "veh_bad", "false"),
    ("veh_class", "veh_bind"), ("veh_bind", "veh_mark"), ("veh_mark", "since"), ("since", "go"),
    ("go", "tl_avail"), ("tl_avail", "reply_avail"),
    ("go2", "went_offline"),
    ("went_offline", "clr", "true"), ("clr", "ev_off"), ("ev_off", "reply"),
    ("went_offline", "ev_on", "false"), ("ev_on", "reply"),
]
FLOWS.append(flow("driver_set_state", "Driver lifecycle: offline / online / available / paused. "
                                  "Going available binds a verified, compliant vehicle.",
                  _NODES, _EDGES, timeout=90))

# ── location ingest ──────────────────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/location"),
    N("auth", "auth.require", role="driver"),
    N("me", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    IF("found", {"truthy": "$steps.me.output.total"}),
    ERR("no_profile", 404, "no_driver_profile", "No driver profile"),
    # out-of-order guard: older than the newest accepted ping is logged but never moves the driver
    IF("in_order", {"gt": ["{{ input.body.recorded_ts }}",
                            "$steps.me.output.data.0.last_location_ts | default: 0"]}),
    REPLY("stale_reply", {"accepted": False, "reason": "out_of_order"}, status=202),
    NOW("recv", format="unix"),
    N("upd", "resource.update", resource="driver_profiles", id="{{ steps.me.output.data.0.id }}", data={
        "last_lat": "{{ input.body.lat }}", "last_lng": "{{ input.body.lng }}",
        "last_heading": "{{ input.body.heading }}", "last_speed_kph": "{{ input.body.speed_kph }}",
        "last_location_ts": "{{ input.body.recorded_ts }}", "location_stale": False}),
    N("log", "resource.create", resource="location_pings", data={
        "driver_user_id": "{{ auth.user_id }}", "lat": "{{ input.body.lat }}", "lng": "{{ input.body.lng }}",
        "heading": "{{ input.body.heading }}", "speed_kph": "{{ input.body.speed_kph }}",
        "accuracy_m": "{{ input.body.accuracy_m }}", "recorded_ts": "{{ input.body.recorded_ts }}",
        "received_ts": "{{ steps.recv.output }}", "state": "{{ steps.me.output.data.0.state }}"}),
    # GPS sanity: implied speed vs the last accepted ping flags teleporting devices
    IF("sanity", {"all": [
        {"truthy": "$steps.me.output.data.0.last_lat"},
        {"gt": ["{{ input.body.speed_kph }}", 300]}]}),
    EMIT("risk_ev", "risk.signal", {"kind": "gps_anomaly", "subject_type": "driver",
                                    "subject_id": "{{ auth.user_id }}",
                                    "detail": "reported speed {{ input.body.speed_kph }} km/h"}),
    # on an active job → publish movement to the customer channel (throttled by cache)
    IF("on_job", {"in": ["$steps.me.output.data.0.state",
                          ["to_pickup", "waiting_at_pickup", "active_trip", "active_delivery", "at_stop"]]}),
    N("job", "db.query", sql=(
        "SELECT 'ride' AS kind, id, status, pickup, dropoff, user_id FROM ride_requests "
        "WHERE driver_user_id = ? AND status IN ('driver_assigned','driver_en_route','driver_arrived','in_progress','at_stop') "
        "UNION ALL "
        "SELECT 'delivery', id, status, pickup, dropoff, user_id FROM delivery_requests "
        "WHERE driver_user_id = ? AND status IN ('driver_assigned','driver_to_pickup','package_collected','in_transit','at_destination') "
        "LIMIT 1"), params=["{{ auth.user_id }}", "{{ auth.user_id }}"]),
    IF("has_job", {"truthy": "$steps.job.output.0.id"}),
    N("throttle", "cache.get", key="locpub:{{ auth.user_id }}"),
    N("thr_set", "cache.set", key="locpub:{{ auth.user_id }}", value={"ts": "{{ steps.recv.output }}"}, ttl=4),
    PUBLISH("pub", "{{ steps.job.output.0.kind }}:{{ steps.job.output.0.id }}", "driver.moved", {
        "lat": "{{ input.body.lat }}", "lng": "{{ input.body.lng }}", "heading": "{{ input.body.heading }}",
        "speed_kph": "{{ input.body.speed_kph }}", "ts": "{{ input.body.recorded_ts }}"}),
    # first movement after assignment upgrades driver_assigned → driver_en_route
    IF("upgrade", {"eq": ["$steps.job.output.0.status", "driver_assigned"]}),
    N("kind", "control.switch", value="{{ steps.job.output.0.kind }}", cases=["ride", "delivery"]),
    N("up_ride", "resource.update", resource="ride_requests", id="{{ steps.job.output.0.id }}",
      data={"status": "driver_en_route"}),
    N("up_del", "resource.update", resource="delivery_requests", id="{{ steps.job.output.0.id }}",
      data={"status": "driver_to_pickup"}),
    PUBLISH("pub2", "{{ steps.job.output.0.kind }}:{{ steps.job.output.0.id }}", "driver.en_route", {}),
    REPLY("reply", {"accepted": True}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "me"), ("me", "found"),
    ("found", "in_order", "true"), ("found", "no_profile", "false"),
    ("in_order", "recv", "true"), ("in_order", "stale_reply", "false"),
    ("recv", "upd"), ("upd", "log"), ("log", "sanity"),
    ("sanity", "risk_ev", "true"), ("risk_ev", "on_job"),
    ("sanity", "on_job", "false"),
    ("on_job", "job", "true"), ("on_job", "reply", "false"),
    ("job", "has_job"), ("has_job", "throttle", "true"), ("has_job", "reply", "false"),
    ("throttle", "upgrade", "hit"),          # published recently → skip publish, still maybe upgrade
    ("throttle", "thr_set", "miss"), ("thr_set", "pub"), ("pub", "upgrade"),
    ("upgrade", "kind", "true"), ("upgrade", "reply", "false"),
    ("kind", "up_ride", "ride"), ("kind", "up_del", "delivery"),
    ("up_ride", "pub2"), ("up_del", "pub2"), ("pub2", "reply"),
]
FLOWS.append(flow("driver_location", "Ingest a driver ping: out-of-order safe, staleness cleared, GPS sanity "
                                  "checked, active-job movement fanned out to the customer channel.",
                  _NODES, _EDGES, timeout=60))

# ── arrival at pickup ────────────────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/jobs/{type}/{id}/arrive"),
    N("auth", "auth.require", role="driver"),
    N("kind", "control.switch", value="{{ input.params.type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", open_states=["driver_assigned", "driver_en_route"],
        arrived="driver_arrived", event_prefix="ride"),
    SET("k_del", resource="delivery_requests", open_states=["driver_assigned", "driver_to_pickup"],
        arrived="driver_at_pickup", event_prefix="delivery"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.params.id }}"),
    ERR("gone", 404, "not_found", "No such job"),
    IF("mine", {"eq": ["$steps.req.output.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This job belongs to another driver"),
    IF("open", {"in": ["$steps.req.output.status", "$vars.open_states"]}),
    ERR("bad_state", 409, "invalid_state",
        "Cannot mark arrival from state {{ steps.req.output.status }}"),
    NOW("now", format="unix"),
    N("svc", "resource.list", resource="services", filters={"slug": "{{ steps.req.output.service }}"}, limit=1),
    CALC("bill_from", "add", ["{{ steps.now.output }}", "{{ steps.svc.output.data.0.waiting.free_seconds | default: 300 }}"]),
    CALC("timeout_at", "add", ["{{ steps.now.output }}",
                               "{{ steps.svc.output.data.0.waiting.max_billable_seconds | default: 900 }}"]),
    N("upd", "resource.update", resource="{{ vars.resource }}", id="{{ input.params.id }}", data={
        "status": "{{ vars.arrived }}", "pickup_arrived_ts": "{{ steps.now.output }}",
        "wait_billable_from_ts": "{{ steps.bill_from.output }}"}),
    N("drv", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    N("drv_upd", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "waiting_at_pickup"}),
    TIMELINE("tl", "{{ input.params.type }}", "{{ input.params.id }}", "driver.arrived",
             "Driver arrived at pickup; free waiting starts", actor="{{ auth.user_id }}"),
    NOTIFY_USER("ntf", "{{ steps.req.output.user_id }}", "{{ vars.event_prefix }}.driver_arrived",
                "Your driver has arrived", "Free waiting time has started."),
    PUBLISH("pub", "{{ input.params.type }}:{{ input.params.id }}", "driver.arrived",
            {"arrived_ts": "{{ steps.now.output }}",
             "wait_billable_from_ts": "{{ steps.bill_from.output }}"}),
    EMIT("ev", "{{ vars.event_prefix }}.driver_arrived", {"request_id": "{{ input.params.id }}"}),
    N("watch", "queue.flow", flow="pickup_watchdog", delay=900, input={
        "request_type": "{{ input.params.type }}", "request_id": "{{ input.params.id }}",
        "timeout_at": "{{ steps.timeout_at.output }}"}),
    REPLY("reply", {"status": "{{ vars.arrived }}",
                    "wait_billable_from_ts": "{{ steps.bill_from.output }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "kind"),
    ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "mine", "next"), ("req", "gone", "missing"),
    ("mine", "open", "true"), ("mine", "not_mine", "false"),
    ("open", "now", "true"), ("open", "bad_state", "false"),
    ("now", "svc"), ("svc", "bill_from"), ("bill_from", "timeout_at"), ("timeout_at", "upd"),
    ("upd", "drv"), ("drv", "drv_upd"), ("drv_upd", "tl"), ("tl", "ntf"), ("ntf", "pub"),
    ("pub", "ev"), ("ev", "watch"), ("watch", "reply"),
]
FLOWS.append(flow("driver_arrive", "Driver marks arrival at pickup: waiting-time accounting starts and the "
                                "customer is notified.",
                  _NODES, _EDGES, timeout=60))

# ── pickup watchdog → driver may declare no-show ─────────────────────────────

_NODES = [
    N("trig", "trigger.job"),
    N("kind", "control.switch", value="{{ input.request_type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", arrived="driver_arrived"),
    SET("k_del", resource="delivery_requests", arrived="driver_at_pickup"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.request_id }}"),
    N("fin", "control.stop"),
    IF("still_waiting", {"eq": ["$steps.req.output.status", "$vars.arrived"]}),
    NOTIFY_USER("ntf_drv", "{{ steps.req.output.driver_user_id }}", "job.pickup_timeout",
                "Pickup window elapsed", "The customer hasn't appeared — you may mark a no-show."),
    NOTIFY_USER("ntf_cust", "{{ steps.req.output.user_id }}", "job.pickup_timeout",
                "Your driver is still waiting", "Please meet your driver or the ride may be cancelled."),
    TIMELINE("tl", "{{ input.request_type }}", "{{ input.request_id }}", "pickup.timeout_reached",
             "Free pickup window elapsed; customer not present", actor="system"),
]
_EDGES = [
    ("trig", "kind"), ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "still_waiting", "next"), ("req", "fin", "missing"),
    ("still_waiting", "ntf_drv", "true"), ("still_waiting", "fin", "false"),
    ("ntf_drv", "ntf_cust"), ("ntf_cust", "tl"),
]
FLOWS.append(flow("pickup_watchdog", "After the pickup window elapses, arm the driver's no-show option and "
                                  "warn both parties.", _NODES, _EDGES, timeout=45))

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/jobs/{type}/{id}/no-show"),
    N("auth", "auth.require", role="driver"),
    N("kind", "control.switch", value="{{ input.params.type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", arrived="driver_arrived", event_prefix="ride"),
    SET("k_del", resource="delivery_requests", arrived="driver_at_pickup", event_prefix="delivery"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.params.id }}"),
    ERR("gone", 404, "not_found", "No such job"),
    IF("mine", {"eq": ["$steps.req.output.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This job belongs to another driver"),
    IF("waiting", {"eq": ["$steps.req.output.status", "$vars.arrived"]}),
    ERR("bad_state", 409, "invalid_state", "No-show is only available while waiting at pickup"),
    NOW("now", format="unix"),
    IF("past_window", {"gte": ["$steps.now.output", "$steps.req.output.wait_billable_from_ts | default: 0"]}),
    ERR("too_soon", 409, "too_early", "The free waiting window hasn't elapsed yet"),
    N("svc", "resource.list", resource="services", filters={"slug": "{{ steps.req.output.service }}"}, limit=1),
    # customer no-show: cancellation fee + driver compensation per service rules
    N("cancel", "resource.update", resource="{{ vars.resource }}", id="{{ input.params.id }}", data={
        "status": "cancelled",
        "cancellation": {"by": "customer_no_show", "reason": "customer_unavailable",
                         "fee_minor": "{{ steps.svc.output.data.0.cancellation.fee_after_arrival_minor | default: 0 }}",
                         "driver_comp_minor": "{{ steps.svc.output.data.0.cancellation.driver_comp_minor | default: 0 }}",
                         "ts": "{{ steps.now.output }"}}}),
    N("drv", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    N("drv_upd", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "available"}),
    N("asn", "resource.list", resource="assignments",
      filters={"request_type": "{{ input.params.type }}", "request_id": "{{ input.params.id }}", "state": "active"}, limit=1),
    IF("has_asn", {"truthy": "$steps.asn.output.total"}),
    N("asn_rel", "resource.update", resource="assignments", id="{{ steps.asn.output.data.0.id }}",
      data={"state": "completed", "released_ts": "{{ steps.now.output }}", "release_reason": "customer_no_show"}),
    TIMELINE("tl", "{{ input.params.type }}", "{{ input.params.id }}", "trip.cancelled_no_show",
             "Customer no-show; cancellation fee applies", actor="{{ auth.user_id }}"),
    NOTIFY_USER("ntf", "{{ steps.req.output.user_id }}", "{{ vars.event_prefix }}.cancelled",
                "Ride cancelled — no-show fee", "You weren't at the pickup; a no-show fee was charged."),
    PUBLISH("pub", "{{ input.params.type }}:{{ input.params.id }}", "{{ vars.event_prefix }}.cancelled",
            {"reason": "customer_no_show"}),
    EMIT("ev", "{{ vars.event_prefix }}.cancelled", {"request_id": "{{ input.params.id }}",
                                                     "reason": "customer_no_show"}),
    N("fee", "queue.flow", flow="payment_capture_fee", input={
        "subject_type": "{{ input.params.type }}", "subject_id": "{{ input.params.id }}",
        "kind": "no_show_fee"}),
    REPLY("reply", {"cancelled": True, "fee_minor":
                    "{{ steps.svc.output.data.0.cancellation.fee_after_arrival_minor | default: 0 }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "kind"),
    ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "mine", "next"), ("req", "gone", "missing"),
    ("mine", "waiting", "true"), ("mine", "not_mine", "false"),
    ("waiting", "now", "true"), ("waiting", "bad_state", "false"),
    ("now", "past_window"), ("past_window", "svc", "true"), ("past_window", "too_soon", "false"),
    ("svc", "cancel"), ("cancel", "drv"), ("drv", "drv_upd"), ("drv_upd", "asn"), ("asn", "has_asn"),
    ("has_asn", "asn_rel", "true"), ("asn_rel", "tl"),
    ("has_asn", "tl", "false"),
    ("tl", "ntf"), ("ntf", "pub"), ("pub", "ev"), ("ev", "fee"), ("fee", "reply"),
]
FLOWS.append(flow("driver_no_show", "Driver declares a customer no-show after the free window: fee + "
                                 "compensation per service rules, driver released.",
                  _NODES, _EDGES, timeout=60))

# ── trip start / stops / complete ────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/rides/{id}/start"),
    N("auth", "auth.require", role="driver"),
    N("req", "resource.get", resource="ride_requests", id="{{ input.params.id }}"),
    ERR("gone", 404, "not_found", "No such ride"),
    IF("mine", {"eq": ["$steps.req.output.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This ride belongs to another driver"),
    IF("ready", {"in": ["$steps.req.output.status", ["driver_arrived", "waiting_for_customer", "at_stop"]]}),
    ERR("bad_state", 409, "invalid_state", "Can't start from {{ steps.req.output.status }}"),
    NOW("now", format="unix"),
    # waiting time closes: any billable seconds become a fare line
    IF("was_waiting", {"all": [
        {"truthy": "$steps.req.output.wait_billable_from_ts"},
        {"gt": ["$steps.now.output", "$steps.req.output.wait_billable_from_ts"]}]}),
    CALC("wait_secs", "subtract", ["{{ steps.now.output }}", "{{ steps.req.output.wait_billable_from_ts }}"]),
    N("svc", "resource.list", resource="services", filters={"slug": "{{ steps.req.output.service }}"}, limit=1),
    CALC("wait_min", "divide", ["{{ steps.wait_secs.output }}", 60]),
    CALC("wait_fee_raw", "multiply", ["{{ steps.wait_min.output }}",
                                      "{{ steps.svc.output.data.0.waiting.per_minute_minor | default: 0 }}"]),
    CALC("wait_fee", "round", ["{{ steps.wait_fee_raw.output }}"], digits=0),
    N("upd0", "resource.update", resource="ride_requests", id="{{ input.params.id }}",
      data={"wait_charged_minor": "{{ steps.wait_fee.output }}"}),
    N("upd", "resource.update", resource="ride_requests", id="{{ input.params.id }}",
      data={"status": "in_progress", "started_ts": "{{ steps.now.output }}"}),
    N("drv", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    N("drv_upd", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "active_trip"}),
    TIMELINE("tl", "ride", "{{ input.params.id }}", "trip.started", "Trip started",
             {"wait_charged_minor": "{{ steps.wait_fee.output | default: 0 }}"}, actor="{{ auth.user_id }}"),
    NOTIFY_USER("ntf", "{{ steps.req.output.user_id }}", "ride.started", "Your trip has started", ""),
    PUBLISH("pub", "ride:{{ input.params.id }}", "ride.started", {"started_ts": "{{ steps.now.output }}"}),
    EMIT("ev", "ride.started", {"ride_id": "{{ input.params.id }}",
                                "wait_charged_minor": "{{ steps.wait_fee.output | default: 0 }}"}),
    REPLY("reply", {"status": "in_progress", "wait_charged_minor": "{{ steps.wait_fee.output | default: 0 }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "req"),
    ("req", "mine", "next"), ("req", "gone", "missing"),
    ("mine", "ready", "true"), ("mine", "not_mine", "false"),
    ("ready", "was_waiting", "true"), ("ready", "bad_state", "false"),
    ("was_waiting", "wait_secs", "true"), ("wait_secs", "svc"), ("svc", "wait_min"),
    ("wait_min", "wait_fee_raw"), ("wait_fee_raw", "wait_fee"), ("wait_fee", "upd0"), ("upd0", "upd"),
    ("was_waiting", "upd", "false"),
    ("upd", "drv"), ("drv", "drv_upd"), ("drv_upd", "tl"), ("tl", "ntf"), ("ntf", "pub"),
    ("pub", "ev"), ("ev", "reply"),
]
FLOWS.append(flow("trip_start", "Start the trip: billable waiting time is settled into the fare, "
                             "state moves to in_progress.", _NODES, _EDGES, timeout=60))

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/rides/{id}/stops/{seq}/arrive"),
    N("auth", "auth.require", role="driver"),
    N("stop", "resource.list", resource="ride_stops",
      filters={"ride_id": "{{ input.params.id }}", "sequence": "{{ input.params.seq }}"}, limit=1),
    IF("found", {"truthy": "$steps.stop.output.total"}),
    ERR("no_stop", 404, "stop_not_found", "No such stop"),
    IF("mine", {"eq": ["$steps.stop.output.data.0.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This ride belongs to another driver"),
    IF("pending", {"eq": ["$steps.stop.output.data.0.status", "pending"]}),
    ERR("done", 409, "invalid_state", "This stop is already handled"),
    NOW("now", format="unix"),
    N("upd", "resource.update", resource="ride_stops", id="{{ steps.stop.output.data.0.id }}",
      data={"status": "arrived", "arrived_ts": "{{ steps.now.output }}"}),
    N("ride", "resource.update", resource="ride_requests", id="{{ input.params.id }}", data={"status": "at_stop"}),
    N("drv", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    N("drv_upd", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "at_stop"}),
    TIMELINE("tl", "ride", "{{ input.params.id }}", "trip.stop_arrived",
             "Arrived at stop {{ input.params.seq }}", actor="{{ auth.user_id }}"),
    PUBLISH("pub", "ride:{{ input.params.id }}", "ride.at_stop", {"sequence": "{{ input.params.seq }}"}),
    REPLY("reply", {"stop": "{{ steps.upd.output }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "stop"), ("stop", "found"),
    ("found", "mine", "true"), ("found", "no_stop", "false"),
    ("mine", "pending", "true"), ("mine", "not_mine", "false"),
    ("pending", "now", "true"), ("pending", "done", "false"),
    ("now", "upd"), ("upd", "ride"), ("ride", "drv"), ("drv", "drv_upd"),
    ("drv_upd", "tl"), ("tl", "pub"), ("pub", "reply"),
]
FLOWS.append(flow("stop_arrive", "Driver arrives at an intermediate stop.", _NODES, _EDGES, timeout=45))

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/rides/{id}/stops/{seq}/complete"),
    N("auth", "auth.require", role="driver"),
    N("stop", "resource.list", resource="ride_stops",
      filters={"ride_id": "{{ input.params.id }}", "sequence": "{{ input.params.seq }}"}, limit=1),
    IF("found", {"truthy": "$steps.stop.output.total"}),
    ERR("no_stop", 404, "stop_not_found", "No such stop"),
    IF("mine", {"eq": ["$steps.stop.output.data.0.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This ride belongs to another driver"),
    IF("active", {"in": ["$steps.stop.output.data.0.status", ["arrived", "waiting"]]}),
    ERR("bad_state", 409, "invalid_state", "This stop isn't in progress"),
    NOW("now", format="unix"),
    CALC("wait_secs", "subtract", ["{{ steps.now.output }}", "{{ steps.stop.output.data.0.arrived_ts | default: 0 }}"]),
    N("svc", "resource.list", resource="services",
      filters={"slug": "{{ input.query.service | default: 'economy-ride' }}"}, limit=1),
    N("upd", "resource.update", resource="ride_stops", id="{{ steps.stop.output.data.0.id }}",
      data={"status": "completed", "completed_ts": "{{ steps.now.output }}",
            "wait_seconds": "{{ steps.wait_secs.output }}",
            "extra_charge_minor": "{{ steps.svc.output.data.0.stop_fee_minor | default: 0 }}"}),
    N("ride", "resource.update", resource="ride_requests", id="{{ input.params.id }}", data={"status": "in_progress"}),
    N("drv", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    N("drv_upd", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "active_trip"}),
    TIMELINE("tl", "ride", "{{ input.params.id }}", "trip.stop_completed",
             "Stop {{ input.params.seq }} completed", actor="{{ auth.user_id }}"),
    PUBLISH("pub", "ride:{{ input.params.id }}", "ride.resumed", {"sequence": "{{ input.params.seq }}"}),
    REPLY("reply", {"stop": "{{ steps.upd.output }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "stop"), ("stop", "found"),
    ("found", "mine", "true"), ("found", "no_stop", "false"),
    ("mine", "active", "true"), ("mine", "not_mine", "false"),
    ("active", "now", "true"), ("active", "bad_state", "false"),
    ("now", "wait_secs"), ("wait_secs", "svc"), ("svc", "upd"), ("upd", "ride"), ("ride", "drv"),
    ("drv", "drv_upd"), ("drv_upd", "tl"), ("tl", "pub"), ("pub", "reply"),
]
FLOWS.append(flow("stop_complete", "Complete an intermediate stop: wait time and stop fee recorded, trip resumes.",
                  _NODES, _EDGES, timeout=45))

_NODES = [
    N("trig", "trigger.http", method="POST", path="/driver/rides/{id}/complete"),
    N("auth", "auth.require", role="driver"),
    N("req", "resource.get", resource="ride_requests", id="{{ input.params.id }}"),
    ERR("gone", 404, "not_found", "No such ride"),
    IF("mine", {"eq": ["$steps.req.output.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This ride belongs to another driver"),
    IF("active", {"in": ["$steps.req.output.status", ["in_progress", "at_stop"]]}),
    ERR("bad_state", 409, "invalid_state", "Can't complete from {{ steps.req.output.status }}"),
    # final fare = estimate + waiting + stop extras + manual adjustments (tolls etc.)
    N("stops", "resource.list", resource="ride_stops",
      filters={"ride_id": "{{ input.params.id }}", "status": "completed"}, limit=50),
    N("stop_sum", "db.query",
      sql="SELECT COALESCE(SUM(extra_charge_minor), 0) AS extras FROM ride_stops WHERE ride_id = ?",
      params=["{{ input.params.id }}"]),
    CALC("fare_raw", "sum", [
        "{{ steps.req.output.estimate_minor | default: 0 }}",
        "{{ steps.req.output.wait_charged_minor | default: 0 }}",
        "{{ steps.stop_sum.output.0.extras }}",
        "{{ input.body.tolls_minor | default: 0 }}",
        "{{ input.body.adjustments_minor | default: 0 }}",
    ]),
    NOW("now", format="unix"),
    N("upd", "resource.update", resource="ride_requests", id="{{ input.params.id }}", data={
        "status": "completed", "completed_ts": "{{ steps.now.output }}",
        "final_fare_minor": "{{ steps.fare_raw.output }}",
        "fare_adjustments": {"waiting_minor": "{{ steps.req.output.wait_charged_minor | default: 0 }}",
                             "stops_minor": "{{ steps.stop_sum.output.0.extras }}",
                             "tolls_minor": "{{ input.body.tolls_minor | default: 0 }}",
                             "other_minor": "{{ input.body.adjustments_minor | default: 0 }}"}}),
    N("asn", "resource.list", resource="assignments",
      filters={"request_type": "ride", "request_id": "{{ input.params.id }}", "state": "active"}, limit=1),
    IF("has_asn", {"truthy": "$steps.asn.output.total"}),
    N("asn_done", "resource.update", resource="assignments", id="{{ steps.asn.output.data.0.id }}",
      data={"state": "completed", "released_ts": "{{ steps.now.output }}"}),
    N("drv", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    CALC("trips", "add", ["{{ steps.drv.output.data.0.trips_completed | default: 0 }}", 1]),
    N("drv_upd", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "available", "trips_completed": "{{ steps.trips.output }}"}),
    TIMELINE("tl", "ride", "{{ input.params.id }}", "trip.completed",
             "Trip completed — final fare {{ steps.fare_raw.output }} minor", {
                 "final_fare_minor": "{{ steps.fare_raw.output }}",
                 "estimate_minor": "{{ steps.req.output.estimate_minor }}"}, actor="{{ auth.user_id }}"),
    N("capture", "queue.flow", flow="payment_capture", input={
        "subject_type": "ride", "subject_id": "{{ input.params.id }}"}),
    N("earn", "queue.flow", flow="earnings_calc", input={
        "job_type": "ride", "job_id": "{{ input.params.id }}"}),
    NOTIFY_USER("ntf", "{{ steps.req.output.user_id }}", "ride.completed", "Trip completed",
                "Thanks for riding. Your receipt is ready.", link="/rides/{{ input.params.id }}"),
    PUBLISH("pub", "ride:{{ input.params.id }}", "ride.completed",
            {"final_fare_minor": "{{ steps.fare_raw.output }}"}),
    EMIT("ev", "ride.completed", {"ride_id": "{{ input.params.id }}",
                                  "driver_user_id": "{{ auth.user_id }}",
                                  "org_id": "{{ steps.req.output.org_id }}",
                                  "final_fare_minor": "{{ steps.fare_raw.output }}",
                                  "market": "{{ steps.req.output.market }}",
                                  "service": "{{ steps.req.output.service }}"}),
    REPLY("reply", {"status": "completed", "final_fare_minor": "{{ steps.fare_raw.output }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "req"),
    ("req", "mine", "next"), ("req", "gone", "missing"),
    ("mine", "active", "true"), ("mine", "not_mine", "false"),
    ("active", "stops", "true"), ("active", "bad_state", "false"),
    ("stops", "stop_sum"), ("stop_sum", "fare_raw"), ("fare_raw", "now"), ("now", "upd"),
    ("upd", "asn"), ("asn", "has_asn"),
    ("has_asn", "asn_done", "true"), ("asn_done", "drv"),
    ("has_asn", "drv", "false"),
    ("drv", "trips"), ("trips", "drv_upd"), ("drv_upd", "tl"), ("tl", "capture"),
    ("capture", "earn"), ("earn", "ntf"), ("ntf", "pub"), ("pub", "ev"), ("ev", "reply"),
]
FLOWS.append(flow("trip_complete", "Complete the trip: final fare settled (estimate + waiting + stops + "
                                "adjustments), payment capture and earnings queued.",
                  _NODES, _EDGES, timeout=90))

# ── customer cancellation with stage-based fees ──────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/rides/{id}/cancel"),
    N("auth", "auth.require"),
    N("req", "resource.get", resource="ride_requests", id="{{ input.params.id }}"),
    ERR("gone", 404, "not_found", "No such ride"),
    IF("mine", {"any": [
        {"eq": ["$steps.req.output.user_id", "$auth.user_id"]},
        {"role": ["ops_agent", "admin", "super_admin"]}]}),
    ERR("not_mine", 403, "forbidden", "Not your ride"),
    IF("open", {"not": {"in": ["$steps.req.output.status", ["completed", "cancelled", "failed", "unserviceable"]]}}),
    ERR("closed", 409, "invalid_state", "This ride is already {{ steps.req.output.status }}"),
    N("svc", "resource.list", resource="services", filters={"slug": "{{ steps.req.output.service }}"}, limit=1),
    NOW("now", format="unix"),
    # fee by stage: none while searching, fee after assignment, larger fee after arrival
    IF("was_assigned", {"in": ["$steps.req.output.status",
                                ["driver_assigned", "driver_en_route", "driver_arrived", "waiting_for_customer"]]}),
    SET("fee_vars", fee="{{ steps.svc.output.data.0.cancellation.fee_after_assign_minor | default: 0 }}",
        comp="{{ steps.svc.output.data.0.cancellation.driver_comp_minor | default: 0 }}"),
    IF("was_arrived", {"in": ["$steps.req.output.status", ["driver_arrived", "waiting_for_customer"]]}),
    SET("fee_vars2", fee="{{ steps.svc.output.data.0.cancellation.fee_after_arrival_minor | default: 0 }}",
        comp="{{ steps.svc.output.data.0.cancellation.driver_comp_minor | default: 0 }}"),
    SET("fee_zero", fee=0, comp=0),
    N("cancel", "resource.update", resource="ride_requests", id="{{ input.params.id }}", data={
        "status": "cancelled",
        "cancellation": {"by": "{{ auth.user_id }}", "reason": "{{ input.body.reason }}",
                         "note": "{{ input.body.note }}",
                         "fee_minor": "{{ vars.fee }}", "driver_comp_minor": "{{ vars.comp }}",
                         "ts": "{{ steps.now.output }}"}}),
    # release the driver when there was one
    IF("had_driver", {"truthy": "$steps.req.output.driver_user_id"}),
    N("drv", "resource.list", resource="driver_profiles",
      filters={"user_id": "{{ steps.req.output.driver_user_id }}"}, limit=1),
    IF("has_drv", {"truthy": "$steps.drv.output.total"}),
    N("drv_free", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "available"}),
    N("asn", "resource.list", resource="assignments",
      filters={"request_type": "ride", "request_id": "{{ input.params.id }}", "state": "active"}, limit=1),
    IF("has_asn", {"truthy": "$steps.asn.output.total"}),
    N("asn_rel", "resource.update", resource="assignments", id="{{ steps.asn.output.data.0.id }}",
      data={"state": "cancelled", "released_ts": "{{ steps.now.output }}",
            "release_reason": "customer_cancelled"}),
    NOTIFY_USER("drv_ntf", "{{ steps.req.output.driver_user_id }}", "ride.cancelled",
                "Ride cancelled", "The customer cancelled. Any compensation is on its way."),
    PUBLISH("drv_pub", "driver:{{ steps.req.output.driver_user_id }}", "job.cancelled",
            {"request_type": "ride", "request_id": "{{ input.params.id }}"}),
    TIMELINE("tl", "ride", "{{ input.params.id }}", "trip.cancelled",
             "Cancelled by customer ({{ input.body.reason }}) — fee {{ vars.fee }} minor",
             {"fee_minor": "{{ vars.fee }}", "stage": "{{ steps.req.output.status }}"},
             actor="{{ auth.user_id }}"),
    PUBLISH("pub", "ride:{{ input.params.id }}", "ride.cancelled", {"reason": "{{ input.body.reason }}"}),
    EMIT("ev", "ride.cancelled", {"ride_id": "{{ input.params.id }}", "by": "customer",
                                  "org_id": "{{ steps.req.output.org_id }}",
                                  "fee_minor": "{{ vars.fee }}"}),
    IF("has_fee", {"gt": ["$vars.fee", 0]}),
    N("fee", "queue.flow", flow="payment_capture_fee", input={
        "subject_type": "ride", "subject_id": "{{ input.params.id }}", "kind": "cancellation_fee"}),
    N("void", "queue.flow", flow="payment_void", input={
        "subject_type": "ride", "subject_id": "{{ input.params.id }}", "reason": "cancelled"}),
    REPLY("reply", {"cancelled": True, "fee_minor": "{{ vars.fee }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "req"),
    ("req", "mine", "next"), ("req", "gone", "missing"),
    ("mine", "open", "true"), ("mine", "not_mine", "false"),
    ("open", "svc", "true"), ("open", "closed", "false"),
    ("svc", "now"), ("now", "was_assigned"),
    ("was_assigned", "fee_vars", "true"), ("fee_vars", "was_arrived"),
    ("was_assigned", "fee_zero", "false"), ("fee_zero", "cancel"),
    ("was_arrived", "fee_vars2", "true"), ("fee_vars2", "cancel"),
    ("was_arrived", "cancel", "false"),
    ("cancel", "had_driver"),
    ("had_driver", "drv", "true"), ("drv", "has_drv"),
    ("has_drv", "drv_free", "true"), ("drv_free", "asn"),
    ("has_drv", "asn", "false"),
    ("asn", "has_asn"),
    ("has_asn", "asn_rel", "true"), ("asn_rel", "drv_ntf"),
    ("has_asn", "drv_ntf", "false"),
    ("drv_ntf", "drv_pub"), ("drv_pub", "tl"),
    ("had_driver", "tl", "false"),
    ("tl", "pub"), ("pub", "ev"), ("ev", "has_fee"),
    ("has_fee", "fee", "true"), ("fee", "void"),
    ("has_fee", "void", "false"),
    ("void", "reply"),
]
FLOWS.append(flow("ride_cancel", "Customer/ops cancellation: stage-based fee, driver release and compensation, "
                              "payment void or fee capture.", _NODES, _EDGES, timeout=90))

# ── in-job chat ──────────────────────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/jobs/{type}/{id}/chat"),
    N("auth", "auth.require"),
    N("kind", "control.switch", value="{{ input.params.type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests"),
    SET("k_del", resource="delivery_requests"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.params.id }}"),
    ERR("gone", 404, "not_found", "No such job"),
    IF("party", {"any": [
        {"eq": ["$steps.req.output.user_id", "$auth.user_id"]},
        {"eq": ["$steps.req.output.driver_user_id", "$auth.user_id"]},
        {"role": ["support_agent", "ops_agent", "admin", "super_admin"]}]}),
    ERR("not_party", 403, "forbidden", "Only the job's parties can chat"),
    N("me", "auth.user"),
    N("msg", "resource.create", resource="chat_messages", data={
        "job_type": "{{ input.params.type }}", "job_id": "{{ input.params.id }}",
        "user_id": "{{ steps.req.output.user_id }}", "driver_user_id": "{{ steps.req.output.driver_user_id }}",
        "sender_id": "{{ auth.user_id }}",
        "sender_role": "{{ auth.roles.0 | default: 'user' }}",
        "body": "{{ input.body.message }}"}),
    PUBLISH("pub", "chat:{{ input.params.type }}:{{ input.params.id }}", "chat.message",
            {"message": "{{ steps.msg.output }}"}),
    # notify the other party
    IF("from_customer", {"eq": ["$auth.user_id", "$steps.req.output.user_id"]}),
    NOTIFY_USER("ntf_drv", "{{ steps.req.output.driver_user_id }}", "chat.message",
                "New message", "{{ input.body.message }}", link="/jobs/{{ input.params.type }}/{{ input.params.id }}"),
    NOTIFY_USER("ntf_cust", "{{ steps.req.output.user_id }}", "chat.message",
                "Message from your driver", "{{ input.body.message }}",
                link="/jobs/{{ input.params.type }}/{{ input.params.id }}"),
    REPLY("reply", {"message": "{{ steps.msg.output }}"}, status=201),
]
_EDGES = [
    ("trig", "auth"), ("auth", "kind"),
    ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "party", "next"), ("req", "gone", "missing"),
    ("party", "me", "true"), ("party", "not_party", "false"),
    ("me", "msg"), ("msg", "pub"), ("pub", "from_customer"),
    ("from_customer", "ntf_drv", "true"), ("ntf_drv", "reply"),
    ("from_customer", "ntf_cust", "false"), ("ntf_cust", "reply"),
]
FLOWS.append(flow("chat_send", "In-job messaging between customer, driver and support.", _NODES, _EDGES, timeout=45))

# ── public tracking link ─────────────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="GET", path="/track/{token}"),
    N("ride", "resource.list", resource="ride_requests",
      filters={"share_token": "{{ input.params.token }}"}, limit=1),
    IF("found", {"truthy": "$steps.ride.output.total"}),
    N("del", "resource.list", resource="delivery_requests",
      filters={"share_token": "{{ input.params.token }}"}, limit=1),
    IF("found2", {"truthy": "$steps.del.output.total"}),
    ERR("gone", 404, "not_found", "This tracking link is invalid"),
    N("me", "resource.list", resource="driver_profiles",
      filters={"user_id": "{{ steps.ride.output.data.0.driver_user_id | default: steps.del.output.data.0.driver_user_id }}"},
      limit=1),
    REPLY("reply", {
        "kind": "ride",
        "status": "{{ steps.ride.output.data.0.status }}",
        "pickup": "{{ steps.ride.output.data.0.pickup }}",
        "dropoff": "{{ steps.ride.output.data.0.dropoff }}",
        "driver": {"full_name": "{{ steps.me.output.data.0.full_name }}",
                   "vehicle_description": "{{ steps.me.output.data.0.vehicle_description }}",
                   "vehicle_plate": "{{ steps.me.output.data.0.vehicle_plate }}",
                   "last_lat": "{{ steps.me.output.data.0.last_lat }}",
                   "last_lng": "{{ steps.me.output.data.0.last_lng }}"},
        "share_token": "{{ input.params.token }}",
    }),
    REPLY("reply2", {
        "kind": "delivery",
        "status": "{{ steps.del.output.data.0.status }}",
        "pickup": "{{ steps.del.output.data.0.pickup }}",
        "dropoff": "{{ steps.del.output.data.0.dropoff }}",
        "driver": {"full_name": "{{ steps.me.output.data.0.full_name }}",
                   "vehicle_description": "{{ steps.me.output.data.0.vehicle_description }}",
                   "vehicle_plate": "{{ steps.me.output.data.0.vehicle_plate }}",
                   "last_lat": "{{ steps.me.output.data.0.last_lat }}",
                   "last_lng": "{{ steps.me.output.data.0.last_lng }}"},
        "share_token": "{{ input.params.token }}",
    }),
]
_EDGES = [
    ("trig", "ride"), ("ride", "found"),
    ("found", "me", "true"),
    ("found", "del", "false"), ("del", "found2"),
    ("found2", "me", "true"), ("found2", "gone", "false"),
    ("me", "reply"),
]
FLOWS.append(flow("share_track", "Public, token-gated live tracking page data (no sign-in).",
                  _NODES, _EDGES, timeout=45))
