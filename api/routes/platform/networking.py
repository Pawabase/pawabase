"""Studio's side of networking: the domains an environment answers on, and its firewall rules."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from app import networking
from app.platform import Platform
from database.models import Domain, FirewallRule
from pawabase_core import firewall
from routes.common import MANAGE, audit, dump, get_environment


class DomainBody(BaseModel):
    hostname: str = Field(min_length=3, max_length=253)


class RuleBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    action: Literal["allow", "block"] = "block"
    enabled: bool = True
    match: dict[str, list[str]] = Field(default_factory=dict)
    note: str = Field(default="", max_length=500)


class RulePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    action: Literal["allow", "block"] | None = None
    enabled: bool | None = None
    match: dict[str, list[str]] | None = None
    note: str | None = Field(default=None, max_length=500)


class Order(BaseModel):
    ids: list[str] = Field(max_length=500)


class Probe(BaseModel):
    ip: str
    path: str = "/"
    method: str = "GET"
    user_agent: str = ""


def _checked(match: dict[str, list[str]]) -> dict[str, list[str]]:
    try:
        return firewall.clean(match)
    except firewall.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    async def domain_of(env: str, domain_id: str) -> Domain:
        row = await Domain.get_or_none(id=domain_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such domain")
        return row

    async def rule_of(env: str, rule_id: str) -> FirewallRule:
        row = await FirewallRule.get_or_none(id=rule_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such rule")
        return row

    @r.get(
        f"{base}/domains",
        auth=MANAGE,
        tags=["networking"],
        summary="Domains this environment answers on",
    )
    async def list_domains(ctx: HttpContext, env: str):
        await get_environment(env)
        return {"data": [networking.view(d) for d in await Domain.filter(env=env)]}

    @r.post(
        f"{base}/domains",
        auth=MANAGE,
        tags=["networking"],
        request_model=DomainBody,
        summary="Add a domain; prove it with a DNS record",
    )
    async def add_domain(ctx: HttpContext, env: str, body: DomainBody):
        await get_environment(env)
        try:
            hostname = networking.check_hostname(body.hostname)
        except networking.DomainError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if await Domain.filter(hostname=hostname).exists():
            raise HTTPException(status_code=409, detail=f"{hostname} is already registered")
        row = await Domain.create(env=env, hostname=hostname, token=networking.new_token())
        await audit(ctx, "domain.added", env=env, target=hostname)
        return created(networking.view(row))

    @r.post(
        f"{base}/domains/{{domain_id}}/verify",
        auth=MANAGE,
        tags=["networking"],
        summary="Check the DNS record now",
    )
    async def verify_domain(ctx: HttpContext, env: str, domain_id: str):
        row = await networking.verify(await domain_of(env, domain_id))
        await audit(
            ctx, "domain.checked", env=env, target=row.hostname, details={"status": row.status}
        )
        return networking.view(row)

    @r.delete(
        f"{base}/domains/{{domain_id}}", auth=MANAGE, tags=["networking"], summary="Remove a domain"
    )
    async def delete_domain(ctx: HttpContext, env: str, domain_id: str):
        row = await domain_of(env, domain_id)
        await row.delete()
        await audit(ctx, "domain.removed", env=env, target=row.hostname)
        return no_content()

    @r.get(
        f"{base}/firewall",
        auth=MANAGE,
        tags=["networking"],
        summary="Firewall rules, in the order they are tried",
    )
    async def list_rules(ctx: HttpContext, env: str):
        await get_environment(env)
        return {"data": [dump(row) for row in await FirewallRule.filter(env=env)]}

    @r.post(
        f"{base}/firewall",
        auth=MANAGE,
        tags=["networking"],
        request_model=RuleBody,
        summary="Add a firewall rule at the end",
    )
    async def add_rule(ctx: HttpContext, env: str, body: RuleBody):
        await get_environment(env)
        last = await FirewallRule.filter(env=env).order_by("-position").first()
        row = await FirewallRule.create(
            env=env,
            name=body.name,
            action=body.action,
            enabled=body.enabled,
            match=_checked(body.match),
            note=body.note,
            position=(last.position + 1) if last else 0,
        )
        await audit(
            ctx, "firewall.rule_added", env=env, target=row.id, details={"action": body.action}
        )
        return created(dump(row))

    @r.patch(
        f"{base}/firewall/{{rule_id}}",
        auth=MANAGE,
        tags=["networking"],
        request_model=RulePatch,
        summary="Change a firewall rule",
    )
    async def patch_rule(ctx: HttpContext, env: str, rule_id: str, body: RulePatch):
        row = await rule_of(env, rule_id)
        values: dict[str, Any] = body.model_dump(exclude_none=True)
        if "match" in values:
            values["match"] = _checked(values["match"])
        for key, value in values.items():
            setattr(row, key, value)
        await row.save()
        await audit(ctx, "firewall.rule_changed", env=env, target=row.id)
        return dump(row)

    @r.delete(
        f"{base}/firewall/{{rule_id}}",
        auth=MANAGE,
        tags=["networking"],
        summary="Delete a firewall rule",
    )
    async def delete_rule(ctx: HttpContext, env: str, rule_id: str):
        row = await rule_of(env, rule_id)
        await row.delete()
        await audit(ctx, "firewall.rule_removed", env=env, target=rule_id)
        return no_content()

    @r.put(
        f"{base}/firewall-order",
        auth=MANAGE,
        tags=["networking"],
        request_model=Order,
        summary="Set the order rules are tried in",
    )
    async def order_rules(ctx: HttpContext, env: str, body: Order):
        await get_environment(env)
        rows = {row.id: row for row in await FirewallRule.filter(env=env)}
        if set(body.ids) != set(rows):
            raise HTTPException(
                status_code=422, detail="list every rule of this environment exactly once"
            )
        for position, rule_id in enumerate(body.ids):
            await FirewallRule.filter(id=rule_id).update(position=position)
        return {"data": [dump(row) for row in await FirewallRule.filter(env=env)]}

    @r.post(
        f"{base}/firewall-test",
        auth=MANAGE,
        tags=["networking"],
        request_model=Probe,
        summary="Which rule would decide a request",
    )
    async def test_rules(ctx: HttpContext, env: str, body: Probe):
        await get_environment(env)
        rules = [dump(row) for row in await FirewallRule.filter(env=env)]
        hit = firewall.evaluate(
            rules, ip=body.ip, path=body.path, method=body.method, user_agent=body.user_agent
        )
        return {
            "outcome": (hit["action"] if hit else "allow"),
            "rule": {"id": hit["id"], "name": hit["name"], "action": hit["action"]}
            if hit
            else None,
        }
