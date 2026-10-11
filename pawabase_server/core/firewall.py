"""Firewall rules: the one matcher the gateway enforces and Studio's tester explains.

A rule has an ``action`` (``allow`` or ``block``) and a ``match``. It matches a request when every key
in its match does: ``ips`` (addresses or CIDRs), ``paths`` (globs), ``methods`` and ``user_agents``
(case-insensitive substrings). A key holding several values matches when any one does. Rules are
tried in order and the first match decides; a request no rule matches is let through.
"""

from __future__ import annotations

import fnmatch
import ipaddress
from collections.abc import Mapping, Sequence
from typing import Any

KEYS = ("ips", "paths", "methods", "user_agents")


class RuleError(ValueError):
    """A rule that can't be saved, and why."""


def clean(match: Mapping[str, Any]) -> dict[str, list[str]]:
    """A match with only known keys and trimmed, non-empty values. Raises :class:`RuleError`."""
    unknown = sorted(set(match) - set(KEYS))
    if unknown:
        raise RuleError(f"unknown match key {unknown[0]!r}; use {', '.join(KEYS)}")
    cleaned: dict[str, list[str]] = {}
    for key in KEYS:
        values = [str(v).strip() for v in (match.get(key) or []) if str(v).strip()]
        if not values:
            continue
        if key == "ips":
            for item in values:
                try:
                    ipaddress.ip_network(item, strict=False)
                except ValueError as exc:
                    raise RuleError(f"{item!r} is not an IP address or CIDR range") from exc
        if key == "methods":
            values = [v.upper() for v in values]
        cleaned[key] = values
    if not cleaned:
        raise RuleError(
            "a rule needs at least one thing to match: ips, paths, methods or user_agents"
        )
    return cleaned


def _ip_in(address: str, networks: Sequence[str]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(item, strict=False) for item in networks)


def matches(match: Mapping[str, Any], *, ip: str, path: str, method: str, user_agent: str) -> bool:
    if match.get("ips") and not _ip_in(ip, match["ips"]):
        return False
    if match.get("paths") and not any(fnmatch.fnmatchcase(path, p) for p in match["paths"]):
        return False
    if match.get("methods") and method.upper() not in {m.upper() for m in match["methods"]}:
        return False
    agent = user_agent.lower()
    return not (
        match.get("user_agents") and not any(u.lower() in agent for u in match["user_agents"])
    )


def evaluate(
    rules: Sequence[Mapping[str, Any]], *, ip: str, path: str, method: str, user_agent: str = ""
) -> Mapping[str, Any] | None:
    """The first enabled rule that matches, or ``None`` when the request is not covered."""
    for rule in rules:
        if rule.get("enabled", True) and matches(
            rule.get("match") or {}, ip=ip, path=path, method=method, user_agent=user_agent
        ):
            return rule
    return None
