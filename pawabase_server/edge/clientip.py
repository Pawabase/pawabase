"""Which address a request really came from.

``X-Forwarded-For`` is whatever the previous hop wrote, so a client can write anything in it. It is only believed when the connection
itself comes from a proxy the operator trusts (``trusted_proxies``); then the address is the last one in the chain that is *not* itself a
trusted proxy, which is the one the trusted infrastructure actually saw. Anything else is the socket's own peer address.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from typing import Any

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
PRIVATE = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
)
MAX_CHAIN = 20


def parse_address(text: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An address from *text*, tolerating whitespace, brackets and a port; ``None`` when it is not one."""
    if not text:
        return None
    text = text.strip().strip("[]")
    for candidate in (text, text.rsplit(":", 1)[0] if text.count(":") == 1 else text):
        try:
            return ipaddress.ip_address(candidate)
        except ValueError:
            continue
    return None


class TrustedProxies:
    """The set of peers whose ``X-Forwarded-For`` is believed."""

    def __init__(self, spec: str = "private") -> None:
        spec = (spec or "").strip()
        self.everyone = spec == "*"
        self.networks: tuple[IPNetwork, ...] = ()
        if spec == "private":
            self.networks = PRIVATE
        elif spec and spec != "*":
            self.networks = tuple(
                ipaddress.ip_network(part.strip(), strict=False)
                for part in spec.split(",")
                if part.strip()
            )

    def trusts(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> bool:
        if address is None:
            return False
        return self.everyone or any(address in network for network in self.networks)

    def client_ip(self, scope: Mapping[str, Any], headers: Mapping[str, str]) -> str:
        """The client's address, as text; ``unknown`` only when the server gave no peer at all."""
        peer_raw = scope.get("client")
        peer_text = peer_raw[0] if peer_raw else None
        peer = parse_address(peer_text)
        forwarded = headers.get("x-forwarded-for")
        if forwarded and self.trusts(peer):
            chain = [part for part in (p.strip() for p in forwarded.split(",")[:MAX_CHAIN]) if part]
            for hop in reversed(chain):
                address = parse_address(hop)
                if address is None:
                    # A garbled hop cannot be vouched for; stop here rather than skip to an earlier, less trustworthy one.
                    break
                if not self.trusts(address):
                    return str(address)
            if chain and (first := parse_address(chain[0])) is not None:
                return str(first)
        return str(peer) if peer is not None else (peer_text or "unknown")


def chain_for_upstream(client_ip: str, peer: str | None) -> str:
    """The ``X-Forwarded-For`` the gateway sends on: the verified client first, then the hop that handed the request over."""
    parts: Iterable[str] = [client_ip] if not peer or peer == client_ip else [client_ip, peer]
    return ", ".join(parts)
