"""The public route table: which paths the outside world may reach, and what each needs.

Everything else the server serves (``/internal``, ``/admin``, Studio) is never reachable through the public entry point.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Upstream:
    """Where a public path prefix goes.

    Attributes:
        prefix: The public path prefix.
        group: What the path belongs to, for counters and latency (``auth``, ``data``, ``storage``...).
        key: ``required``, ``optional`` (resolved when present) or ``none``.
    """

    prefix: str
    group: str
    key: str = "required"


# Most specific first: the first matching prefix wins.
ROUTES: tuple[Upstream, ...] = (
    # Browser navigations and email links carry no API key; the environment is in the path.
    Upstream("/auth/v1/authorize/", "auth", "none"),
    Upstream("/auth/v1/callback/", "auth", "none"),
    Upstream("/auth/v1/links/", "auth", "none"),
    # A SAML identity provider posts the person back here, with no key; the environment is in the path.
    Upstream("/auth/v1/saml/", "auth", "none"),
    # The hosted sign-in pages are browser navigations too.
    Upstream("/auth/v1/hosted/", "auth", "none"),
    Upstream("/auth/v1/", "auth"),
    Upstream("/rest/", "data"),
    Upstream("/storage/v1/signed/", "storage", "none"),
    # Object URLs go in ``<img src>`` and ``<video src>``, which cannot send a key header. The environment is in the path of
    # ``files``; a share link's token names its own object. Policies and grants are evaluated by the storage routes.
    Upstream("/storage/v1/files/", "storage", "none"),
    Upstream("/storage/v1/share/", "storage", "none"),
    Upstream("/storage/v1/", "storage"),
    # HTTP functions whose config says ``auth: "public"`` (webhooks from other services): no key; the environment is in the path.
    Upstream("/functions/v1/public/", "functions", "none"),
    Upstream("/functions/v1/", "functions"),
    Upstream("/flows/v1/", "flows"),
    Upstream("/hooks/v1/", "hooks", "none"),
    Upstream("/docs/v1/", "docs", "none"),
    # Liveness check for the key's environment. Requires an apikey (default "required") so it also proves the key itself resolves.
    Upstream("/health/v1/", "health"),
    Upstream("/realtime/v1/", "realtime"),
    # The management plane takes a secret API key (the Functions CLI, automation). Studio never comes through here: it reaches the
    # management plane in-process with a service token.
    Upstream("/platform/v1/", "management"),
)

#: What the public entry point answers itself, with no key: the landing page, liveness and the status page.
OPEN_PATHS = frozenset({"/", "/health", "/health/ready", "/status", "/v1/status"})
OPEN_PREFIXES = ("/status/",)

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


def is_open(path: str) -> bool:
    """Whether the public entry point serves *path* without a key."""
    return path in OPEN_PATHS or path.startswith(OPEN_PREFIXES)


def route_for(path: str) -> Upstream | None:
    if path.startswith(BLOCKED_PREFIXES):
        return None
    for upstream in ROUTES:
        if path.startswith(upstream.prefix) or path + "/" == upstream.prefix:
            return upstream
    return None
