"""The public route table: which path goes to which service, and what it needs.

This is the only place the internal topology is known. Clients see one origin.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Upstream:
    """Where a public path prefix goes.

    Attributes:
        prefix: The public path prefix.
        service: ``api``, ``akountz`` or ``angula``.
        key: ``required``, ``optional`` (resolved when present) or ``none``.
    """

    prefix: str
    service: str
    key: str = "required"


# Most specific first: the first matching prefix wins.
ROUTES: tuple[Upstream, ...] = (
    # Browser navigations and email links carry no API key; the environment is in the path.
    Upstream("/auth/v1/authorize/", "akountz", "none"),
    Upstream("/auth/v1/callback/", "akountz", "none"),
    Upstream("/auth/v1/links/", "akountz", "none"),
    Upstream("/auth/v1/", "akountz"),
    Upstream("/rest/", "api"),
    Upstream("/storage/v1/signed/", "api", "none"),
    Upstream("/storage/v1/", "api"),
    Upstream("/functions/v1/", "api"),
    Upstream("/flows/v1/", "api"),
    Upstream("/hooks/v1/", "api", "none"),
    Upstream("/docs/v1/", "api", "none"),
    # Liveness check for the key's environment. Requires an apikey (default
    # "required") so it also proves the key itself resolves.
    Upstream("/health/v1/", "api"),
    Upstream("/realtime/v1/", "angula"),
    # The management plane takes a secret API key (the Functions CLI, automation),
    # which the gateway turns into a signed context. Studio never comes through
    # here: it reaches the API directly with a service token.
    Upstream("/platform/v1/", "api"),
)

#: Never exposed publicly, whatever the prefix table says.
BLOCKED_PREFIXES = ("/internal/", "/admin/")


def route_for(path: str) -> Upstream | None:
    if path.startswith(BLOCKED_PREFIXES):
        return None
    for upstream in ROUTES:
        if path.startswith(upstream.prefix) or path + "/" == upstream.prefix:
            return upstream
    return None
