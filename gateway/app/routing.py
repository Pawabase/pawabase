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


def is_clean_path(path: str) -> bool:
    """Whether *path* is safe to forward as it stands.

    The route table matches on the path's prefix, but an upstream may *resolve* the path before using it, so ``/rest/../internal/v1/x``
    would be routed as ``/rest/`` and then reach ``/internal/``. Dot segments, empty segments, backslashes and control characters are
    therefore refused outright instead of being normalised: no legitimate client sends them.
    """
    if (
        not path.startswith("/")
        or "\\" in path
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in path)
    ):
        return False
    segments = path.split("/")[1:]
    if segments and segments[-1] == "":
        segments = segments[:-1]  # one trailing slash is ordinary
    return not any(segment in ("", ".", "..") for segment in segments)


def route_for(path: str) -> Upstream | None:
    if path.startswith(BLOCKED_PREFIXES):
        return None
    for upstream in ROUTES:
        if path.startswith(upstream.prefix) or path + "/" == upstream.prefix:
            return upstream
    return None
