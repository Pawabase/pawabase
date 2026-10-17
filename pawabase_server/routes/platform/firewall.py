"""Studio's side of the firewall: rules, managed rule sets, automatic and manual bans, what the firewall did, and a request tester.

The gateway enforces what is stored here (``edge/firewall.py``); every change tells it to reload the environment's configuration.
"""

from __future__ import annotations

import ipaddress
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException
from tortoise.expressions import Q

from pawabase_server.core import firewall
from pawabase_server.db.models import FirewallBan, FirewallConfig, FirewallEvent, FirewallRule
from pawabase_server.platform import Platform
from pawabase_server.routes.common import MANAGE, audit, dump, get_environment

Action = Literal["allow", "block", "log", "rate_limit"]


class Condition(BaseModel):
    field: str = Field(min_length=1, max_length=80)
    op: str = Field(min_length=1, max_length=16)
    value: Any = None


class RateLimit(BaseModel):
    requests: int
    window_seconds: int
    by: str = "ip"
    ban_seconds: int = 0


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    action: Action = "block"
    enabled: bool = True
    mode: Literal["enforce", "simulate"] = "enforce"
    match: dict[str, list[str]] = Field(default_factory=dict)
    conditions: list[Condition] = Field(default_factory=list, max_length=firewall.MAX_CONDITIONS)
    logic: Literal["all", "any"] = "all"
    rate_limit: RateLimit | None = None
    expires_at: datetime | None = None
    note: str = Field(default="", max_length=500)


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    action: Action | None = None
    enabled: bool | None = None
    mode: Literal["enforce", "simulate"] | None = None
    match: dict[str, list[str]] | None = None
    conditions: list[Condition] | None = None
    logic: Literal["all", "any"] | None = None
    rate_limit: RateLimit | None = None
    expires_at: datetime | None = None
    clear_expiry: bool = False
    note: str | None = Field(default=None, max_length=500)


class Order(BaseModel):
    ids: list[str] = Field(max_length=500)


class ConfigBody(BaseModel):
    managed: dict[str, str] | None = None
    auto_ban: dict[str, Any] | None = None


class BanBody(BaseModel):
    ip: str = Field(min_length=2, max_length=64)
    reason: str = Field(default="", max_length=300)
    #: Seconds; omitted or 0 bans until removed.
    seconds: int = Field(default=0, ge=0, le=365 * 86_400)


class Probe(BaseModel):
    ip: str
    path: str = "/"
    method: str = "GET"
    user_agent: str = ""
    query: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    country: str = ""
    role: Literal["none", "anon", "service"] = "none"


def _validated(rule: dict[str, Any]) -> dict[str, Any]:
    try:
        return firewall.clean_rule(rule)
    except firewall.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _view(row: FirewallRule) -> dict[str, Any]:
    out = dump(row)
    out["expired"] = bool(row.expires_at and row.expires_at <= datetime.now(UTC))
    return out


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    def reload(env: str) -> None:
        engine = platform.app.state.get("firewall") if platform.app is not None else None
        if engine is not None:
            engine.invalidate(env)

    async def rule_of(env: str, rule_id: str) -> FirewallRule:
        row = await FirewallRule.get_or_none(id=rule_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such rule")
        return row

    # ── rules ────────────────────────────────────────────────────────────

    @r.get(
        f"{base}/firewall",
        auth=MANAGE,
        tags=["firewall"],
        summary="Firewall rules, in the order they are tried",
    )
    async def list_rules(ctx: HttpContext, env: str):
        await get_environment(env)
        return {"data": [_view(row) for row in await FirewallRule.filter(env=env)]}

    @r.post(
        f"{base}/firewall",
        auth=MANAGE,
        tags=["firewall"],
        request_model=RuleBody,
        summary="Add a firewall rule at the end",
    )
    async def add_rule(ctx: HttpContext, env: str, body: RuleBody):
        await get_environment(env)
        rule = _validated(body.model_dump())
        last = await FirewallRule.filter(env=env).order_by("-position").first()
        row = await FirewallRule.create(
            env=env,
            name=body.name,
            enabled=body.enabled,
            mode=body.mode,
            expires_at=body.expires_at,
            note=body.note,
            position=(last.position + 1) if last else 0,
            **rule,
        )
        reload(env)
        await audit(
            ctx,
            "firewall.rule_added",
            env=env,
            target=row.id,
            details={"action": row.action, "name": row.name},
        )
        return created(_view(row))

    @r.patch(
        f"{base}/firewall/{{rule_id}}",
        auth=MANAGE,
        tags=["firewall"],
        request_model=RulePatch,
        summary="Change a firewall rule",
    )
    async def patch_rule(ctx: HttpContext, env: str, rule_id: str, body: RulePatch):
        row = await rule_of(env, rule_id)
        changes = body.model_dump(exclude_unset=True)
        merged = {
            "action": changes.get("action", row.action),
            "match": changes.get("match", row.match or {}),
            "conditions": changes.get("conditions", row.conditions or []),
            "logic": changes.get("logic", row.logic),
            "rate_limit": changes.get("rate_limit", row.rate_limit),
        }
        for key, value in _validated(merged).items():
            setattr(row, key, value)
        for key in ("name", "enabled", "mode", "note", "expires_at"):
            if key in changes and (changes[key] is not None or key == "expires_at"):
                setattr(row, key, changes[key])
        if changes.get("clear_expiry"):
            row.expires_at = None
        await row.save()
        reload(env)
        await audit(
            ctx, "firewall.rule_changed", env=env, target=row.id, details={"name": row.name}
        )
        return _view(row)

    @r.delete(
        f"{base}/firewall/{{rule_id}}",
        auth=MANAGE,
        tags=["firewall"],
        summary="Delete a firewall rule",
    )
    async def delete_rule(ctx: HttpContext, env: str, rule_id: str):
        row = await rule_of(env, rule_id)
        await row.delete()
        reload(env)
        await audit(
            ctx, "firewall.rule_removed", env=env, target=rule_id, details={"name": row.name}
        )
        return no_content()

    @r.put(
        f"{base}/firewall-order",
        auth=MANAGE,
        tags=["firewall"],
        request_model=Order,
        summary="Set the order rules are tried in",
    )
    async def order_rules(ctx: HttpContext, env: str, body: Order):
        await get_environment(env)
        rows = {row.id: row for row in await FirewallRule.filter(env=env)}
        if set(body.ids) != set(rows) or len(body.ids) != len(rows):
            raise HTTPException(
                status_code=422, detail="list every rule of this environment exactly once"
            )
        for position, rule_id in enumerate(body.ids):
            await FirewallRule.filter(id=rule_id).update(position=position)
        reload(env)
        await audit(ctx, "firewall.rules_reordered", env=env, target="rules")
        return {"data": [_view(row) for row in await FirewallRule.filter(env=env)]}

    # ── managed rules and automatic bans ─────────────────────────────────

    @r.get(
        f"{base}/firewall-config",
        auth=MANAGE,
        tags=["firewall"],
        summary="Managed rule sets and automatic bans",
    )
    async def get_config(ctx: HttpContext, env: str):
        await get_environment(env)
        row = await FirewallConfig.get_or_none(env=env)
        managed = (row.managed if row else {}) or {}
        return {
            "managed": [
                {
                    "key": key,
                    "label": spec["label"],
                    "about": spec["about"],
                    "action": managed.get(key, "off"),
                }
                for key, spec in firewall.MANAGED.items()
            ],
            "auto_ban": firewall.clean_auto_ban((row.auto_ban if row else {}) or {}),
            "fields": [*firewall.FIELDS, "header:<name>", "query:<name>"],
            "operators": list(firewall.OPS),
        }

    @r.put(
        f"{base}/firewall-config",
        auth=MANAGE,
        tags=["firewall"],
        request_model=ConfigBody,
        summary="Change managed rule sets or automatic bans",
    )
    async def put_config(ctx: HttpContext, env: str, body: ConfigBody):
        await get_environment(env)
        row, _ = await FirewallConfig.get_or_create(
            env=env, defaults={"managed": {}, "auto_ban": {}}
        )
        try:
            if body.managed is not None:
                row.managed = {**(row.managed or {}), **firewall.clean_managed(body.managed)}
            if body.auto_ban is not None:
                row.auto_ban = firewall.clean_auto_ban({**(row.auto_ban or {}), **body.auto_ban})
        except firewall.RuleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await row.save()
        reload(env)
        await audit(
            ctx,
            "firewall.config_changed",
            env=env,
            target="config",
            details=body.model_dump(exclude_none=True),
        )
        return await get_config(ctx, env)

    # ── bans ─────────────────────────────────────────────────────────────

    @r.get(
        f"{base}/firewall-bans",
        auth=MANAGE,
        tags=["firewall"],
        summary="Addresses refused now (and, with ?all=1, earlier bans)",
    )
    async def list_bans(ctx: HttpContext, env: str):
        await get_environment(env)
        rows = FirewallBan.filter(env=env)
        if not ctx.query_params.get("all"):
            rows = rows.filter(Q(expires_at=None) | Q(expires_at__gt=datetime.now(UTC)))
        return {"data": [dump(b) for b in await rows.limit(500)]}

    @r.post(
        f"{base}/firewall-bans",
        auth=MANAGE,
        tags=["firewall"],
        request_model=BanBody,
        summary="Ban an address or range",
    )
    async def add_ban(ctx: HttpContext, env: str, body: BanBody):
        await get_environment(env)
        try:
            network = ipaddress.ip_network(body.ip.strip(), strict=False)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"{body.ip!r} is not an IP address or CIDR range"
            ) from exc
        row = await FirewallBan.create(
            env=env,
            ip=str(network) if network.num_addresses > 1 else str(network.network_address),
            source="manual",
            reason=body.reason or "Banned in Studio",
            expires_at=datetime.now(UTC) + timedelta(seconds=body.seconds)
            if body.seconds
            else None,
        )
        reload(env)
        await audit(
            ctx, "firewall.ban_added", env=env, target=row.ip, details={"seconds": body.seconds}
        )
        return created(dump(row))

    @r.delete(
        f"{base}/firewall-bans/{{ban_id}}", auth=MANAGE, tags=["firewall"], summary="Lift a ban"
    )
    async def lift_ban(ctx: HttpContext, env: str, ban_id: str):
        row = await FirewallBan.get_or_none(id=ban_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such ban")
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await row.save(update_fields=["expires_at", "updated_at"])
        reload(env)
        await audit(ctx, "firewall.ban_lifted", env=env, target=row.ip)
        return no_content()

    # ── what the firewall did ────────────────────────────────────────────

    @r.get(
        f"{base}/firewall-events",
        auth=MANAGE,
        tags=["firewall"],
        summary="Requests the firewall acted on, newest first",
    )
    async def list_events(ctx: HttpContext, env: str):
        await get_environment(env)
        q = ctx.query_params
        rows = FirewallEvent.filter(env=env)
        for name in ("action", "source", "ip", "rule_id", "country"):
            if q.get(name):
                rows = rows.filter(**{name: q[name]})
        if q.get("path"):
            rows = rows.filter(path__icontains=q["path"])
        limit = max(1, min(int(q.get("limit") or 100), 500))
        return {"data": [dump(e) for e in await rows.order_by("-id").limit(limit)]}

    @r.get(
        f"{base}/firewall-stats",
        auth=MANAGE,
        tags=["firewall"],
        summary="What the firewall did over the last hours",
    )
    async def stats(ctx: HttpContext, env: str):
        await get_environment(env)
        hours = max(1, min(int(ctx.query_params.get("hours") or 24), 24 * 30))
        since = datetime.now(UTC) - timedelta(hours=hours)
        rows = await FirewallEvent.filter(env=env, created_at__gte=since).values(
            "ip", "path", "rule_name", "action", "source", "country", "created_at", "simulated"
        )

        def top(key: str, n: int = 10) -> list[dict[str, Any]]:
            counts = Counter(r[key] for r in rows if r[key])
            return [{"value": v, "count": c} for v, c in counts.most_common(n)]

        timeline: Counter = Counter()
        for row in rows:
            moment = row["created_at"]
            timeline[
                (moment.replace(minute=0, second=0, microsecond=0).isoformat(), row["action"])
            ] += 1
        active_bans = (
            await FirewallBan.filter(env=env)
            .filter(Q(expires_at=None) | Q(expires_at__gt=datetime.now(UTC)))
            .count()
        )
        return {
            "hours": hours,
            "total": len(rows),
            "by_action": dict(Counter(r["action"] for r in rows)),
            "by_source": dict(Counter(r["source"] for r in rows)),
            "simulated": sum(1 for r in rows if r["simulated"]),
            "top_ips": top("ip"),
            "top_paths": top("path"),
            "top_rules": top("rule_name"),
            "top_countries": top("country"),
            "timeline": [
                {"hour": h, "action": a, "count": c} for (h, a), c in sorted(timeline.items())
            ],
            "active_bans": active_bans,
        }

    # ── tester ───────────────────────────────────────────────────────────

    @r.post(
        f"{base}/firewall-test",
        auth=MANAGE,
        tags=["firewall"],
        request_model=Probe,
        summary="What the firewall would do with a request, step by step",
    )
    async def test_rules(ctx: HttpContext, env: str, body: Probe):
        await get_environment(env)
        query = body.query.lstrip("?")
        facts = firewall.Facts(
            ip=body.ip,
            method=body.method.upper(),
            path=body.path,
            query=query,
            headers={
                **{k.lower(): v for k, v in body.headers.items()},
                **({"user-agent": body.user_agent} if body.user_agent else {}),
            },
            params=dict(
                pair.split("=", 1) if "=" in pair else (pair, "")
                for pair in query.split("&")
                if pair
            ),
            country=body.country.upper(),
            role=body.role,
        )
        rules = [dump(row) for row in await FirewallRule.filter(env=env)]
        now = datetime.now(UTC)
        steps: list[dict[str, Any]] = []
        outcome, decider = "allow", None
        live = [
            r
            for r in rules
            if r["enabled"] and not (r.get("expires_at") and r["expires_at"] <= now)
        ]
        allowed = any(
            r["action"] == "allow"
            and r.get("mode") != "simulate"
            and firewall.rule_matches(r, facts)
            for r in live
        )
        try:
            address = ipaddress.ip_address(body.ip)
        except ValueError:
            address = None
        bans = await FirewallBan.filter(env=env).filter(Q(expires_at=None) | Q(expires_at__gt=now))
        ban = next(
            (
                b
                for b in bans
                if address is not None
                and (network := ipaddress.ip_network(b.ip, strict=False)).version == address.version
                and address in network
            ),
            None,
        )
        if ban and not allowed:
            steps.append({"step": "ban", "rule": ban.ip, "result": f"banned ({ban.reason})"})
            outcome = "block"
        else:
            if ban:
                steps.append(
                    {
                        "step": "ban",
                        "rule": ban.ip,
                        "result": "banned, but an allow rule lets it through",
                    }
                )
            for rule in live:
                if not firewall.rule_matches(rule, facts):
                    continue
                simulated = rule.get("mode") == "simulate"
                if rule["action"] == "log" or simulated:
                    steps.append(
                        {
                            "step": "rule",
                            "rule": rule["name"],
                            "result": "recorded only" if simulated else "logged, continues",
                        }
                    )
                    continue
                if rule["action"] == "rate_limit":
                    limit = rule.get("rate_limit") or {}
                    steps.append(
                        {
                            "step": "rule",
                            "rule": rule["name"],
                            "result": f"counted: refused past {limit.get('requests')} per {limit.get('window_seconds')}s by {limit.get('by')}",
                        }
                    )
                    continue
                steps.append(
                    {
                        "step": "rule",
                        "rule": rule["name"],
                        "result": "allowed" if rule["action"] == "allow" else "blocked",
                    }
                )
                outcome, decider = rule["action"], rule
                break
            if decider is None:
                config = await FirewallConfig.get_or_none(env=env)
                hit = firewall.managed_hit((config.managed if config else {}) or {}, facts)
                if hit:
                    name, action = hit
                    steps.append(
                        {
                            "step": "managed",
                            "rule": firewall.MANAGED[name]["label"],
                            "result": "blocked" if action == "block" else "logged",
                        }
                    )
                    if action == "block":
                        outcome = "block"
        return {
            "outcome": outcome,
            "rule": {"id": decider["id"], "name": decider["name"], "action": decider["action"]}
            if decider
            else None,
            "steps": steps,
        }
