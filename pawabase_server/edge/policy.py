"""The rules a resolved key and its environment impose on a request, in the order they are checked.

Each check returns a :class:`~pawabase_server.edge.errors.Refusal` or ``None``. HTTP requests and WebSocket upgrades go through the same
:func:`evaluate`, so a rule cannot apply to one and be forgotten on the other.
"""

from __future__ import annotations

import fnmatch
import ipaddress
from collections.abc import Mapping
from typing import Any

from pawabase_server.core import firewall
from pawabase_server.edge import scopes as key_scopes
from pawabase_server.edge.errors import WS_FORBIDDEN, WS_MAINTENANCE, Refusal
from pawabase_server.edge.request import Request


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def _within(address: str, networks: list[str]) -> bool:
    """Whether *address* is in any of *networks*. A bad stored range counts as no match, so rules fail closed."""
    ip = _address(address)
    if ip is None:
        return False
    for item in networks:
        try:
            if ip in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def check_origin(request: Request, context: Any, info: Mapping[str, Any]) -> Refusal | None:
    """A publishable key used from a browser may be limited to the origins the environment lists."""
    origins = info.get("cors_origins") or []
    origin = request.origin
    if context.role == "anon" and origins and origin and origin not in origins:
        return Refusal(403, "origin_not_allowed", f"{origin} may not use this key.")
    return None


def check_key(method: str, path: str, client_ip: str, info: Mapping[str, Any]) -> str | None:
    """A stable code when the key's own restrictions (addresses, routes) are not met."""
    allowed_ips = info.get("allowed_ips") or []
    if allowed_ips and not _within(client_ip, list(allowed_ips)):
        return "ip_not_allowed"
    if not key_scopes.allows(info.get("scopes") or (), method, path):
        return "scope_not_allowed"
    rules = info.get("allowed_routes") or []
    if rules:
        method = method.upper()
        for rule in rules:
            parts = rule.split(None, 1)
            rule_method, pattern = (
                (parts[0].upper(), parts[1]) if len(parts) == 2 else ("*", parts[0])
            )
            if rule_method in ("*", method) and fnmatch.fnmatchcase(path, pattern):
                return None
        return "route_not_allowed"
    return None


def check_firewall(request: Request, info: Mapping[str, Any]) -> Refusal | None:
    """Studio's firewall rules, first match wins; only a ``block`` refuses."""
    rules = info.get("firewall") or []
    if not rules:
        return None
    hit = firewall.evaluate(
        rules,
        ip=request.client_ip,
        path=request.path,
        method=request.method,
        user_agent=request.user_agent,
    )
    if hit and hit.get("action") == "block":
        name = str(hit.get("name") or "rule")
        return Refusal(
            403, "firewall_blocked", f"This request was blocked by a firewall rule ({name})."
        )
    return None


def check_host(served: str | None, context: Any) -> Refusal | None:
    """A key belongs to one environment; the hostname it is used on may serve a different one."""
    if served and context is not None and context.env != served:
        return Refusal(
            403,
            "host_environment_mismatch",
            "This API key belongs to a different environment than this address serves.",
        )
    return None


def environment_gate(
    client_ip: str, context: Any, info: Mapping[str, Any]
) -> tuple[str, str, int] | None:
    """Environment-wide settings, as ``(code, message, retry_after)`` when the request is refused.

    Maintenance mode refuses everything except secret keys (unless the environment says otherwise). The address allow-list restricts secret
    keys, because browsers on publishable keys have no stable address.
    """
    maintenance = info.get("maintenance") or {}
    if maintenance.get("enabled") and not (
        context.role == "service" and maintenance.get("allow_secret_keys", True)
    ):
        return (
            "maintenance",
            maintenance.get("message") or "This service is down for maintenance.",
            int(maintenance.get("retry_after") or 300),
        )
    allowlist = info.get("ip_allowlist") or []
    if allowlist and context.role == "service" and not _within(client_ip, list(allowlist)):
        return "ip_not_allowed", "Secret keys may not be used from this address.", 0
    return None


def _gate_refusal(gate: tuple[str, str, int]) -> Refusal:
    code, message, retry_after = gate
    if code == "maintenance":
        return Refusal(
            503,
            code,
            message,
            ws_code=WS_MAINTENANCE,
            extra={"retry_after": retry_after},
            headers=((b"retry-after", str(retry_after).encode()),),
        )
    return Refusal(403, code, message, ws_code=WS_FORBIDDEN)


def evaluate(
    request: Request, context: Any, info: Mapping[str, Any], *, served: str | None
) -> Refusal | None:
    """The first rule the request breaks, or ``None`` when it may proceed."""
    if (refusal := check_origin(request, context, info)) is not None:
        return refusal
    if (denied := check_key(request.method, request.path, request.client_ip, info)) is not None:
        return Refusal(403, denied, "This API key is not allowed for this request.")
    if (refusal := check_firewall(request, info)) is not None:
        return refusal
    if (refusal := check_host(served, context)) is not None:
        return refusal
    if (gate := environment_gate(request.client_ip, context, info)) is not None:
        return _gate_refusal(gate)
    return None
