"""An environment's authentication settings, as Akountz sees them.

The runtime's configuration belongs to the API: Akountz reads each environment's
``auth`` section from it (cached briefly with Sillo's cache) and applies
defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sillo.exceptions import HTTPException

from pawabase_core.clients import ServiceError

if TYPE_CHECKING:
    from app.platform import Akountz


@dataclass
class AuthConfig:
    """Authentication settings for one environment, with defaults applied."""

    env: str
    project_name: str = ""
    signup_enabled: bool = True
    require_email_verification: bool = False
    password_policy: str = "basic"
    password_min_length: int = 8
    access_ttl: int = 900
    refresh_ttl: int = 30 * 24 * 3600
    magic_link_enabled: bool = True
    mfa_enabled: bool = True
    site_url: str = ""
    redirect_urls: list[str] = field(default_factory=list)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    default_roles: list[str] = field(default_factory=list)
    emails: dict[str, dict[str, str]] = field(default_factory=dict)
    public_url: str = ""
    #: Failed sign-ins before a temporary lock; unset uses the installation's default.
    lockout_threshold: int | None = None
    lockout_minutes: int | None = None
    #: Who may sign up. An empty allow list means everyone; a blocked domain is always refused.
    allowed_email_domains: list[str] = field(default_factory=list)
    blocked_email_domains: list[str] = field(default_factory=list)
    #: Most active sessions per user; 0 is unlimited. The oldest session ends when a new one starts over the limit.
    max_sessions: int = 0

    @classmethod
    def from_api(cls, env: str, payload: dict[str, Any]) -> AuthConfig:
        auth = payload.get("auth") or {}
        known = set(cls.__dataclass_fields__) - {"env", "project_name", "public_url"}
        values = {key: auth[key] for key in known if key in auth and auth[key] is not None}
        return cls(
            env=env,
            project_name=payload.get("project_name", ""),
            public_url=payload.get("public_url", ""),
            **values,
        )

    def lockout(self, default_threshold: int, default_minutes: int) -> tuple[int, int]:
        """The failed-attempt threshold and lock length, with the installation's defaults filled in."""
        return (
            self.lockout_threshold or default_threshold,
            self.lockout_minutes or default_minutes,
        )

    def email_refused(self, email: str) -> str | None:
        """Why *email* may not sign up, or None when it may."""
        domain = email.rsplit("@", 1)[-1].strip().lower()
        blocked = {d.strip().lower().lstrip("@") for d in self.blocked_email_domains if d.strip()}
        allowed = {d.strip().lower().lstrip("@") for d in self.allowed_email_domains if d.strip()}
        if domain in blocked:
            return "sign-ups from that email domain are not allowed"
        if allowed and domain not in allowed:
            return "sign-ups are limited to specific email domains"
        return None

    def redirect_allowed(self, url: str | None) -> bool:
        """Whether *url* may receive tokens: the site URL or an allowlisted prefix."""
        if not url:
            return True
        allowed = [u for u in [self.site_url, *self.redirect_urls] if u]
        return any(
            url == base or url.startswith(base.rstrip("/") + "/") or url.startswith(base + "?")
            for base in allowed
        )

    def enabled_providers(self) -> list[str]:
        return sorted(
            name
            for name, conf in self.providers.items()
            if conf.get("enabled", True) and conf.get("client_id")
        )


async def load_config(akountz: Akountz, env: str) -> AuthConfig:
    key = f"authcfg:{env}"
    cached = await akountz.cache_get(key)
    if cached is not None:
        return AuthConfig(**cached)
    try:
        payload = await akountz.api.get(f"/internal/v1/environments/{env}/auth")
    except ServiceError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail=f"no environment {env!r}") from exc
        raise
    config = AuthConfig.from_api(env, payload)
    await akountz.cache.set(key, config.__dict__, ttl=akountz.settings.config_ttl)
    return config
