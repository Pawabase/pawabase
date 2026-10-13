"""Studio's access gate: only a signed link from the service that hosts this installation opens it.

Studio has no sign-in of its own: self-hosted it is published on localhost only. A hosting service that serves it publicly (Pawabase Cloud) sets
``PAWABASE_STUDIO_ACCESS_SECRET`` and Studio then refuses every request that does not carry a session cookie, and the only way to get one is
``/studio-access?token=…``: a link the hosting service signs (HS256, ``iss=pawabase-cloud``, ``aud=studio``) after it has checked who is asking.
A link lives a few minutes at most and works once. The session it starts lasts eight hours.

A link may carry a ``project`` claim. The session it starts is then confined to that project's environments (see :mod:`studio.scope`): this is
how a shared runtime lets one customer into Studio without letting them see anyone else's.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from collections.abc import Awaitable, Callable
from http.cookies import SimpleCookie
from typing import Any

import jwt

logger = logging.getLogger("pawabase.studio.access")

COOKIE = "pb_studio"
ISSUER, AUDIENCE = "pawabase-cloud", "studio"
SESSION_SECONDS = 8 * 3600
MAX_LINK_SECONDS = 300
ENTRY = "/studio-access"
PROJECT = re.compile(r"[A-Za-z0-9_-]{8,64}")
OPEN_PATHS = ("/health",)
OPEN_PREFIXES = ("/internal/",)  # service-to-service routes carry their own signed token


class StudioAccessGate:
    """ASGI middleware; register it with ``app.use`` after the other middleware so it runs first."""

    def __init__(
        self,
        secret: str,
        *,
        secure_cookie: bool = False,
        guard: Callable[[dict[str, Any], str], Awaitable[bool]] | None = None,
    ) -> None:
        """*guard* is asked about every request of a session that is confined to a project; ``False`` is a 403."""
        self.secret = secret
        self.secure = secure_cookie
        self.guard = guard
        self.app: Any = None
        self._used: dict[str, float] = {}

    # ── the session cookie ───────────────────────────────────────────────

    def _sign(self, expires: int, project: str = "") -> str:
        message = f"studio-session:{expires}" + (f":{project}" if project else "")
        return hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()

    def session_cookie(self, project: str = "") -> str:
        expires = int(time.time()) + SESSION_SECONDS
        middle = f"{project}." if project else ""
        return f"{expires}.{middle}{self._sign(expires, project)}"

    def session_of(self, scope: dict[str, Any]) -> str | None:
        """The project a valid session is confined to (``""`` for none), or ``None`` when there is no valid session."""
        raw = dict(scope.get("headers", ())).get(b"cookie", b"").decode("latin-1")
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 - a broken Cookie header is just no session
            return None
        morsel = jar.get(COOKIE)
        if morsel is None:
            return None
        parts = morsel.value.split(".")
        if len(parts) not in (2, 3) or not parts[0].isdigit() or int(parts[0]) < time.time():
            return None
        project = parts[1] if len(parts) == 3 else ""
        if not hmac.compare_digest(parts[-1], self._sign(int(parts[0]), project)):
            return None
        return project

    def has_session(self, scope: dict[str, Any]) -> bool:
        return self.session_of(scope) is not None

    # ── the link ─────────────────────────────────────────────────────────

    def accept_link(self, token: str) -> bool:
        return self.link_project(token) is not None

    def link_project(self, token: str) -> str | None:
        """The project a valid link is for (``""`` for a link that is for none), or ``None`` when the link is not valid; spends the link."""
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": ["exp", "jti"]},
            )
        except jwt.PyJWTError:
            return None
        now = time.time()
        if claims["exp"] - now > MAX_LINK_SECONDS:  # a link that lives for days is not a link
            return None
        self._used = {jti: exp for jti, exp in self._used.items() if exp > now}
        if claims["jti"] in self._used:
            return None
        project = str(claims.get("project") or "")
        if project and not PROJECT.fullmatch(project):
            return None
        self._used[claims["jti"]] = claims["exp"]
        return project

    # ── the middleware ───────────────────────────────────────────────────

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            await self.app(scope, receive, send)
            return
        if path == ENTRY and scope["type"] == "http":
            query = dict(
                item.split("=", 1)
                for item in scope.get("query_string", b"").decode().split("&")
                if "=" in item
            )
            project = self.link_project(query.get("token", ""))
            if project is not None:
                cookie = (
                    f"{COOKIE}={self.session_cookie(project)}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}"
                    + ("; Secure" if self.secure else "")
                )
                await self._respond(
                    send,
                    303,
                    b"",
                    [
                        (b"location", b"/"),
                        (b"set-cookie", cookie.encode()),
                        (b"cache-control", b"no-store"),
                    ],
                )
            else:
                await self._respond(
                    send,
                    403,
                    b'{"error":"invalid_link","message":"This link is invalid or has expired. Open Studio again from your dashboard."}',
                )
            return
        project = self.session_of(scope)
        if project is not None:
            if project and self.guard is not None:
                allowed = await self.guard(scope, project)
                if not allowed:
                    logger.warning("project-scoped Studio request refused: project=%s path=%s", project, path)
                    if scope["type"] == "websocket":
                        await send({"type": "websocket.close", "code": 4403})
                    else:
                        await self._respond(
                            send,
                            403,
                            b'{"error":"outside_project","message":"This part of Studio is not available for your project."}',
                        )
                    return
            elif project:  # a session confined to a project, and nothing to confine it with: refuse rather than open everything
                await self._respond(send, 403, b'{"error":"outside_project"}')
                return
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await self._respond(
            send,
            401,
            b'{"error":"studio_access_required","message":"Open Studio from your dashboard."}',
        )

    @staticmethod
    async def _respond(
        send, status: int, body: bytes, headers: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        base = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-store"),
        ]
        await send(
            {"type": "http.response.start", "status": status, "headers": [*base, *(headers or [])]}
        )
        await send({"type": "http.response.body", "body": body})
