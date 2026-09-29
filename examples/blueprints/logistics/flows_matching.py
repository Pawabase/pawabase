"""Matching: estimates, ride creation, the round-based matching engine, accepts,
rejects, cancellations and reassignment.

The engine is deliberately observable: every round is a ``matching_rounds`` row,
every offer an ``offers`` row, every race decided by the unique
``assignment_claims`` row for the request.
"""

from helpers import (
    ALL, ANY, E, EQ, ERR, EXISTS, GT, IF, IN, NE, NOT, NOW, N, SET, CALC, EMIT,
    LOG, NOTIFY_USER, PUBLISH, REPLY, TIMELINE, TRUTHY, flow,
)
from flows_util import (
    CANDIDATES_SQL, ZONE_FEE_SQL, ZONE_SQL, disruption_guard, pricing_core_fragment,
    pricing_total_fragment, promo_fragment, surge_fragment,
)

FLOWS = []


def _chain(nodes, edges, seq):
    for a, b in zip(seq, seq[1:]):
        edges.append((a, b))


# ── shared pricing subgraph (estimate & ride/delivery create) ────────────────


def quote_subgraph(p, *, pickup_lat, pickup_lng, dropoff_lat, dropoff_lng,
                   market_tpl, service_tpl, promo_tpl, free_delivery_ok=False,
                   stops_count="0", weight="0", scheduled="false", priority="false"):
    """Resolve config → distance → zones → surge → promo → total.

    Template strings are passed in so the same math serves estimates, rides and
    deliveries. Leaves: vars.market, vars.service, vars.rule, vars.market_row,
    vars.service_row, vars.distance_km, vars.duration_min, vars.zone_fee_minor,
    vars.airport_fee_minor, vars.discount_minor, vars.quote, vars.total_minor.
    """
    nodes = [
        SET(f"{p}_in", market=market_tpl, service=service_tpl,
            zone_fee_minor=0, airport_fee_minor=0, discount_minor=0,
            stops_count=stops_count, weight_kg=weight, scheduled=scheduled, priority=priority),
        N(f"{p}_market", "resource.list", resource="markets",
          filters={"slug": market_tpl, "active": True}, limit=1),
        IF(f"{p}_market_ok", {"truthy": f"$steps.{p}_market.output.total"}),
        ERR(f"{p}_no_market", 422, "unknown_market", "We don't operate there yet"),
        SET(f"{p}_market_set", market_row=f"{{{{ steps.{p}_market.output.data.0 }}}}"),
        N(f"{p}_svc", "resource.list", resource="services",
          filters={"slug": service_tpl, "active": True}, limit=1),
        IF(f"{p}_svc_ok", {"truthy": f"$steps.{p}_svc.output.total"}),
        ERR(f"{p}_no_svc", 404, "unknown_service", "No such service"),
        SET(f"{p}_svc_set", service_row=f"{{{{ steps.{p}_svc.output.data.0 }}}}"),
        N(f"{p}_rule", "resource.list", resource="pricing_rules",
          filters={"market": market_tpl, "service": service_tpl, "active": True}, limit=1),
        IF(f"{p}_rule_ok", {"truthy": f"$steps.{p}_rule.output.total"}),
        ERR(f"{p}_no_rule", 422, "no_price", "This service isn't priced here yet"),
        SET(f"{p}_rule_set", rule=f"{{{{ steps.{p}_rule.output.data.0 }}}}"),
        # distance: |dlat| * 111.32 + |dlng| * market scale  (abs via max(x, -x))
        CALC(f"{p}_dlat", "subtract", [pickup_lat, dropoff_lat], digits=6),
        CALC(f"{p}_dlat_neg", "multiply", [f"{{{{ steps.{p}_dlat.output }}}}", -1], digits=6),
        CALC(f"{p}_dlat_abs", "max", [f"{{{{ steps.{p}_dlat.output }}}}", f"{{{{ steps.{p}_dlat_neg.output }}}}"], digits=6),
        CALC(f"{p}_dlng", "subtract", [pickup_lng, dropoff_lng], digits=6),
        CALC(f"{p}_dlng_neg", "multiply", [f"{{{{ steps.{p}_dlng.output }}}}", -1], digits=6),
        CALC(f"{p}_dlng_abs", "max", [f"{{{{ steps.{p}_dlng.output }}}}", f"{{{{ steps.{p}_dlng_neg.output }}}}"], digits=6),
        CALC(f"{p}_km_lat", "multiply", [f"{{{{ steps.{p}_dlat_abs.output }}}}", 111.32], digits=3),
        CALC(f"{p}_km_lng", "multiply", [f"{{{{ steps.{p}_dlng_abs.output }}}}",
                                         "{{ vars.market_row.km_per_deg_lng }}"], digits=3),
        CALC(f"{p}_km", "add", [f"{{{{ steps.{p}_km_lat.output }}}}", f"{{{{ steps.{p}_km_lng.output }}}}"], digits=2),
        SET(f"{p}_km_set", distance_km=f"{{{{ steps.{p}_km.output }}}}"),
        CALC(f"{p}_mins_raw", "multiply", [f"{{{{ steps.{p}_km.output }}}}", 60], digits=0),
        CALC(f"{p}_mins", "divide", [f"{{{{ steps.{p}_mins_raw.output }}}}", 28], digits=0),
        SET(f"{p}_mins_set", duration_min=f"{{{{ steps.{p}_mins.output }}}}"),
        # zones
        N(f"{p}_zone_pk", "db.query", sql=ZONE_SQL,
          params=[market_tpl, pickup_lat, pickup_lat, pickup_lng, pickup_lng]),
        IF(f"{p}_zone_pk_hit", {"truthy": f"$steps.{p}_zone_pk.output.0.slug"}),
        N(f"{p}_fee_pk", "db.query", sql=ZONE_FEE_SQL,
          params=[market_tpl, service_tpl, f"{{{{ steps.{p}_zone_pk.output.0.slug }}}}"]),
        CALC(f"{p}_fee_pk_sum", "sum", [f"{{{{ steps.{p}_fee_pk.output.0.pickup_fee_minor | default: 0 }}}}"], digits=0),
        SET(f"{p}_fee_pk_set", zone_fee_minor=f"{{{{ steps.{p}_fee_pk_sum.output }}}}"),
        IF(f"{p}_pk_airport", {"eq": [f"$steps.{p}_zone_pk.output.0.kind", "airport"]}),
        SET(f"{p}_pk_airport_fee", airport_fee_minor="{{ vars.rule.airport_fee_minor }}"),
        N(f"{p}_zone_dp", "db.query", sql=ZONE_SQL,
          params=[market_tpl, dropoff_lat, dropoff_lat, dropoff_lng, dropoff_lng]),
        IF(f"{p}_zone_dp_hit", {"truthy": f"$steps.{p}_zone_dp.output.0.slug"}),
        N(f"{p}_fee_dp", "db.query", sql=ZONE_FEE_SQL,
          params=[market_tpl, service_tpl, f"{{{{ steps.{p}_zone_dp.output.0.slug }}}}"]),
        CALC(f"{p}_fee_total", "sum", ["{{ vars.zone_fee_minor }}",
                                       f"{{{{ steps.{p}_fee_dp.output.0.dropoff_fee_minor | default: 0 }}}}"], digits=0),
        SET(f"{p}_fee_total_set", zone_fee_minor=f"{{{{ steps.{p}_fee_total.output }}}}"),
        IF(f"{p}_dp_airport", {"eq": [f"$steps.{p}_zone_dp.output.0.kind", "airport"]}),
        SET(f"{p}_dp_airport_fee", airport_fee_minor="{{ vars.rule.airport_fee_minor }}"),
    ]
    edges = [
        (f"{p}_in", f"{p}_market"), (f"{p}_market", f"{p}_market_ok"),
        (f"{p}_market_ok", f"{p}_no_market", "false"), (f"{p}_market_ok", f"{p}_market_set", "true"),
        (f"{p}_market_set", f"{p}_svc"), (f"{p}_svc", f"{p}_svc_ok"),
        (f"{p}_svc_ok", f"{p}_no_svc", "false"), (f"{p}_svc_ok", f"{p}_svc_set", "true"),
        (f"{p}_svc_set", f"{p}_rule"), (f"{p}_rule", f"{p}_rule_ok"),
        (f"{p}_rule_ok", f"{p}_no_rule", "false"), (f"{p}_rule_ok", f"{p}_rule_set", "true"),
        (f"{p}_rule_set", f"{p}_dlat"),
    ]
    seq1 = [f"{p}_dlat", f"{p}_dlat_neg", f"{p}_dlat_abs", f"{p}_dlng", f"{p}_dlng_neg",
            f"{p}_dlng_abs", f"{p}_km_lat", f"{p}_km_lng", f"{p}_km", f"{p}_km_set",
            f"{p}_mins_raw", f"{p}_mins", f"{p}_mins_set"]
    _chain(nodes, edges, seq1)
    edges += [
        (f"{p}_mins_set", f"{p}_zone_pk"), (f"{p}_zone_pk", f"{p}_zone_pk_hit"),
        (f"{p}_zone_pk_hit", f"{p}_fee_pk", "true"), (f"{p}_fee_pk", f"{p}_fee_pk_sum"),
        (f"{p}_fee_pk_sum", f"{p}_fee_pk_set"), (f"{p}_fee_pk_set", f"{p}_pk_airport"),
        (f"{p}_zone_pk_hit", f"{p}_pk_airport", "false"),
        (f"{p}_pk_airport", f"{p}_pk_airport_fee", "true"), (f"{p}_pk_airport", f"{p}_zone_dp", "false"),
        (f"{p}_pk_airport_fee", f"{p}_zone_dp"),
        (f"{p}_zone_dp", f"{p}_zone_dp_hit"),
        (f"{p}_zone_dp_hit", f"{p}_fee_dp", "true"), (f"{p}_fee_dp", f"{p}_fee_total"),
        (f"{p}_fee_total", f"{p}_fee_total_set"), (f"{p}_fee_total_set", f"{p}_dp_airport"),
        (f"{p}_zone_dp_hit", f"{p}_dp_airport", "false"),
        (f"{p}_dp_airport", f"{p}_dp_airport_fee", "true"),
    ]
    last_zone = f"{p}_dp_airport"
    # surge
    sn, se, s_last = surge_fragment(f"{p}_surge", market_tpl)
    nodes += sn
    edges += [(last_zone, f"{p}_surge_default"), (f"{p}_dp_airport_fee", f"{p}_surge_default")] + se
    # core charges
    cn, ce, c_last = pricing_core_fragment(f"{p}_core")
    nodes += cn
    edges += [(s_last, f"{p}_core_has_sched")] + ce
    # promo
    pn, pe, pr_last = promo_fragment(f"{p}_promo", promo_tpl, free_delivery_ok=free_delivery_ok)
    nodes += pn
    edges += [(c_last, f"{p}_promo_zero")] + pe
    # total
    tn, te, t_last = pricing_total_fragment(f"{p}_total")
    nodes += tn
    edges += [(pr_last, f"{p}_total_disc")] + te
    return nodes, edges, t_last


# ── estimate ─────────────────────────────────────────────────────────────────

_p = "e"
_nodes = [N("trig", "trigger.http", method="POST", path="/pricing/estimate")]
_nodes += [
    SET("e_in_flags", scheduled="{{ input.body.scheduled_at | default: false }}",
        priority="{{ input.body.priority | default: false }}"),
]
_qn, _qe, _q_last = quote_subgraph(
    "e_q",
    pickup_lat="{{ input.body.pickup_lat }}", pickup_lng="{{ input.body.pickup_lng }}",
    dropoff_lat="{{ input.body.dropoff_lat | default: input.body.pickup_lat }}",
    dropoff_lng="{{ input.body.dropoff_lng | default: input.body.pickup_lng }}",
    market_tpl="{{ input.body.market | default: 'lagos' }}",
    service_tpl="{{ input.body.service }}",
    promo_tpl="{{ input.body.promo_code }}",
    stops_count="{{ input.body.stops | length }}",
    weight="{{ input.body.package_weight_kg | default: 0 }}",
)
_nodes += _qn
_nodes += [
    NOW("e_now", format="unix", offset=900),
    N("e_save", "resource.create", resource="estimates", data={
        "user_id": "{{ auth.user_id }}",
        "kind": "{{ vars.service_row.kind }}",
        "service": "{{ vars.service }}",
        "market": "{{ vars.market }}",
        "pickup": {"lat": "{{ input.body.pickup_lat }}", "lng": "{{ input.body.pickup_lng }}"},
        "dropoff": {"lat": "{{ input.body.dropoff_lat }}", "lng": "{{ input.body.dropoff_lng }}"},
        "stops": "{{ input.body.stops }}",
        "distance_km": "{{ vars.distance_km }}",
        "duration_min": "{{ vars.duration_min }}",
        "surge": "{{ vars.surge }}",
        "breakdown": "{{ vars.quote }}",
        "total_minor": "{{ vars.total_minor }}",
        "currency": "{{ vars.rule.currency }}",
        "promo_code": "{{ vars.promo }}",
        "discount_minor": "{{ vars.discount_minor }}",
        "expires_ts": "{{ steps.e_now.output }}",
    }),
    REPLY("e_reply", {"estimate": "{{ steps.e_save.output }}", "quote": "{{ vars.quote }}",
                      "distance_km": "{{ vars.distance_km }}", "duration_min": "{{ vars.duration_min }}",
                      "surge": "{{ vars.surge }}"}),
]
_edges = [("trig", "e_in_flags"), ("e_in_flags", "e_q_in")] + _qe + [
    (_q_last, "e_now"), ("e_now", "e_save"), ("e_save", "e_reply"),
]
FLOWS.append(flow("fare_estimate", "Price a ride or delivery before booking; stores the quote for confirmation.",
                  _nodes, _edges))

# ── ride create ──────────────────────────────────────────────────────────────

_nodes = [
    N("trig", "trigger.http", method="POST", path="/rides"),
    N("auth", "auth.require", role="customer"),
    # idempotent confirm: the same key returns the same ride
    IF("has_idem", {"truthy": "{{ input.body.idempotency_key }}"}),
    N("idem_find", "resource.list", resource="ride_requests",
      filters={"user_id": "{{ auth.user_id }}", "idempotency_key": "{{ input.body.idempotency_key }}"}, limit=1),
    IF("idem_hit", {"truthy": "$steps.idem_find.output.total"}),
    REPLY("idem_reply", {"ride": "{{ steps.idem_find.output.data.0 }}", "idempotent_replay": True}, status=200),
    # estimate handoff when given
    IF("has_est", {"truthy": "{{ input.body.estimate_id }}"}),
    N("est_load", "resource.get", resource="estimates", id="{{ input.body.estimate_id }}"),
    ERR("est_missing", 422, "estimate_expired", "That estimate has expired; price the ride again"),
    NOW("now", format="unix"),
    IF("est_valid", {"all": [
        {"eq": ["$steps.est_load.output.user_id", "$auth.user_id"]},
        {"gt": ["$steps.est_load.output.expires_ts", "$steps.now.output"]}]}),
    ERR("est_bad", 422, "estimate_expired", "That estimate has expired; price the ride again"),
    SET("use_est", total_minor="{{ steps.est_load.output.total_minor }}",
        quote="{{ steps.est_load.output.breakdown }}", surge="{{ steps.est_load.output.surge }}",
        distance_km="{{ steps.est_load.output.distance_km }}", duration_min="{{ steps.est_load.output.duration_min }}",
        market="{{ steps.est_load.output.market }}", service="{{ input.body.service }}",
        promo="{{ steps.est_load.output.promo_code }}"),
]
_edges = [
    ("trig", "auth"), ("auth", "has_idem"),
    ("has_idem", "idem_find", "true"), ("idem_find", "idem_hit"),
    ("idem_hit", "idem_reply", "true"), ("idem_hit", "has_est", "false"),
    ("has_idem", "has_est", "false"),
    ("has_est", "est_load", "true"),
    ("est_load", "now", "next"), ("est_load", "est_missing", "missing"),
    ("now", "est_valid"),
    ("est_valid", "use_est", "true"), ("est_valid", "est_bad", "false"),
]
# no estimate → price inline from the body
_qn, _qe, _q_last = quote_subgraph(
    "rc_q",
    pickup_lat="{{ input.body.pickup.lat }}", pickup_lng="{{ input.body.pickup.lng }}",
    dropoff_lat="{{ input.body.dropoff.lat }}", dropoff_lng="{{ input.body.dropoff.lng }}",
    market_tpl="{{ input.body.market | default: 'lagos' }}",
    service_tpl="{{ input.body.service }}",
    promo_tpl="{{ input.body.promo_code }}",
    stops_count="{{ input.body.stops | length }}",
    scheduled="{{ input.body.scheduled_at | default: false }}",
)
_nodes += _qn
_edges += [("has_est", "rc_q_in", "false")] + _qe
# disruption guard, then create
_dn, _de, _d_last = disruption_guard("rc_d", "{{ vars.service }}")
_nodes += _dn
_edges += [("use_est", "rc_d_svc_flag"), (_q_last, "rc_d_svc_flag")] + _de
_nodes += [
    # share token + timestamps
    N("tok", "util.id", kind="token", length=24),
    NOW("now2", format="unix"),
    IF("is_sched", {"truthy": "{{ input.body.scheduled_ts }}"}),
    # scheduled: enters 'scheduled' with a dispatch lead time (15 min)
    CALC("lead", "subtract", ["{{ input.body.scheduled_ts | default: 0 }}", 900]),
    SET("sched_vars", status="scheduled", dispatch_ts="{{ steps.lead.output }}"),
    SET("live_vars", status="searching", dispatch_ts="{{ steps.now2.output }}"),
    N("create", "resource.create", resource="ride_requests", data={
        "user_id": "{{ auth.user_id }}",
        "org_id": "{{ input.body.org_id }}",
        "service": "{{ input.body.service }}",
        "market": "{{ vars.market }}",
        "status": "{{ vars.status }}",
        "pickup": "{{ input.body.pickup }}",
        "dropoff": "{{ input.body.dropoff }}",
        "stops": "{{ input.body.stops }}",
        "passengers": "{{ input.body.passengers }}",
        "accessibility": "{{ input.body.accessibility }}",
        "pickup_notes": "{{ input.body.pickup_notes }}",
        "payment_method": "{{ input.body.payment_method }}",
        "payment_method_id": "{{ input.body.payment_method_id }}",
        "promo_code": "{{ vars.promo }}",
        "estimate_id": "{{ input.body.estimate_id }}",
        "estimate_minor": "{{ vars.total_minor }}",
        "estimate_snapshot": "{{ vars.quote }}",
        "surge": "{{ vars.surge }}",
        "distance_km": "{{ vars.distance_km }}",
        "duration_min": "{{ vars.duration_min }}",
        "currency": "{{ vars.quote.currency | default: vars.rule.currency }}",
        "scheduled_at": "{{ input.body.scheduled_at | default: input.body.scheduled_ts }}",
        "dispatch_ts": "{{ vars.dispatch_ts }}",
        "matching_round": 0,
        "idempotency_key": "{{ input.body.idempotency_key }}",
        "share_token": "{{ steps.tok.output }}",
        "created_via": "app",
    }),
    # stops as their own rows
    N("mk_stops", "control.foreach", items="{{ input.body.stops }}"),
    N("stop_row", "resource.create", resource="ride_stops", data={
        "ride_id": "{{ steps.create.output.id }}",
        "user_id": "{{ auth.user_id }}",
        "sequence": "{{ item.sequence | default: index }}",
        "address": "{{ item.address }}",
        "instructions": "{{ item.instructions }}",
        "contact_name": "{{ item.contact_name }}",
        "contact_phone": "{{ item.contact_phone }}",
    }),
    TIMELINE("tl", "ride", "{{ steps.create.output.id }}", "ride.requested",
             "Ride requested — {{ input.body.service }}", {
                 "service": "{{ input.body.service }}", "estimate_minor": "{{ vars.total_minor }}",
                 "scheduled_at": "{{ input.body.scheduled_at }}"}),
    # payment authorization runs alongside matching
    N("pay", "queue.flow", flow="payment_authorize", input={
        "subject_type": "ride", "subject_id": "{{ steps.create.output.id }}"}),
    # scheduled rides wait for the dispatch sweep; live rides match now
    IF("go_match", {"eq": ["$vars.status", "searching"]}),
    N("match", "queue.flow", flow="matching_round", input={
        "request_type": "ride", "request_id": "{{ steps.create.output.id }}", "round": 1}),
    EMIT("ev_sched", "ride.scheduled", {"ride_id": "{{ steps.create.output.id }}",
                                        "scheduled_at": "{{ input.body.scheduled_at }}"}),
    NOTIFY_USER("ntf", "{{ auth.user_id }}", "ride.requested", "Ride confirmed",
                "We're finding your driver." , link="/rides/{{ steps.create.output.id }}"),
    REPLY("reply", {"ride": "{{ steps.create.output }}", "quote": "{{ vars.quote }}"}, status=201),
]
_edges += [
    (_d_last, "tok"), ("tok", "now2"), ("now2", "is_sched"),
    ("is_sched", "lead", "true"), ("lead", "sched_vars"),
    ("is_sched", "live_vars", "false"),
    ("sched_vars", "create"), ("live_vars", "create"),
    ("create", "mk_stops"),
    ("mk_stops", "stop_row", "each"), ("mk_stops", "tl", "done"),
    ("tl", "pay"), ("pay", "go_match"),
    ("go_match", "match", "true"), ("match", "ntf"),
    ("go_match", "ev_sched", "false"), ("ev_sched", "ntf"),
    ("ntf", "reply"),
]
FLOWS.append(flow("ride_create", "Confirm a ride: price (or reuse an estimate), guard disruptions, "
                               "create the request, start payment authorization and matching.",
                  _nodes, _edges, timeout=120))

# ── matching engine: one round ───────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.job"),
    # branch: ride or delivery
    N("kind", "control.switch", value="{{ input.request_type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", searching="searching", assigned="driver_assigned",
        dead="unserviceable", event_prefix="ride"),
    SET("k_del", resource="delivery_requests", searching="searching_driver", assigned="driver_assigned",
        dead="delivery_failed", event_prefix="delivery"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.request_id }}"),
    ERR("gone", 404, "request_gone", "The request no longer exists"),
    # only an open, still-searching request enters a round
    IF("still_open", {"eq": ["$steps.req.output.status", "$vars.searching"]}),
    N("svc", "resource.list", resource="services", filters={"slug": "{{ steps.req.output.service }}"}, limit=1),
    SET("svc_set", svc="{{ steps.svc.output.data.0 }}"),
    N("mkt", "resource.list", resource="markets", filters={"slug": "{{ steps.req.output.market }}"}, limit=1),
    SET("mkt_set", market_row="{{ steps.mkt.output.data.0 }}"),
    # round configuration, four rounds with widening reach
    SET("cfg", radius_km="{{ vars.svc.matching.r1_radius_km }}", offer_seconds="{{ vars.svc.matching.r1_offer_seconds }}",
        pool="{{ vars.svc.matching.r1_pool }}", min_rating="{{ vars.svc.matching.r1_min_rating }}",
        strategy="nearest_highly_suitable"),
    IF("r2", {"eq": ["{{ input.round }}", 2]}),
    SET("cfg2", radius_km="{{ vars.svc.matching.r2_radius_km }}", offer_seconds="{{ vars.svc.matching.r2_offer_seconds }}",
        pool="{{ vars.svc.matching.r2_pool }}", min_rating="{{ vars.svc.matching.r2_min_rating }}",
        strategy="expanded_radius"),
    IF("r3", {"eq": ["{{ input.round }}", 3]}),
    SET("cfg3", radius_km="{{ vars.svc.matching.r3_radius_km }}", offer_seconds="{{ vars.svc.matching.r3_offer_seconds }}",
        pool="{{ vars.svc.matching.r3_pool }}", min_rating="{{ vars.svc.matching.r3_min_rating }}",
        strategy="broad_pool"),
    IF("r4", {"gte": ["{{ input.round }}", 4]}),
    SET("cfg4", radius_km="{{ vars.svc.matching.r4_radius_km }}", offer_seconds="{{ vars.svc.matching.r4_offer_seconds }}",
        pool="{{ vars.svc.matching.r4_pool }}", min_rating="{{ vars.svc.matching.r4_min_rating }}",
        strategy="final_strategy"),
    # geography for the radius
    CALC("rad_lat", "divide", ["{{ vars.radius_km }}", 111.32], digits=6),
    CALC("rad_lng", "divide", ["{{ vars.radius_km }}", "{{ vars.market_row.km_per_deg_lng | default: 111.32 }}"], digits=6),
    CALC("lat_min", "subtract", ["{{ steps.req.output.pickup.lat }}", "{{ steps.rad_lat.output }}"], digits=6),
    CALC("lat_max", "add", ["{{ steps.req.output.pickup.lat }}", "{{ steps.rad_lat.output }}"], digits=6),
    CALC("lng_min", "subtract", ["{{ steps.req.output.pickup.lng }}", "{{ steps.rad_lng.output }}"], digits=6),
    CALC("lng_max", "add", ["{{ steps.req.output.pickup.lng }}", "{{ steps.rad_lng.output }}"], digits=6),
    NOW("stale", format="unix", offset=-180),
    N("cand", "db.query", sql=CANDIDATES_SQL, params=[
        "{{ steps.req.output.pickup.lat }}", "{{ vars.market_row.km_per_deg_lng | default: 111.32 }}",
        "{{ steps.req.output.pickup.lng }}",
        "{{ steps.req.output.pickup.lat }}", "{{ vars.market_row.km_per_deg_lng | default: 111.32 }}",
        "{{ steps.req.output.pickup.lng }}",
        "{{ steps.req.output.market }}", "{{ steps.stale.output }}",
        "{{ vars.svc.min_class_rank }}", "{{ vars.svc.max_class_rank }}",
        "{{ vars.min_rating }}", "{{ steps.lat_min.output }}", "{{ steps.lat_max.output }}",
        "{{ steps.lng_min.output }}", "{{ steps.lng_max.output }}",
        "{{ input.request_type }}", "{{ input.request_id }}", "{{ vars.pool }}",
    ]),
    # record the round
    NOW("rnow", format="unix"),
    N("round_row", "resource.create", resource="matching_rounds", data={
        "request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
        "round": "{{ input.round }}", "strategy": "{{ vars.strategy }}",
        "radius_km": "{{ vars.radius_km }}", "candidates": "{{ steps.cand.output | length }}",
        "offered": "{{ steps.cand.output | length }}", "started_ts": "{{ steps.rnow.output }",
    }),
    # no candidates → close as exhausted immediately
    IF("any_cand", {"truthy": "{{ steps.cand.output | length }}"}),
    # offers
    NOW("onow", format="unix"),
    CALC("exp", "add", ["{{ steps.onow.output }}", "{{ vars.offer_seconds }}"]),
    SET("exp_set", expires_ts="{{ steps.exp.output }}", offered_ts="{{ steps.onow.output }}"),
    N("offer_loop", "control.foreach", items="{{ steps.cand.output }}"),
    N("offer_mk", "resource.create", resource="offers", data={
        "request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
        "round": "{{ input.round }}", "driver_user_id": "{{ item.user_id }}", "driver_id": "{{ item.id }}",
        "score": "{{ item.rating_avg }}", "eta_min": "{{ item.eta_min }}", "distance_km": "{{ item.dist_km }}",
        "offered_ts": "{{ vars.offered_ts }}", "expires_ts": "{{ vars.expires_ts }}",
    }),
    PUBLISH("offer_pub", "driver:{{ item.user_id }}", "offer.new", {
        "offer_id": "{{ steps.offer_mk.output.id }}", "request_type": "{{ input.request_type }}",
        "request_id": "{{ input.request_id }}", "round": "{{ input.round }}",
        "eta_min": "{{ item.eta_min }}", "expires_ts": "{{ vars.expires_ts }}"}),
    TIMELINE("offer_tl", "{{ input.request_type }}", "{{ input.request_id }}", "matching.offer_sent",
             "Offer sent to {{ item.user_id }} (round {{ input.round }})", {
                 "driver_user_id": "{{ item.user_id }}", "eta_min": "{{ item.eta_min }}",
                 "expires_ts": "{{ vars.expires_ts }}"}, actor="system"),
    TIMELINE("round_tl", "{{ input.request_type }}", "{{ input.request_id }}", "matching.round_started",
             "Round {{ input.round }} ({{ vars.strategy }}): {{ steps.cand.output | length }} offers", {
                 "radius_km": "{{ vars.radius_km }}", "candidates": "{{ steps.cand.output | length }}"}, actor="system"),
    # close the round after offer_seconds
    N("close_later", "queue.flow", flow="matching_close", delay="{{ vars.offer_seconds }}", input={
        "request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
        "round": "{{ input.round }}", "offer_seconds": "{{ vars.offer_seconds }}"}),
    N("fin_ok", "control.stop"),
    # exhausted: widen or die
    TIMELINE("none_tl", "{{ input.request_type }}", "{{ input.request_id }}", "matching.round_exhausted",
             "Round {{ input.round }} found no available drivers", {"round": "{{ input.round }}"}, actor="system"),
    N("close_row0", "resource.update", resource="matching_rounds", id="{{ steps.round_row.output.id }}",
      data={"outcome": "exhausted", "closed_ts": "{{ steps.rnow.output }}"}),
    N("advance0", "queue.flow", flow="matching_advance", delay=2, input={
        "request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
        "round": "{{ input.round }}"}),
    N("fin0", "control.stop"),
]
_EDGES = [
    ("trig", "kind"),
    ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "still_open", "next"), ("req", "gone", "missing"),
    ("still_open", "svc", "true"), ("still_open", "fin_ok", "false"),
    ("svc", "svc_set"), ("svc_set", "mkt"), ("mkt", "mkt_set"), ("mkt_set", "cfg"),
    ("cfg", "r2"), ("r2", "cfg2", "true"), ("r2", "r3", "false"),
    ("cfg2", "r3"), ("r3", "cfg3", "true"), ("r3", "r4", "false"),
    ("cfg3", "r4"), ("r4", "cfg4", "true"), ("r4", "rad_lat", "false"),
    ("cfg4", "rad_lat"),
]
_chain(_NODES, _EDGES, ["rad_lat", "rad_lng", "lat_min", "lat_max", "lng_min", "lng_max", "stale", "cand", "rnow", "round_row"])
_EDGES += [
    ("round_row", "any_cand"),
    ("any_cand", "onow", "true"),
    ("any_cand", "none_tl", "false"),
    ("onow", "exp"), ("exp", "exp_set"), ("exp_set", "offer_loop"),
    ("offer_loop", "offer_mk", "each"), ("offer_mk", "offer_pub"), ("offer_pub", "offer_tl"),
    ("offer_loop", "round_tl", "done"),
    ("round_tl", "close_later"), ("close_later", "fin_ok"),
    ("none_tl", "close_row0"), ("close_row0", "advance0"), ("advance0", "fin0"),
]
FLOWS.append(flow("matching_round", "One matching round: pick candidates by round policy, send expiring offers, "
                                 "schedule the close. Rounds widen until a policy limit.",
                  _NODES, _EDGES, timeout=120))

# ── matching close: expire the round's offers, advance or finish ─────────────

_NODES = [
    N("trig", "trigger.job"),
    N("kind", "control.switch", value="{{ input.request_type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", searching="searching", assigned="driver_assigned"),
    SET("k_del", resource="delivery_requests", searching="searching_driver", assigned="driver_assigned"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.request_id }}"),
    N("fin_gone", "control.stop"),
    # expire this round's stale offers
    NOW("now", format="unix"),
    N("pend", "resource.list", resource="offers",
      filters={"request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
               "state": "pending"}, limit=50),
    N("exp_loop", "control.foreach", items="{{ steps.pend.output.data }}"),
    IF("is_stale", {"lt": ["$item.expires_ts", "$steps.now.output"]}),
    N("exp_upd", "resource.update", resource="offers", id="{{ item.id }}",
      data={"state": "expired", "responded_ts": "{{ steps.now.output }}"}),
    PUBLISH("exp_pub", "driver:{{ item.driver_user_id }}", "offer.expired",
            {"offer_id": "{{ item.id }}", "request_type": "{{ input.request_type }}",
             "request_id": "{{ input.request_id }}"}),
    TIMELINE("exp_tl", "{{ input.request_type }}", "{{ input.request_id }}", "matching.offer_expired",
             "Offer to {{ item.driver_user_id }} expired", actor="system"),
    # if someone accepted meanwhile, the request moved on — stop
    IF("assigned", {"eq": ["$steps.req.output.status", "$vars.assigned"]}),
    N("close_acc", "resource.update", resource="matching_rounds",
      id="{{ steps.round_find.output.data.0.id | default: 0 }}", data={"outcome": "accepted"}),
    N("fin_acc", "control.stop"),
    # still searching → next round
    IF("still_open", {"eq": ["$steps.req.output.status", "$vars.searching"]}),
    N("round_find", "resource.list", resource="matching_rounds",
      filters={"request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
               "outcome": "open"}, limit=1, sort="-id"),
    N("close_open", "resource.update", resource="matching_rounds",
      id="{{ steps.round_find.output.data.0.id | default: 0 }}", data={"outcome": "exhausted", "closed_ts": "{{ steps.now.output }}"}),
    N("advance", "queue.flow", flow="matching_advance", input={
        "request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
        "round": "{{ input.round }}"}),
    N("fin", "control.stop"),
]
_EDGES = [
    ("trig", "kind"), ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "now", "next"), ("req", "fin_gone", "missing"),
    ("now", "pend"), ("pend", "exp_loop"),
    ("exp_loop", "is_stale", "each"),
    ("is_stale", "exp_upd", "true"), ("exp_upd", "exp_pub"), ("exp_pub", "exp_tl"),
    ("exp_loop", "assigned", "done"),
    ("assigned", "round_find", "true"),
    ("round_find", "close_acc"), ("close_acc", "fin_acc"),
    ("assigned", "still_open", "false"),
    ("still_open", "round_find", "true"),
    ("still_open", "fin", "false"),
]
FLOWS.append(flow("matching_close", "Close a round: expire stale offers, then finish (accepted) or advance.",
                  _NODES, _EDGES, timeout=90))

# ── matching advance: next round or unserviceable ────────────────────────────

_NODES = [
    N("trig", "trigger.job"),
    N("kind", "control.switch", value="{{ input.request_type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", searching="searching", dead="unserviceable",
        dead_kind="ride.unserviceable", dead_msg="No drivers are available right now",
        ntf_kind="ride.unserviceable"),
    SET("k_del", resource="delivery_requests", searching="searching_driver", dead="delivery_failed",
        dead_kind="delivery.failed", dead_msg="We couldn't find a courier for this delivery",
        ntf_kind="delivery.failed"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.request_id }}"),
    N("fin_gone", "control.stop"),
    IF("still_open", {"eq": ["$steps.req.output.status", "$vars.searching"]}),
    N("svc", "resource.list", resource="services", filters={"slug": "{{ steps.req.output.service }}"}, limit=1),
    IF("more", {"lt": ["{{ input.round }}", 4]}),
    CALC("next_round", "add", ["{{ input.round }}", 1]),
    N("go_next", "queue.flow", flow="matching_round", input={
        "request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
        "round": "{{ steps.next_round.output }}"}),
    N("fin_next", "control.stop"),
    # exhausted every round → declare unserviceable
    N("die", "resource.update", resource="{{ vars.resource }}", id="{{ input.request_id }}",
      data={"status": "{{ vars.dead }}",
            "cancellation": {"by": "platform", "reason": "no_drivers_available", "ts": "{{ steps.req.output.assigned_ts }}"}}),
    TIMELINE("die_tl", "{{ input.request_type }}", "{{ input.request_id }}", "matching.unserviceable",
             "{{ vars.dead_msg }} after 4 rounds", actor="system"),
    N("die_ntf", "resource.create", resource="notifications", data={
        "user_id": "{{ steps.req.output.user_id }}", "kind": "{{ vars.ntf_kind }}",
        "title": "{{ vars.dead_msg }}", "body": "You won't be charged. Please try again shortly."}),
    EMIT("die_ev", "{{ vars.dead_kind }}", {"request_id": "{{ input.request_id }}"}),
    PUBLISH("die_pub", "{{ input.request_type }}:{{ input.request_id }}", "{{ vars.dead_kind }}",
            {"request_id": "{{ input.request_id }}"}),
    N("die_pay", "queue.flow", flow="payment_void", input={
        "subject_type": "{{ input.request_type }}", "subject_id": "{{ input.request_id }}",
        "reason": "unserviceable"}),
    N("fin", "control.stop"),
]
_EDGES = [
    ("trig", "kind"), ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "still_open", "next"), ("req", "fin_gone", "missing"),
    ("still_open", "svc", "true"), ("still_open", "fin", "false"),
    ("svc", "more"),
    ("more", "next_round", "true"), ("next_round", "go_next"), ("go_next", "fin_next"),
    ("more", "die", "false"),
    ("die", "die_tl"), ("die_tl", "die_ntf"), ("die_ntf", "die_ev"), ("die_ev", "die_pub"),
    ("die_pub", "die_pay"), ("die_pay", "fin"),
]
FLOWS.append(flow("matching_advance", "Advance to the next round, or declare the request unserviceable.",
                  _NODES, _EDGES, timeout=60))

# ── driver accept (the race) ─────────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/offers/{id}/accept"),
    N("auth", "auth.require", role="driver"),
    N("offer", "resource.get", resource="offers", id="{{ input.params.id }}"),
    ERR("no_offer", 404, "offer_not_found", "No such offer"),
    IF("mine", {"eq": ["$steps.offer.output.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This offer belongs to another driver"),
    IF("pending", {"eq": ["$steps.offer.output.state", "pending"]}),
    ERR("closed", 409, "offer_closed", "This offer is no longer available"),
    NOW("now", format="unix"),
    IF("fresh", {"gt": ["$steps.offer.output.expires_ts", "$steps.now.output"]}),
    N("mark_exp", "resource.update", resource="offers", id="{{ input.params.id }}", data={"state": "expired"}),
    ERR("expired", 410, "offer_expired", "This offer has expired"),
    # load the request, in the right resource
    N("kind", "control.switch", value="{{ steps.offer.output.request_type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", searching="searching", assigned="driver_assigned",
        active_state="to_pickup", event_prefix="ride"),
    SET("k_del", resource="delivery_requests", searching="searching_driver", assigned="driver_assigned",
        active_state="to_pickup", event_prefix="delivery"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ steps.offer.output.request_id }}"),
    ERR("req_gone", 404, "request_gone", "The request no longer exists"),
    IF("open", {"eq": ["$steps.req.output.status", "$vars.searching"]}),
    ERR("too_late", 409, "offer_closed", "This opportunity has closed"),
    # ── the claim: exactly one winner, ever ──
    NOW("cts", format="unix"),
    N("claim", "resource.create", resource="assignment_claims", data={
        "request_type": "{{ steps.offer.output.request_type }}",
        "request_id": "{{ steps.offer.output.request_id }}",
        "winner": "driver", "winner_id": "{{ auth.user_id }}", "created_ts": "{{ steps.cts.output }}",
    }),
    # claim failed → another winner exists
    N("lost_sup", "resource.update", resource="offers", id="{{ input.params.id }}",
      data={"state": "superseded", "responded_ts": "{{ steps.cts.output }}"}),
    PUBLISH("lost_pub", "driver:{{ auth.user_id }}", "offer.closed",
            {"offer_id": "{{ input.params.id }}", "reason": "assigned_to_another"}),
    ERR("lost", 409, "offer_closed", "Another driver took this one"),
    # won → bind everything atomically
    NOW("ats", format="unix"),
    N("bind", "db.transaction", operations=[
        {"op": "update", "resource": "offers", "id": "{{ input.params.id }}",
         "data": {"state": "accepted", "responded_ts": "{{ steps.ats.output }}"}},
        {"op": "update", "resource": "{{ vars.resource }}", "id": "{{ steps.offer.output.request_id }}",
         "data": {"driver_id": "{{ steps.offer.output.driver_id }}", "driver_user_id": "{{ auth.user_id }}",
                  "status": "{{ vars.assigned }}", "assigned_ts": "{{ steps.ats.output }}",
                  "matching_round": "{{ steps.offer.output.round }}"}},
        {"op": "create", "resource": "assignments", "data": {
            "request_type": "{{ steps.offer.output.request_type }}",
            "request_id": "{{ steps.offer.output.request_id }}",
            "driver_user_id": "{{ auth.user_id }}", "driver_id": "{{ steps.offer.output.driver_id }}",
            "round": "{{ steps.offer.output.round }}", "assigned_ts": "{{ steps.ats.output }}"}},
        {"op": "update", "resource": "driver_profiles", "id": "{{ steps.offer.output.driver_id }}",
         "data": {"state": "{{ vars.active_state }}"}},
    ]),
    # supersede the other pending offers and tell those drivers at once
    N("sibs", "resource.list", resource="offers",
      filters={"request_type": "{{ steps.offer.output.request_type }}",
               "request_id": "{{ steps.offer.output.request_id }}", "state": "pending"}, limit=50),
    N("sib_loop", "control.foreach", items="{{ steps.sibs.output.data }}"),
    N("sib_upd", "resource.update", resource="offers", id="{{ item.id }}",
      data={"state": "superseded", "responded_ts": "{{ steps.ats.output }}"}),
    PUBLISH("sib_pub", "driver:{{ item.driver_user_id }}", "offer.closed",
            {"offer_id": "{{ item.id }}", "reason": "assigned_to_another"}),
    TIMELINE("tl", "{{ steps.offer.output.request_type }}", "{{ steps.offer.output.request_id }}",
             "matching.assigned", "Driver {{ auth.user_id }} accepted (round {{ steps.offer.output.round }})",
             {"driver_user_id": "{{ auth.user_id }}", "offer_id": "{{ input.params.id }}",
              "round": "{{ steps.offer.output.round }}"}, actor="{{ auth.user_id }}"),
    N("me", "resource.list", resource="driver_profiles", filters={"user_id": "{{ auth.user_id }}"}, limit=1),
    PUBLISH("cust_pub", "{{ steps.offer.output.request_type }}:{{ steps.offer.output.request_id }}",
            "{{ vars.event_prefix }}.driver_assigned",
            {"driver": "{{ steps.me.output.data.0 }}", "eta_min": "{{ steps.offer.output.eta_min }}"}),
    NOTIFY_USER("cust_ntf", "{{ steps.req.output.user_id }}", "{{ vars.event_prefix }}.driver_assigned",
                "Driver found", "Your driver is on the way — ETA {{ steps.offer.output.eta_min }} min",
                link="/{{ steps.offer.output.request_type }}s/{{ steps.offer.output.request_id }}"),
    EMIT("ev", "{{ vars.event_prefix }}.driver_assigned", {
        "request_id": "{{ steps.offer.output.request_id }}", "driver_user_id": "{{ auth.user_id }}",
        "round": "{{ steps.offer.output.round }}", "org_id": "{{ steps.req.output.org_id }}"}),
    REPLY("reply", {"assigned": True, "request": "{{ steps.bind.output.1 }}",
                    "assignment": "{{ steps.bind.output.2 }}"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "offer"),
    ("offer", "mine", "next"), ("offer", "no_offer", "missing"),
    ("mine", "pending", "true"), ("mine", "not_mine", "false"),
    ("pending", "now", "true"), ("pending", "closed", "false"),
    ("now", "fresh"),
    ("fresh", "kind", "true"), ("fresh", "mark_exp", "false"), ("mark_exp", "expired"),
    ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "open", "next"), ("req", "req_gone", "missing"),
    ("open", "cts", "true"), ("open", "too_late", "false"),
    ("cts", "claim"),
    ("claim", "ats", "next"),
    ("claim", "lost_sup", "error"), ("lost_sup", "lost_pub"), ("lost_pub", "lost"),
    ("ats", "bind"), ("bind", "sibs"), ("sibs", "sib_loop"),
    ("sib_loop", "sib_upd", "each"), ("sib_upd", "sib_pub"),
    ("sib_loop", "tl", "done"),
    ("tl", "me"), ("me", "cust_pub"), ("cust_pub", "cust_ntf"), ("cust_ntf", "ev"), ("ev", "reply"),
]
FLOWS.append(flow("driver_accept", "Accept an offer. The unique claim row decides simultaneous accepts: "
                                "one driver binds the assignment, the rest are told the offer closed.",
                  _NODES, _EDGES, timeout=120))

# ── driver reject ────────────────────────────────────────────────────────────

_NODES = [
    N("trig", "trigger.http", method="POST", path="/offers/{id}/reject"),
    N("auth", "auth.require", role="driver"),
    N("offer", "resource.get", resource="offers", id="{{ input.params.id }}"),
    ERR("no_offer", 404, "offer_not_found", "No such offer"),
    IF("mine", {"eq": ["$steps.offer.output.driver_user_id", "$auth.user_id"]}),
    ERR("not_mine", 403, "forbidden", "This offer belongs to another driver"),
    IF("pending", {"eq": ["$steps.offer.output.state", "pending"]}),
    NOW("now", format="unix"),
    N("rej", "resource.update", resource="offers", id="{{ input.params.id }}",
      data={"state": "rejected", "responded_ts": "{{ steps.now.output }}"}),
    N("prof", "resource.update", resource="driver_profiles", id="{{ steps.offer.output.driver_id }}",
      data={"state": "available"}),
    TIMELINE("tl", "{{ steps.offer.output.request_type }}", "{{ steps.offer.output.request_id }}",
             "matching.offer_rejected", "Driver {{ auth.user_id }} declined",
             {"reason": "{{ input.body.reason }}"}, actor="{{ auth.user_id }}"),
    REPLY("reply", {"rejected": True}),
    ERR("closed", 409, "offer_closed", "This offer is no longer available"),
    REPLY("reply2", {"rejected": False, "reason": "already_closed"}),
]
_EDGES = [
    ("trig", "auth"), ("auth", "offer"),
    ("offer", "mine", "next"), ("offer", "no_offer", "missing"),
    ("mine", "pending", "true"), ("mine", "not_mine", "false"),
    ("pending", "now", "true"), ("pending", "closed", "false"),
    ("now", "rej"), ("rej", "prof"), ("prof", "tl"), ("tl", "reply"),
]
FLOWS.append(flow("driver_reject", "Decline an offer; the driver is excluded from later rounds of this request.",
                  _NODES, _EDGES, timeout=60))

# ── reassignment: driver cancel, ops remove, no-progress watchdog ────────────

_NODES = [
    N("trig", "trigger.job"),
    N("kind", "control.switch", value="{{ input.request_type }}", cases=["ride", "delivery"]),
    SET("k_ride", resource="ride_requests", searching="searching",
        open_states=["driver_assigned", "driver_en_route", "driver_arrived"], event_prefix="ride"),
    SET("k_del", resource="delivery_requests", searching="searching_driver",
        open_states=["driver_assigned", "driver_to_pickup", "driver_at_pickup"], event_prefix="delivery"),
    N("req", "resource.get", resource="{{ vars.resource }}", id="{{ input.request_id }}"),
    N("fin_gone", "control.stop"),
    IF("open", {"in": ["$steps.req.output.status", "$vars.open_states"]}),
    # release the active assignment
    N("asn", "resource.list", resource="assignments",
      filters={"request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
               "state": "active"}, limit=1),
    IF("has_asn", {"truthy": "$steps.asn.output.total"}),
    NOW("rts", format="unix"),
    N("asn_rel", "resource.update", resource="assignments", id="{{ steps.asn.output.data.0.id }}",
      data={"state": "released", "released_ts": "{{ steps.rts.output }}",
            "release_reason": "{{ input.reason }}"}),
    # free the driver
    N("drv", "resource.list", resource="driver_profiles",
      filters={"user_id": "{{ steps.asn.output.data.0.driver_user_id }}"}, limit=1),
    IF("has_drv", {"truthy": "$steps.drv.output.total"}),
    N("drv_free", "resource.update", resource="driver_profiles", id="{{ steps.drv.output.data.0.id }}",
      data={"state": "available"}),
    NOTIFY_USER("drv_ntf", "{{ steps.asn.output.data.0.driver_user_id }}", "job.reassigned",
                "Job reassigned", "This job was returned to dispatch: {{ input.reason }}"),
    # reopen the request for a fresh round of matching
    N("claim_find", "resource.list", resource="assignment_claims",
      filters={"request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}"}, limit=1),
    IF("has_claim", {"truthy": "$steps.claim_find.output.total"}),
    N("claim_del", "resource.delete", resource="assignment_claims", id="{{ steps.claim_find.output.data.0.id }}"),
    N("reopen", "resource.update", resource="{{ vars.resource }}", id="{{ input.request_id }}",
      data={"status": "{{ vars.searching }}", "driver_id": None, "driver_user_id": None, "assigned_ts": None}),
    TIMELINE("tl", "{{ input.request_type }}", "{{ input.request_id }}", "matching.reassigned",
             "Assignment released ({{ input.reason }}); returned to matching", actor="{{ input.actor | default: 'system' }}"),
    NOTIFY_USER("cust_ntf", "{{ steps.req.output.user_id }}", "{{ vars.event_prefix }}.reassigned",
                "Finding you another driver", "Your previous driver couldn't continue — we're on it."),
    EMIT("ev", "{{ vars.event_prefix }}.rematching", {"request_id": "{{ input.request_id }}",
                                                      "reason": "{{ input.reason }}"}),
    PUBLISH("cust_pub", "{{ input.request_type }}:{{ input.request_id }}", "{{ vars.event_prefix }}.rematching",
            {"reason": "{{ input.reason }}"}),
    N("rematch", "queue.flow", flow="matching_round", input={
        "request_type": "{{ input.request_type }}", "request_id": "{{ input.request_id }}",
        "round": "{{ steps.req.output.matching_round | default: 1 }}"}),
    N("fin", "control.stop"),
]
_EDGES = [
    ("trig", "kind"), ("kind", "k_ride", "ride"), ("kind", "k_del", "delivery"),
    ("k_ride", "req"), ("k_del", "req"),
    ("req", "open", "next"), ("req", "fin_gone", "missing"),
    ("open", "asn", "true"), ("open", "fin", "false"),
    ("asn", "has_asn"),
    ("has_asn", "rts", "true"), ("rts", "asn_rel"), ("asn_rel", "drv"), ("drv", "has_drv"),
    ("has_drv", "drv_free", "true"), ("drv_free", "drv_ntf"),
    ("has_drv", "drv_ntf", "false"),
    ("drv_ntf", "claim_find"),
    ("has_asn", "claim_find", "false"),
    ("claim_find", "has_claim"),
    ("has_claim", "claim_del", "true"), ("claim_del", "reopen"),
    ("has_claim", "reopen", "false"),
    ("reopen", "tl"), ("tl", "cust_ntf"), ("cust_ntf", "ev"), ("ev", "cust_pub"), ("cust_pub", "rematch"),
    ("rematch", "fin"),
]
FLOWS.append(flow("request_rematch", "Release the current driver and return the request to matching, "
                                  "keeping full assignment history.",
                  _NODES, _EDGES, timeout=90))
