"""Hostnames, custom domains and the firewall: what Studio stores and the edge reads.

A hostname can serve one environment: through the ``PAWABASE_HOSTS`` mapping or a custom domain. An API key already belongs to one
environment, so a mapping adds no new way in. It does two things: a key used on the wrong environment's hostname is refused, and the
hostname tells key resolution which environment to expect, which lets a deployment give each environment an address of its own
(``api.example.com``, ``staging-api.example.com``).

A domain is proven with a DNS TXT record, ``_pawabase.<hostname>`` holding the domain's token, looked up over DNS-over-HTTPS so the check
works from inside any network. Once verified, the server answers for the hostname. Certificates are not issued here: terminate TLS for the
hostname at the proxy or CDN in front of the server.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from pawabase_server.db.models import Domain

log = logging.getLogger("pawabase.networking")

DOH = "https://cloudflare-dns.com/dns-query"
LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


class DomainError(ValueError):
    """A hostname that can't be used, and why."""


def check_hostname(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(LABEL.match(part) for part in labels):
        raise DomainError(f"{value!r} is not a hostname, like api.example.com")
    if labels[-1].isdigit():
        raise DomainError("use a hostname, not an IP address")
    return host


def record_name(hostname: str) -> str:
    return f"_pawabase.{hostname}"


def new_token() -> str:
    return "pawabase-verify=" + secrets.token_hex(16)


async def txt_records(name: str) -> list[str]:
    """The TXT values published at *name*. Raises ``httpx.HTTPError`` when the lookup itself fails."""
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.get(
            DOH, params={"name": name, "type": "TXT"}, headers={"accept": "application/dns-json"}
        )
        response.raise_for_status()
    answers = response.json().get("Answer") or []
    return [str(a.get("data", "")).strip('"') for a in answers if a.get("type") == 16]


async def verify(domain: Domain) -> Domain:
    """Look the record up and record the outcome."""
    domain.last_checked_at = datetime.now(UTC)
    try:
        found = await txt_records(record_name(domain.hostname))
    except Exception as exc:
        domain.last_error = f"the DNS lookup failed: {exc}"
        await domain.save()
        return domain
    if domain.token in found:
        domain.status, domain.last_error = "verified", None
        domain.verified_at = domain.verified_at or domain.last_checked_at
    else:
        domain.status = "failed" if domain.status != "verified" else "verified"
        domain.last_error = (
            f"no TXT record at {record_name(domain.hostname)} with the value {domain.token}"
            + (f" (found: {', '.join(found)})" if found else "")
        )
    await domain.save()
    return domain


def view(domain: Domain) -> dict[str, Any]:
    return {
        "id": domain.id,
        "hostname": domain.hostname,
        "status": domain.status,
        "verified_at": domain.verified_at.isoformat() if domain.verified_at else None,
        "last_checked_at": domain.last_checked_at.isoformat() if domain.last_checked_at else None,
        "last_error": domain.last_error,
        "record": {"type": "TXT", "name": record_name(domain.hostname), "value": domain.token},
        "points_to": "Point the hostname (CNAME or A record) at your gateway once it is verified.",
    }


# ── which environment a hostname serves ─────────────────────────────────────


def normalise_host(host: str) -> str:
    """A host as compared: lower case, without a port."""
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:8080
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0]


def parse_hosts(text: str) -> dict[str, str]:
    """``production=api.example.com,staging=a.example.com|b.example.com`` as ``{host: environment}``.

    Raises:
        ValueError: A pair has no ``=``, an environment or host is empty, or a host appears twice.
    """
    hosts: dict[str, str] = {}
    for pair in (part.strip() for part in text.split(",")):
        if not pair:
            continue
        environment, separator, names = pair.partition("=")
        environment = environment.strip()
        if not separator or not environment or not names.strip():
            raise ValueError(f"PAWABASE_HOSTS: {pair!r} is not environment=host[|host...]")
        for name in names.split("|"):
            host = normalise_host(name)
            if not host:
                raise ValueError(f"PAWABASE_HOSTS: {pair!r} has an empty host")
            if host in hosts and hosts[host] != environment:
                raise ValueError(
                    f"PAWABASE_HOSTS: {host} serves both {hosts[host]!r} and {environment!r}"
                )
            hosts[host] = environment
    return hosts


async def verified_domains() -> dict[str, str]:
    """Every verified custom domain, as ``{hostname: environment}``."""
    rows = await Domain.filter(status="verified")
    return {row.hostname.lower(): row.env for row in rows}


class DomainSync:
    """Keeps a mapping of verified hostnames current: it is read on every request, so it is refreshed in the background."""

    def __init__(
        self,
        target: Any,
        every: float = 30,
        *,
        source: Callable[[], Awaitable[dict[str, str]]] = verified_domains,
    ) -> None:
        self.target = target  # anything with a ``domains`` dict
        self.every = every
        self.source = source
        self.task: asyncio.Task[None] | None = None

    async def refresh(self) -> int:
        try:
            self.target.domains = await self.source()
        except Exception as exc:
            log.debug("domain refresh skipped: %s", exc)
        return len(self.target.domains)

    async def _loop(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.every)

    def start(self) -> None:
        if self.task is None and self.every > 0:
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
