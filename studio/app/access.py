"""Studio's access gate: only a signed link from the service that hosts this installation opens it.

Studio has no sign-in of its own: self-hosted it is published on localhost only. A hosting service that serves it publicly (Pawabase Cloud) sets
``PAWABASE_STUDIO_ACCESS_SECRET`` and Studio then refuses every request that does not carry a session cookie, and the only way to get one is
``/studio-access?token=…``: a link the hosting service signs (HS256, ``iss=pawabase-cloud``, ``aud=studio``) after it has checked who is asking.
A link lives a few minutes at most and works once. The session it starts lasts eight hours.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from http.cookies import SimpleCookie
from typing import Any

import jwt

COOKIE = "pb_studio"
ISSUER, AUDIENCE = "pawabase-cloud", "studio"
SESSION_SECONDS = 8 * 3600
MAX_LINK_SECONDS = 300
ENTRY = "/studio-access"
OPEN_PATHS = ("/health",)
OPEN_PREFIXES = ("/internal/",)  # service-to-service routes carry their own signed token


class StudioAccessGate:
    """ASGI middleware; register it with ``app.use`` after the other middleware so it runs first."""

    def __init__(self, secret: str, *, secure_cookie: bool = False) -> None:
        self.secret = secret
        self.secure = secure_cookie
        self.app: Any = None
        self._used: dict[str, float] = {}

    # ── the session cookie ───────────────────────────────────────────────

    def _sign(self, expires: int) -> str:
        return hmac.new(self.secret.encode(), f"studio-session:{expires}".encode(), hashlib.sha256).hexdigest()

    def session_cookie(self) -> str:
        expires = int(time.time()) + SESSION_SECONDS
        return f"{expires}.{self._sign(expires)}"

    def has_session(self, scope: dict[str, Any]) -> bool:
        raw = dict(scope.get("headers", ())).get(b"cookie", b"").decode("latin-1")
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 - a broken Cookie header is just no session
            return False
        morsel = jar.get(COOKIE)
        if morsel is None:
            return False
        expires, _, signature = morsel.value.partition(".")
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(signature, self._sign(int(expires)))

    # ── the link ─────────────────────────────────────────────────────────

    def accept_link(self, token: str) -> bool:
        try:
            claims = jwt.decode(token, self.secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER, options={"require": ["exp", "jti"]})
        except jwt.PyJWTError:
            return False
        now = time.time()
        if claims["exp"] - now > MAX_LINK_SECONDS:  # a link that lives for days is not a link
            return False
        self._used = {jti: exp for jti, exp in self._used.items() if exp > now}
        if claims["jti"] in self._used:
            return False
        self._used[claims["jti"]] = claims["exp"]
        return True

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
            query = dict(item.split("=", 1) for item in scope.get("query_string", b"").decode().split("&") if "=" in item)
            if self.accept_link(query.get("token", "")):
                cookie = f"{COOKIE}={self.session_cookie()}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}" + ("; Secure" if self.secure else "")
                await self._respond(send, 303, b"", [(b"location", b"/"), (b"set-cookie", cookie.encode()), (b"cache-control", b"no-store")])
            else:
                await self._respond(send, 403, b'{"error":"invalid_link","message":"This link is invalid or has expired. Open Studio again from your dashboard."}')
            return
        if self.has_session(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await self._respond(send, 401, b'{"error":"studio_access_required","message":"Open Studio from your dashboard."}')

    @staticmethod
    async def _respond(send, status: int, body: bytes, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        base = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]
        await send({"type": "http.response.start", "status": status, "headers": [*base, *(headers or [])]})
        await send({"type": "http.response.body", "body": body})
