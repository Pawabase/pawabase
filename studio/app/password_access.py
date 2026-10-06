"""Optional, self-hosted username/password protection for Studio.

This is intentionally independent of Pawabase Cloud's signed-link gate.  It
does not create an application user or call Akountz: it only protects the
operator UI of this one local runtime.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs

COOKIE = "pb_studio_local"
LOGIN = "/login"
LOGOUT = "/logout"
SESSION_SECONDS = 8 * 3600
OPEN_PATHS = ("/health",)
OPEN_PREFIXES = ("/internal/",)


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
                await self._login(receive, send)
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

    async def _login(self, receive, send) -> None:
        chunks: list[bytes] = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        fields = parse_qs(b"".join(chunks).decode("utf-8", "replace"), keep_blank_values=True)
        if self._matches(fields.get("username", [""])[0], fields.get("password", [""])[0]):
            await self._redirect(send, "/", cookie=self._session_cookie())
            return
        await self._html(send, 401, self._form(error="Invalid username or password."))

    def _cookie(self, value: str, *, clear: bool = False) -> bytes:
        parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
        parts.append("Max-Age=0" if clear else f"Max-Age={SESSION_SECONDS}")
        if self.secure:
            parts.append("Secure")
        return "; ".join(parts).encode()

    @staticmethod
    def _form(*, error: str = "") -> bytes:
        notice = f'<div class="alert" role="alert">{error}</div>' if error else ""
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sign in · Pawabase Studio</title><style>:root{{color-scheme:light dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f8faf9;color:#1c2522;font:15px/1.5 system-ui,sans-serif}}main{{width:min(100% - 32px,430px);padding:32px;border:1px solid #d9e2de;border-radius:16px;background:#fff}}.brand{{display:flex;align-items:center;gap:10px;font-size:18px;font-weight:800}}.mark{{width:32px;height:32px;color:#3ecf8e}}h1{{font-size:27px;letter-spacing:-.04em;margin:28px 0 4px}}.sub{{margin:0 0 22px;color:#64716b}}label{{display:block;margin:13px 0 6px;font-weight:650}}input,button{{width:100%;border-radius:9px;font:inherit}}input{{padding:11px 12px;border:1px solid #bfcac5;outline:none}}input:focus{{border-color:#3ecf8e;box-shadow:0 0 0 3px #d9f8e9}}button{{margin-top:20px;padding:12px;border:0;background:#3ecf8e;color:#103a2a;font-weight:750;cursor:pointer}}button:hover{{background:#2fbd7c}}.alert{{margin:14px 0;padding:10px 12px;border-radius:9px;background:#fee4e2;color:#b42318;font-size:13px}}@media(prefers-color-scheme:dark){{body{{background:#101513;color:#eaf2ee}}main{{background:#17201d;border-color:#304039}}input{{background:#101513;color:#eaf2ee;border-color:#46574f}}.sub{{color:#a8b8b0}}}}</style></head><body><main><div class="brand"><svg class="mark" viewBox="0 0 109 113" fill="none" aria-hidden="true"><path d="M63.7 110.7c-3.2 4-9.7 1.8-9.7-3.3V66.8h48.4c8.8 0 13.7 10.2 8.2 17.1L63.7 110.7Z" fill="currentColor"/><path d="M45.3 2.3c3.2-4 9.7-1.8 9.7 3.3v40.6H6.6c-8.8 0-13.7-10.2-8.2-17.1L45.3 2.3Z" fill="currentColor" opacity=".72"/></svg><span>Supabase</span><span style="font-weight:500;color:#718078">Studio</span></div><h1>Welcome back</h1><p class="sub">Sign in to manage this self-hosted runtime.</p>{notice}<form method="post" action="/login"><label for="username">Username</label><input id="username" name="username" autocomplete="username" required autofocus><label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required><button type="submit">Sign in to Studio</button></form></main></body></html>""".encode()

    async def _redirect(
        self, send, location: str, *, cookie: str = "", clear: bool = False
    ) -> None:
        headers = [(b"location", location.encode()), (b"cache-control", b"no-store")]
        if cookie or clear:
            headers.append((b"set-cookie", self._cookie(cookie, clear=clear)))
        await send({"type": "http.response.start", "status": 303, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    @staticmethod
    async def _html(send, status: int, body: bytes) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
