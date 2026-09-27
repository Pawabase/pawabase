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
from .settings import PLATFORM_ENV, PLATFORM_PROJECT
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


class ContextMiddleware:
    """Verify the gateway's ``X-Pawabase-Context`` header and expose it.

    Register it with ``app.use`` *after* constructing ``SilloApp(auth=[...])``:
    Sillo builds middleware inside-out, so this runs first and the user backend
    already knows which environment a bearer token must belong to.

    A header that fails verification is dropped rather than rejected here, so
    public routes keep working. Routes that need a project use
    :func:`~pawabase_kit.context.require_context`.
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


class ProjectUserBackend(AuthenticationBackend):
    """A project user's bearer access token, issued by Akountz.

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
            claims = verify_user_token(
                token, self.master_secret, project=context.project, env=context.env
            )
        except TokenInvalid:
            return _FAIL
        return AuthResult(success=True, identity=encode_identity("user", claims), scope="user")


class OperatorBackend(AuthenticationBackend):
    """A platform operator's bearer token: an Akountz user of ``_platform``.

    Used on the management plane when it is called directly (CLI, automation)
    rather than through Studio.
    """

    name = "operatorAuth"

    def __init__(self, master_secret: str, description: str | None = None) -> None:
        self.master_secret = master_secret
        self.description = description or "An operator access token for the _platform project."

    def describe(self):
        from sillo.openapi.models import HTTPBearer

        return HTTPBearer(
            type="http", scheme="bearer", bearerFormat="JWT", description=self.description
        )

    async def authenticate(self, ctx) -> AuthResult:
        token = _bearer(ctx.headers)
        if not token:
            return _FAIL
        try:
            claims = verify_user_token(
                token, self.master_secret, project=PLATFORM_PROJECT, env=PLATFORM_ENV
            )
        except TokenInvalid:
            return _FAIL
        return AuthResult(
            success=True, identity=encode_identity("operator", claims), scope="operator"
        )


class ServiceBackend(AuthenticationBackend):
    """Another Pawabase service, optionally acting for a Studio operator.

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
        kind = "operator" if claims.get("sub") else "service"
        if kind == "service":
            claims.setdefault("roles", ["admin"])
        return AuthResult(success=True, identity=encode_identity(kind, claims), scope=kind)
