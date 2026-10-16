"""Studio's side of networking: the domains an environment answers on. The firewall is ``routes/platform/firewall.py``."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sillo import HttpContext, Router, created, no_content
from sillo.exceptions import HTTPException

from pawabase_server.db.models import Domain
from pawabase_server.edge import networking
from pawabase_server.platform import Platform
from pawabase_server.routes.common import MANAGE, audit, get_environment


class DomainBody(BaseModel):
    hostname: str = Field(min_length=3, max_length=253)


def register(r: Router, platform: Platform) -> None:
    base = "/envs/{env}"

    async def domain_of(env: str, domain_id: str) -> Domain:
        row = await Domain.get_or_none(id=domain_id, env=env)
        if row is None:
            raise HTTPException(status_code=404, detail="no such domain")
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
