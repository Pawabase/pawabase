"""An environment's authentication settings, as Akountz sees them.

Project configuration belongs to the API: Akountz reads each environment's
``auth`` section from it (cached briefly with Sillo's cache) and applies
defaults. The reserved ``_platform`` environment, Studio's operators, is
configured here rather than in the API, because the API does not host it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sillo.exceptions import HTTPException

from pawabase_kit.clients import ServiceError
from pawabase_kit.settings import PLATFORM_ENV, PLATFORM_PROJECT

if TYPE_CHECKING:
    from app.platform import Akountz


@dataclass
class AuthConfig:
    """Authentication settings for one environment, with defaults applied."""

    project: str
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

    @classmethod
    def from_api(cls, project: str, env: str, payload: dict[str, Any]) -> AuthConfig:
        auth = payload.get("auth") or {}
        known = set(cls.__dataclass_fields__) - {"project", "env", "project_name", "public_url"}
        values = {key: auth[key] for key in known if key in auth and auth[key] is not None}
        return cls(
            project=project,
            env=env,
            project_name=payload.get("project_name", project),
            public_url=payload.get("public_url", ""),
            **values,
        )

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


def platform_config(akountz: Akountz) -> AuthConfig:
    return AuthConfig(
        project=PLATFORM_PROJECT,
        env=PLATFORM_ENV,
        project_name="Pawabase",
        signup_enabled=False,
        password_policy="strict",
        password_min_length=10,
        access_ttl=3600,
        refresh_ttl=7 * 24 * 3600,
        magic_link_enabled=False,
        public_url=akountz.settings.public_url,
    )


async def load_config(akountz: Akountz, project: str, env: str) -> AuthConfig:
    if (project, env) == (PLATFORM_PROJECT, PLATFORM_ENV):
        return platform_config(akountz)
    key = f"authcfg:{project}:{env}"
    cached = await akountz.cache_get(key)
    if cached is not None:
        return AuthConfig(**cached)
    try:
        payload = await akountz.api.get(f"/internal/v1/environments/{project}/{env}/auth")
    except ServiceError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail=f"no environment {project}/{env}") from exc
        raise
    config = AuthConfig.from_api(project, env, payload)
    await akountz.cache.set(key, config.__dict__, ttl=akountz.settings.config_ttl)
    return config
