"""Custom domains and the firewall: what Studio stores and the gateway reads.

A domain is proven with a DNS TXT record, ``_pawabase.<hostname>`` holding the domain's token, looked up
over DNS-over-HTTPS so the check works from inside any network. Once verified, the gateway answers for the
hostname. Certificates are not issued here: terminate TLS for the hostname at the proxy or CDN in front of
the gateway.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx

from database.models import Domain

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
