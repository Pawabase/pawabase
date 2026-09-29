"""Shared flow fragments for SwiftLine: SQL, pricing, disruption guards.

Flows are assembled from these fragments so the same pricing math, the same
matching SQL and the same guard rails apply everywhere. All SQL is written for
both bundled dialects (SQLite and Postgres): ``?`` placeholders, ``TRUE`` /
``FALSE`` literals, ``ABS()`` and ``CAST`` only.

Fragments expect and produce well-known entries in ``vars``:

* ``vars.rule``   — the pricing_rules row (flat fields, so templates can read it)
* ``vars.market_row``, ``vars.service_row`` — the resolved config rows
* ``vars.surge``  — multiplier computed by ``surge_fragment``
* ``vars.quote``  — the priced breakdown produced by ``pricing_fragment``
"""

from helpers import CALC, IF, N, SET

# ── SQL ──────────────────────────────────────────────────────────────────────

#: The highest-priority zone containing a point. Params: market, lat, lat, lng, lng.
ZONE_SQL = """SELECT slug, kind, priority FROM zones
WHERE market = ? AND active = TRUE
  AND min_lat <= ? AND max_lat >= ? AND min_lng <= ? AND max_lng >= ?
ORDER BY priority DESC LIMIT 1"""

#: Zone fees for a service at a zone. Params: market, service, zone.
ZONE_FEE_SQL = """SELECT pickup_fee_minor, dropoff_fee_minor FROM zone_fees
WHERE market = ? AND service = ? AND zone_slug = ? AND active = TRUE LIMIT 1"""

#: Eligible drivers around a pickup point, nearest first. Params:
#: lat, lng_scale, lng, lat, lng_scale, lng, market, stale_before_ts,
#: class_min, class_max, min_rating, lat_min, lat_max, lng_min, lng_max,
#: request_type, request_id, limit.
CANDIDATES_SQL = """SELECT dp.id, dp.user_id, dp.vehicle_class, dp.last_lat, dp.last_lng,
       dp.rating_avg, dp.vehicle_weight_kg, dp.fleet_id,
       CAST((ABS(dp.last_lat - ?) * 111.32 + ABS(dp.last_lng - ?) * ?) / 28.0 * 60.0 AS INTEGER) AS eta_min,
       (ABS(dp.last_lat - ?) * 111.32 + ABS(dp.last_lng - ?) * ?) AS dist_km
FROM driver_profiles dp
WHERE dp.market = ?
  AND dp.state = 'available'
  AND dp.location_stale = FALSE
  AND dp.last_location_ts > ?
  AND dp.onboarding_status = 'activated'
  AND dp.vehicle_class_rank BETWEEN ? AND ?
  AND dp.rating_avg >= ?
  AND dp.last_lat BETWEEN ? AND ? AND dp.last_lng BETWEEN ? AND ?
  AND dp.user_id NOT IN (
      SELECT driver_user_id FROM offers
      WHERE request_type = ? AND request_id = ? AND state IN ('rejected', 'expired', 'superseded', 'accepted'))
ORDER BY dist_km ASC
LIMIT ?"""

#: Demand/supply for surge: open demand vs available drivers in a market.
#: Params: market, market, market.
SURGE_SQL = """SELECT
  (SELECT COUNT(*) FROM ride_requests WHERE market = ? AND status = 'searching')
 + (SELECT COUNT(*) FROM delivery_requests WHERE market = ? AND status IN ('searching_driver', 'awaiting_dispatch'))
  AS demand,
  (SELECT COUNT(*) FROM driver_profiles WHERE market = ? AND state = 'available' AND location_stale = FALSE) AS supply"""

# ── fragments ────────────────────────────────────────────────────────────────


def disruption_guard(prefix, service_slug_tpl, zone_var=None, zone_scope="zone_pickup"):
    """Refuse when ops has disabled the service or assignments globally.

    Returns (nodes, edges, pass_node): the caller wires *pass_node* onward;
    the blocked path raises 409 ``temporarily_unavailable``.
    """
    p = prefix
    nodes = [
        N(f"{p}_svc_flag", "resource.list", resource="ops_flags",
          filters={"scope": "service", "scope_key": service_slug_tpl, "disabled": True}, limit=1),
        IF(f"{p}_svc_off", {"truthy": f"$steps.{p}_svc_flag.output.total"}),
        N(f"{p}_asn_flag", "resource.list", resource="ops_flags",
          filters={"scope": "assignments", "scope_key": "all", "disabled": True}, limit=1),
        IF(f"{p}_asn_off", {"truthy": f"$steps.{p}_asn_flag.output.total"}),
    ]
    edges = [
        (f"{p}_svc_flag", f"{p}_svc_off"),
        (f"{p}_svc_off", f"{p}_blocked", "true"),
        (f"{p}_svc_off", f"{p}_asn_flag", "false"),
        (f"{p}_asn_flag", f"{p}_asn_off"),
        (f"{p}_asn_off", f"{p}_blocked", "true"),
    ]
    last = f"{p}_asn_off"
    if zone_var:
        nodes += [
            N(f"{p}_zone_flag", "resource.list", resource="ops_flags",
              filters={"scope": zone_scope, "scope_key": zone_var, "disabled": True}, limit=1),
            IF(f"{p}_zone_off", {"truthy": f"$steps.{p}_zone_flag.output.total"}),
        ]
        edges += [
            (last, f"{p}_zone_flag", "false"),
            (f"{p}_zone_flag", f"{p}_zone_off"),
            (f"{p}_zone_off", f"{p}_blocked", "true"),
        ]
        last = f"{p}_zone_off"
    nodes.append(N(f"{p}_blocked", "error.raise", status=409, code="temporarily_unavailable",
                   message="This service or area is temporarily unavailable"))
    nodes.append(N(f"{p}_pass", "control.set", values={"disruption_ok": True}))
    edges.append((last, f"{p}_pass", "false"))
    return nodes, edges, f"{p}_pass"


def surge_fragment(prefix, market_tpl):
    """Set ``vars.surge`` from live demand/supply against the rule's tiers.

    Expects ``vars.rule`` in state. Tiers evaluate high to low; default 1.0.
    """
    p = prefix
    nodes = [
        SET(f"{p}_default", surge=1.0),
        N(f"{p}_ds", "db.query", sql=SURGE_SQL, params=[market_tpl, market_tpl, market_tpl]),
        CALC(f"{p}_ratio", "divide",
             [f"{{{{ steps.{p}_ds.output.0.demand | default: 0 }}}}",
              f"{{{{ steps.{p}_ds.output.0.supply | default: 1 }}}}"], digits=2),
        SET(f"{p}_save", ratio=f"{{{{ steps.{p}_ratio.output }}}}"),
        IF(f"{p}_t3", {"all": [{"gt": ["$vars.rule.surge_t3_ratio", 0]},
                                {"gte": ["$vars.ratio", "$vars.rule.surge_t3_ratio"]}]}),
        SET(f"{p}_s3", surge="{{ vars.rule.surge_t3_multiplier }}"),
        IF(f"{p}_t2", {"all": [{"gt": ["$vars.rule.surge_t2_ratio", 0]},
                                {"gte": ["$vars.ratio", "$vars.rule.surge_t2_ratio"]}]}),
        SET(f"{p}_s2", surge="{{ vars.rule.surge_t2_multiplier }}"),
        IF(f"{p}_t1", {"all": [{"gt": ["$vars.rule.surge_t1_ratio", 0]},
                                {"gte": ["$vars.ratio", "$vars.rule.surge_t1_ratio"]}]}),
        SET(f"{p}_s1", surge="{{ vars.rule.surge_t1_multiplier }}"),
        N(f"{p}_done", "control.set", values={"surge_final": "{{ vars.surge }}"}),
    ]
    edges = [
        (f"{p}_default", f"{p}_ds"), (f"{p}_ds", f"{p}_ratio"), (f"{p}_ratio", f"{p}_save"),
        (f"{p}_save", f"{p}_t3"),
        (f"{p}_t3", f"{p}_s3", "true"), (f"{p}_s3", f"{p}_done"),
        (f"{p}_t3", f"{p}_t2", "false"),
        (f"{p}_t2", f"{p}_s2", "true"), (f"{p}_s2", f"{p}_done"),
        (f"{p}_t2", f"{p}_t1", "false"),
        (f"{p}_t1", f"{p}_s1", "true"), (f"{p}_s1", f"{p}_done"),
        (f"{p}_t1", f"{p}_done", "false"),
    ]
    return nodes, edges, f"{p}_done"


def pricing_core_fragment(prefix):
    """Charges through surge. Sets ``vars.subtotal_surged`` and ``vars.quote_lines``.

    Reads: ``vars.rule``, ``vars.distance_km``, ``vars.duration_min``,
    ``vars.stops_count``, ``vars.weight_kg``, ``vars.priority``, ``vars.scheduled``,
    ``vars.zone_fee_minor``, ``vars.airport_fee_minor``, ``vars.surge``.
    """
    p = prefix
    r = "$vars.rule"
    nodes = [
        IF(f"{p}_has_sched", {"truthy": "$vars.scheduled"}),
        SET(f"{p}_sched_fee", schedule_fee=f"{{{{ {r}.schedule_fee_minor }}}}"),
        SET(f"{p}_sched_zero", schedule_fee=0),
        IF(f"{p}_has_prio", {"truthy": "$vars.priority"}),
        SET(f"{p}_prio_fee", priority_fee=f"{{{{ {r}.priority_fee_minor }}}}"),
        SET(f"{p}_prio_zero", priority_fee=0),
        CALC(f"{p}_dist_chg", "multiply", ["{{ vars.distance_km }}", f"{{{{ {r}.per_km_minor }}}}"]),
        CALC(f"{p}_time_chg", "multiply", ["{{ vars.duration_min }}", f"{{{{ {r}.per_minute_minor }}}}"]),
        CALC(f"{p}_stop_chg", "multiply", ["{{ vars.stops_count }}", f"{{{{ {r}.stop_fee_minor }}}}"]),
        CALC(f"{p}_wgt_chg", "multiply", ["{{ vars.weight_kg }}", f"{{{{ {r}.weight_per_kg_minor }}}}"]),
        CALC(f"{p}_subtotal", "sum", [
            f"{{{{ {r}.base_minor }}}}",
            f"{{{{ steps.{p}_dist_chg.output }}}}",
            f"{{{{ steps.{p}_time_chg.output }}}}",
            f"{{{{ steps.{p}_stop_chg.output }}}}",
            f"{{{{ steps.{p}_wgt_chg.output }}}}",
            "{{ vars.zone_fee_minor }}",
            "{{ vars.airport_fee_minor }}",
            "{{ vars.schedule_fee }}",
            "{{ vars.priority_fee }}",
        ]),
        CALC(f"{p}_floor", "max", [f"{{{{ steps.{p}_subtotal.output }}}}", f"{{{{ {r}.minimum_minor }}}}"]),
        CALC(f"{p}_mult", "multiply", [f"{{{{ steps.{p}_floor.output }}}}", f"{{{{ {r}.multiplier }}}}"], digits=0),
        CALC(f"{p}_surged", "multiply", [f"{{{{ steps.{p}_mult.output }}}}", "{{ vars.surge }}"], digits=0),
        SET(f"{p}_save", subtotal_surged=f"{{{{ steps.{p}_surged.output }}}}", **{"quote_lines": {
            "base_minor": f"{{{{ {r}.base_minor }}}}",
            "distance_minor": f"{{{{ steps.{p}_dist_chg.output }}}}",
            "duration_minor": f"{{{{ steps.{p}_time_chg.output }}}}",
            "stops_minor": f"{{{{ steps.{p}_stop_chg.output }}}}",
            "weight_minor": f"{{{{ steps.{p}_wgt_chg.output }}}}",
            "zone_fee_minor": "{{ vars.zone_fee_minor }}",
            "airport_fee_minor": "{{ vars.airport_fee_minor }}",
            "schedule_fee_minor": "{{ vars.schedule_fee }}",
            "priority_fee_minor": "{{ vars.priority_fee }}",
            "surge": "{{ vars.surge }}",
        }}),
    ]
    edges = [
        (f"{p}_has_sched", f"{p}_sched_fee", "true"),
        (f"{p}_has_sched", f"{p}_sched_zero", "false"),
        (f"{p}_sched_fee", f"{p}_has_prio"),
        (f"{p}_sched_zero", f"{p}_has_prio"),
        (f"{p}_has_prio", f"{p}_prio_fee", "true"),
        (f"{p}_has_prio", f"{p}_prio_zero", "false"),
        (f"{p}_prio_fee", f"{p}_dist_chg"),
        (f"{p}_prio_zero", f"{p}_dist_chg"),
    ]
    seq = [n[0] for n in nodes]
    for a, b in zip(seq[6:], seq[7:]):
        edges.append((a, b))
    return nodes, edges, seq[-1]


def promo_fragment(prefix, promo_code_tpl, *, free_delivery_ok):
    """Resolve a promo code into ``vars.discount_minor`` (0 when absent/invalid).

    Reads ``vars.subtotal_surged``, ``vars.service``, ``vars.market``, ``auth.user_id``.
    """
    p = prefix
    nodes = [
        SET(f"{p}_zero", discount_minor=0, promo=None),
        IF(f"{p}_has_code", {"truthy": promo_code_tpl}),
        N(f"{p}_now", "time.now", format="iso"),
        N(f"{p}_load", "resource.list", resource="promotions",
          filters={"code": promo_code_tpl, "active": True}, limit=1),
        IF(f"{p}_found", {"truthy": f"$steps.{p}_load.output.total"}),
        # date window (ISO strings compare lexicographically)
        IF(f"{p}_dates", {"all": [
            {"any": [{"empty": f"$steps.{p}_load.output.data.0.starts_at"},
                     {"lte": [f"$steps.{p}_load.output.data.0.starts_at", f"$steps.{p}_now.output"]}]},
            {"any": [{"empty": f"$steps.{p}_load.output.data.0.ends_at"},
                     {"gte": [f"$steps.{p}_load.output.data.0.ends_at", f"$steps.{p}_now.output"]}]},
        ]}),
        # global usage limit
        IF(f"{p}_cap", {"any": [
            {"empty": f"$steps.{p}_load.output.data.0.usage_limit"},
            {"lt": [f"$steps.{p}_load.output.data.0.usage_count",
                    f"$steps.{p}_load.output.data.0.usage_limit"]}]}),
        # per-user limit
        N(f"{p}_mine", "resource.list", resource="promo_redemptions",
          filters={"promo_code": promo_code_tpl, "user_id": "{{ auth.user_id }}"}, limit=50),
        IF(f"{p}_ucap", {"any": [
            {"empty": f"$steps.{p}_load.output.data.0.per_user_limit"},
            {"lt": [f"$steps.{p}_mine.output.total",
                    f"$steps.{p}_load.output.data.0.per_user_limit"]}]}),
        # service applicability (empty list: all services)
        IF(f"{p}_svc", {"any": [
            {"empty": f"$steps.{p}_load.output.data.0.services"},
            {"contains": [f"$steps.{p}_load.output.data.0.services", "$vars.service"]}]}),
        # market applicability
        IF(f"{p}_mkt", {"any": [
            {"empty": f"$steps.{p}_load.output.data.0.markets"},
            {"contains": [f"$steps.{p}_load.output.data.0.markets", "$vars.market"]}]}),
        # compute discount by kind
        N(f"{p}_kind", "control.switch", value=f"{{{{ steps.{p}_load.output.data.0.kind }}}}",
          cases=["percentage", "fixed", "free_delivery"]),
        CALC(f"{p}_pct_raw", "multiply", ["{{ vars.subtotal_surged }}",
                                          f"{{{{ steps.{p}_load.output.data.0.value }}}}"], digits=0),
        CALC(f"{p}_pct", "divide", [f"{{{{ steps.{p}_pct_raw.output }}}}", 10000], digits=0),
        IF(f"{p}_pct_cap", {"all": [{"gt": [f"$steps.{p}_load.output.data.0.max_discount_minor", 0]},
                                     {"gt": [f"$steps.{p}_pct.output",
                                              f"$steps.{p}_load.output.data.0.max_discount_minor"]}]}),
        SET(f"{p}_pct_capped", discount_minor=f"{{{{ steps.{p}_load.output.data.0.max_discount_minor }}}}"),
        SET(f"{p}_pct_plain", discount_minor=f"{{{{ steps.{p}_pct.output }}}}"),
        CALC(f"{p}_fixed", "min", [f"{{{{ steps.{p}_load.output.data.0.value }}}}",
                                   "{{ vars.subtotal_surged }}"]),
        SET(f"{p}_fixed_save", discount_minor=f"{{{{ steps.{p}_fixed.output }}}}"),
        SET(f"{p}_free", discount_minor="{{ vars.subtotal_surged }}" if free_delivery_ok else 0),
        SET(f"{p}_done", promo=f"{{{{ steps.{p}_load.output.data.0.code }}}}"),
        N(f"{p}_end", "control.set", values={"promo_resolved": True}),
    ]
    edges = [
        (f"{p}_zero", f"{p}_has_code"),
        (f"{p}_has_code", f"{p}_now", "true"),
        (f"{p}_has_code", f"{p}_end", "false"),
        (f"{p}_now", f"{p}_load"), (f"{p}_load", f"{p}_found"),
        (f"{p}_found", f"{p}_dates", "true"), (f"{p}_found", f"{p}_end", "false"),
        (f"{p}_dates", f"{p}_cap", "true"), (f"{p}_dates", f"{p}_end", "false"),
        (f"{p}_cap", f"{p}_mine", "true"), (f"{p}_cap", f"{p}_end", "false"),
        (f"{p}_mine", f"{p}_ucap"),
        (f"{p}_ucap", f"{p}_svc", "true"), (f"{p}_ucap", f"{p}_end", "false"),
        (f"{p}_svc", f"{p}_mkt", "true"), (f"{p}_svc", f"{p}_end", "false"),
        (f"{p}_mkt", f"{p}_kind", "true"), (f"{p}_mkt", f"{p}_end", "false"),
        (f"{p}_kind", f"{p}_pct_raw", "percentage"),
        (f"{p}_kind", f"{p}_fixed", "fixed"),
        (f"{p}_kind", f"{p}_free", "free_delivery"),
        (f"{p}_kind", f"{p}_end", "default"),
        (f"{p}_pct_raw", f"{p}_pct"), (f"{p}_pct", f"{p}_pct_cap"),
        (f"{p}_pct_cap", f"{p}_pct_capped", "true"), (f"{p}_pct_capped", f"{p}_done"),
        (f"{p}_pct_cap", f"{p}_pct_plain", "false"), (f"{p}_pct_plain", f"{p}_done"),
        (f"{p}_fixed", f"{p}_fixed_save"), (f"{p}_fixed_save", f"{p}_done"),
        (f"{p}_free", f"{p}_done"),
        (f"{p}_done", f"{p}_end"),
    ]
    return nodes, edges, f"{p}_end"


def pricing_total_fragment(prefix):
    """Discount → tax → total. Reads ``vars.subtotal_surged``, ``vars.discount_minor``,
    ``vars.rule``, ``vars.quote_lines``; sets ``vars.quote`` and ``vars.total_minor``."""
    p = prefix
    r = "$vars.rule"
    nodes = [
        CALC(f"{p}_disc", "subtract", ["{{ vars.subtotal_surged }}", "{{ vars.discount_minor }}"]),
        CALC(f"{p}_disc_floor", "max", [f"{{{{ steps.{p}_disc.output }}}}", 0]),
        CALC(f"{p}_tax_raw", "multiply", [f"{{{{ steps.{p}_disc_floor.output }}}}", f"{{{{ {r}.tax_bps }}}}"],
             digits=0),
        CALC(f"{p}_tax", "divide", [f"{{{{ steps.{p}_tax_raw.output }}}}", 10000], digits=0),
        CALC(f"{p}_total", "add", [f"{{{{ steps.{p}_disc_floor.output }}}}",
                                   f"{{{{ steps.{p}_tax.output }}}}"]),
        SET(f"{p}_quote", **{"quote": {
            "lines": "{{ vars.quote_lines }}",
            "discount_minor": "{{ vars.discount_minor }}",
            "tax_minor": f"{{{{ steps.{p}_tax.output }}}}",
            "total_minor": f"{{{{ steps.{p}_total.output }}}}",
            "currency": f"{{{{ {r}.currency }}}}",
        }, "total_minor": f"{{{{ steps.{p}_total.output }}}}"}),
    ]
    seq = [n[0] for n in nodes]
    edges = [(a, b) for a, b in zip(seq, seq[1:])]
    return nodes, edges, seq[-1]
