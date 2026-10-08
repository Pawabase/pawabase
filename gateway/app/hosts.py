"""Which environment a hostname serves.

An API key already belongs to one environment, so a host mapping adds no new way in. It does
two things: a key used on the wrong environment's hostname is refused, and the hostname tells
key resolution which environment to expect, which is what lets a deployment give each
environment an address of its own (``api.example.com``, ``staging-api.example.com``).
"""

from __future__ import annotations


def parse(text: str) -> dict[str, str]:
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
            host = normalise(name)
            if not host:
                raise ValueError(f"PAWABASE_HOSTS: {pair!r} has an empty host")
            if host in hosts and hosts[host] != environment:
                raise ValueError(
                    f"PAWABASE_HOSTS: {host} serves both {hosts[host]!r} and {environment!r}"
                )
            hosts[host] = environment
    return hosts


def normalise(host: str) -> str:
    """A host as compared: lower case, without a port."""
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:8080
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0]
