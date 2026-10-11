"""Optional, self-hosted username/password protection for Studio.

This is intentionally independent of Pawabase Cloud's signed-link gate.  It
does not create an application user or call Accounts: it only protects the
operator UI of this one local runtime.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from studio import login_page

COOKIE = "pb_studio_local"
LOGIN = "/login"
LOGOUT = "/logout"
SESSION_SECONDS = 8 * 3600
OPEN_PATHS = ("/health",)
OPEN_PREFIXES = ("/internal/",)


def _pawabase_logo() -> str:
    """Reuse Studio's canonical Pawabase mark in the pre-frontend login page."""
    template = Path(__file__).parent / "resources" / "views" / "app.html"
    match = re.search(
        r'href="(data:image/svg\+xml;base64,[^"]+)"', template.read_text(encoding="utf-8")
    )
    return match.group(1) if match else ""


PAWABASE_LOGO = _pawabase_logo()

#: Failed sign-ins one address may make within the window before further attempts are refused until it passes.
MAX_FAILURES = 5
FAILURE_WINDOW = 600
MAX_TRACKED = 2000
MAX_FORM_BYTES = 8 * 1024


class FailureLog:
    """Counts failed sign-ins per client address, in a table that cannot grow without bound.

    Per process and in memory on purpose: it only has to slow down someone guessing at a single self-hosted installation, and a
    restart forgetting the count is no loss.
    """

    def __init__(
        self, *, limit: int = MAX_FAILURES, window: float = FAILURE_WINDOW, clock=time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window
        self._clock = clock
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = self._clock() - self.window
        recent = [at for at in self._failures.get(key, ()) if at > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked_for(self, key: str) -> int:
        """Seconds until *key* may try again; 0 when it may try now."""
        recent = self._recent(key)
        if len(recent) < self.limit:
            return 0
        return max(1, int(recent[0] + self.window - self._clock()) + 1)

    def record(self, key: str) -> None:
        if key not in self._failures and len(self._failures) >= MAX_TRACKED:
            del self._failures[next(iter(self._failures))]
        self._failures.setdefault(key, []).append(self._clock())

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


class StudioPasswordGate:
    """ASGI login gate for a self-hosted Studio.

    Passwords remain only in the deployment environment.  The browser receives
    an HttpOnly, signed expiry value; rotating either credential invalidates all
    existing sessions.
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        session_secret: str,
        secure_cookie: bool = False,
    ) -> None:
        self.username = username
        self.password = password
        self.secure = secure_cookie
        self.failures = FailureLog()
        self._secret = hmac.new(
            session_secret.encode(),
            f"studio-local:{username}:{password}".encode(),
            hashlib.sha256,
        ).digest()
        self.app: Any = None

    def _sign(self, expires: int) -> str:
        return hmac.new(
            self._secret, f"studio-session:{expires}".encode(), hashlib.sha256
        ).hexdigest()

    def _session_cookie(self) -> str:
        expires = int(time.time()) + SESSION_SECONDS
        return f"{expires}.{self._sign(expires)}"

    def _has_session(self, scope: dict[str, Any]) -> bool:
        raw = dict(scope.get("headers", ())).get(b"cookie", b"").decode("latin-1")
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 - malformed cookies are unauthenticated
            return False
        morsel = jar.get(COOKIE)
        if morsel is None:
            return False
        expires, _, signature = morsel.value.partition(".")
        return (
            expires.isdigit()
            and int(expires) >= time.time()
            and hmac.compare_digest(signature, self._sign(int(expires)))
        )

    def _matches(self, username: str, password: str) -> bool:
        return hmac.compare_digest(username, self.username) and hmac.compare_digest(
            password, self.password
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and path == LOGIN:
            if scope.get("method") == "POST":
                await self._login(scope, receive, send)
            else:
                await self._html(send, 200, self._form())
            return
        if scope["type"] == "http" and path == LOGOUT:
            await self._redirect(send, LOGIN, clear=True)
            return
        if self._has_session(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        await self._redirect(send, LOGIN)

    async def _login(self, scope, receive, send) -> None:
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            body = message.get("body", b"")
            total += len(body)
            if total > MAX_FORM_BYTES:
                await self._html(send, 413, self._form(error="That request was too large."))
                return
            chunks.append(body)
            if not message.get("more_body"):
                break
        fields = parse_qs(b"".join(chunks).decode("utf-8", "replace"), keep_blank_values=True)
        username = fields.get("username", [""])[0]
        client = scope.get("client")
        address = client[0] if client else "unknown"
        wait = self.failures.blocked_for(address)
        if wait:
            await self._html(
                send,
                429,
                self._form(
                    error="Too many failed sign-in attempts.", username=username, retry_after=wait
                ),
                extra=[(b"retry-after", str(wait).encode())],
            )
            return
        if self._matches(username, fields.get("password", [""])[0]):
            self.failures.clear(address)
            await self._redirect(send, "/", cookie=self._session_cookie())
            return
        self.failures.record(address)
        await self._html(
            send, 401, self._form(error="Invalid username or password.", username=username)
        )

    def _cookie(self, value: str, *, clear: bool = False) -> bytes:
        parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
        parts.append("Max-Age=0" if clear else f"Max-Age={SESSION_SECONDS}")
        if self.secure:
            parts.append("Secure")
        return "; ".join(parts).encode()

    @staticmethod
    def _form(*, error: str = "", username: str = "", retry_after: int = 0) -> bytes:
        return login_page.render(
            logo=PAWABASE_LOGO, error=error, username=username, retry_after=retry_after
        )

    async def _redirect(
        self, send, location: str, *, cookie: str = "", clear: bool = False
    ) -> None:
        headers = [(b"location", location.encode()), (b"cache-control", b"no-store")]
        if cookie or clear:
            headers.append((b"set-cookie", self._cookie(cookie, clear=clear)))
        await send({"type": "http.response.start", "status": 303, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _html(
        send, status: int, body: bytes, extra: list[tuple[bytes, bytes]] | None = None
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"same-origin"),
                    *(extra or []),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
