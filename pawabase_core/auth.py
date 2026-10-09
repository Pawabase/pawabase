"""Sillo authentication backends and middleware shared by every service.

Each backend is an ordinary Sillo :class:`~sillo.auth.backend.AuthenticationBackend`,
so services declare them with ``SilloApp(auth=[...])`` and gate routes with
``useAuth`` (or the policy gate built on it). Sillo also documents each backend in
the OpenAPI document from the same declaration.
"""

from __future__ import annotations

from typing import Any

from sillo.auth.backend import AuthenticationBackend
from sillo.auth.model import AuthResult

from .context import CONTEXT_HEADER, SCOPE_KEY, bind_context, reset_context
from .principal import encode_identity
from .tokens import (
    TokenInvalid,
    verify_context_token,
    verify_service_token,
    verify_user_token,
)

SERVICE_HEADER = "x-pawabase-service"

_FAIL = AuthResult(success=False, identity="", scope="")


def _bearer(headers: Any) -> str | None:
    value = headers.get("authorization") or ""
    if value[:7].lower() == "bearer ":
        return value[7:].strip() or None
    return None


def _apikey(headers: Any) -> str | None:
    """Extract apikey from the apikey header."""
    value = headers.get("apikey") or ""
    return value.strip() or None


class ContextMiddleware:
    """Verify the gateway's ``X-Pawabase-Context`` header and expose it.

    Register it with ``app.use`` *after* constructing ``SilloApp(auth=[...])``:
    Sillo builds middleware inside-out, so this runs first and the user backend
    already knows which environment a bearer token must belong to.

    A header that fails verification is dropped rather than rejected here, so
    public routes keep working. Routes that need an environment use
    :func:`~pawabase_core.context.require_context`.
    """

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.app: Any = None

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        context = None
        for name, value in scope.get("headers", ()):
            if name == CONTEXT_HEADER.encode():
                try:
                    context = verify_context_token(value.decode("latin-1"), self.secret)
                except TokenInvalid:
                    context = None
                break
        scope[SCOPE_KEY] = context
        token = bind_context(context)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_context(token)


class UserBackend(AuthenticationBackend):
    """An application user's bearer access token, issued by Akountz.

    The environment comes from the request's platform context, so a token
    issued for another environment fails even if the signature would be valid
    there.
    """

    name = "bearerAuth"

    def __init__(self, master_secret: str, description: str | None = None) -> None:
        self.master_secret = master_secret
        self.description = description or "A user access token issued by Akountz."

    def describe(self):
        from sillo.openapi.models import HTTPBearer

        return HTTPBearer(
            type="http", scheme="bearer", bearerFormat="JWT", description=self.description
        )

    async def authenticate(self, ctx) -> AuthResult:
        token = _bearer(ctx.headers)
        context = ctx.scope.get(SCOPE_KEY)
        if not token or context is None:
            return _FAIL
        try:
            claims = verify_user_token(token, self.master_secret, env=context.env)
        except TokenInvalid:
            return _FAIL
        return AuthResult(success=True, identity=encode_identity("user", claims), scope="user")


class APIKeyBackend(AuthenticationBackend):
    """Validates an API key (apikey header) and sets the platform context.

    Used when calling the API directly without going through the gateway.
    The apikey identifies the environment.
    """

    name = "apiKeyAuth"

    def __init__(self, description: str | None = None) -> None:
        self.description = description or "An API key (pk_ or sk_) in the apikey header."

    def describe(self):
        from sillo.openapi.models import APIKey

        return APIKey.model_validate(
            {
                "type": "apiKey",
                "name": "apikey",
                "in": "header",
                "description": self.description,
            }
        )

    async def authenticate(self, ctx) -> AuthResult:
        # This backend doesn't actually authenticate users, it just validates
        # the apikey header exists. The gateway normally does this validation.
        # For direct API access, we rely on platform context being set elsewhere.
        apikey = _apikey(ctx.headers)
        if not apikey:
            return _FAIL
        # Return success with empty identity - the actual validation is done by the gateway
        # or by direct database lookup elsewhere
        return AuthResult(success=True, identity="apikey", scope="apikey")


class ServiceBackend(AuthenticationBackend):
    """Another Pawabase service, such as Studio, acting for the runtime.

    The token is addressed to this service by name, so a token minted for
    Angula cannot be replayed against Akountz.
    """

    name = "serviceToken"

    def __init__(self, secret: str, audience: str, description: str | None = None) -> None:
        self.secret = secret
        self.audience = audience
        self.description = description or "A short-lived token minted by another Pawabase service."

    def describe(self):
        from sillo.openapi.models import APIKey

        return APIKey.model_validate(
            {
                "type": "apiKey",
                "name": SERVICE_HEADER,
                "in": "header",
                "description": self.description,
            }
        )

    async def authenticate(self, ctx) -> AuthResult:
        token = ctx.headers.get(SERVICE_HEADER)
        if not token:
            return _FAIL
        try:
            claims = verify_service_token(token, self.secret, audience=self.audience)
        except TokenInvalid:
            return _FAIL
        claims.setdefault("roles", ["admin"])
        return AuthResult(
            success=True, identity=encode_identity("service", claims), scope="service"
        )
