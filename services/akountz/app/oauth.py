"""Social and enterprise sign-in, with ``sillo-oauth``.

Each environment configures providers with its own credentials::

    "providers": {
        "google":  {"client_id": "...", "client_secret": "secret://GOOGLE_SECRET"},
        "github":  {"client_id": "...", "client_secret": "secret://GITHUB_SECRET"},
        "microsoft": {"client_id": "...", "client_secret": "...", "tenant": "common"},
        "gitlab":  {"client_id": "...", "client_secret": "...",
                    "authorize_endpoint": "...", "token_endpoint": "...",
                    "userinfo_endpoint": "...", "scopes": ["read_user"]}
    }

Google, GitHub, Discord and Microsoft are ``sillo-oauth``'s shipped
providers. Any other name with endpoints becomes a generic
``OAuthProvider`` (OAuth 2.0, or OIDC when ``openid`` is in the scopes).
State, PKCE and profile mapping are ``sillo-oauth``'s; Akountz decides what a
verified profile means.
"""

from __future__ import annotations

from typing import Any

from sillo.exceptions import HTTPException
from sillo_oauth import (
    DiscordOAuthProvider,
    GithubOAuthProvider,
    GoogleOAuthProvider,
    MicrosoftOAuthProvider,
    OAuthProvider,
)

from app.environment import AuthConfig
from app.platform import Akountz

SHIPPED = {
    "google": GoogleOAuthProvider,
    "github": GithubOAuthProvider,
    "discord": DiscordOAuthProvider,
    "microsoft": MicrosoftOAuthProvider,
}
GENERIC_KEYS = ("authorize_endpoint", "token_endpoint", "userinfo_endpoint")

#: Tests substitute an ``httpx`` transport here.
TRANSPORT: Any = None


def callback_url(akountz: Akountz, config: AuthConfig, provider: str) -> str:
    base = (config.public_url or akountz.settings.public_url).rstrip("/")
    return f"{base}/auth/v1/callback/{config.project}/{config.env}/{provider}"


def build_provider(akountz: Akountz, config: AuthConfig, name: str) -> Any:
    settings = config.providers.get(name)
    if not settings or not settings.get("enabled", True) or not settings.get("client_id"):
        raise HTTPException(status_code=404, detail=f"the {name!r} provider is not enabled")
    options: dict[str, Any] = {
        "client_id": settings["client_id"],
        "client_secret": settings.get("client_secret", ""),
        "state_secret": akountz.state_secret(config.project, config.env),
        "redirect_uri": callback_url(akountz, config, name),
    }
    if settings.get("scopes"):
        options["scopes"] = list(settings["scopes"])
    if settings.get("authorize_params"):
        options["authorize_params"] = dict(settings["authorize_params"])
    if TRANSPORT is not None:
        options["transport"] = TRANSPORT
    if name in SHIPPED:
        if name == "microsoft" and settings.get("tenant"):
            options["tenant"] = settings["tenant"]
        for key in GENERIC_KEYS:
            if settings.get(key):
                options[key] = settings[key]
        return SHIPPED[name](**options)
    if not all(settings.get(key) for key in GENERIC_KEYS):
        raise HTTPException(
            status_code=500,
            detail=f"provider {name!r} needs authorize, token and userinfo endpoints",
        )
    return OAuthProvider(name=name, **options, **{key: settings[key] for key in GENERIC_KEYS})
